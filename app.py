import os
import json
import time
import subprocess
import threading
import logging
import gi

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango

# Configuração central de logging para monitorar as execuções do subprocess via terminal
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("FlatStore")

CACHE_DIR = os.path.expanduser("~/.cache/flatstore")
CACHE_FILE = os.path.join(CACHE_DIR, "cache.json")
CONFIG_FILE = os.path.join(CACHE_DIR, "config.json")
DEFAULT_CACHE_TTL = 3600  
CMD_TIMEOUT = 45  

# O CSS ajusta a interface para um visual mais limpo e unificado, removendo bordas arredondadas padrão de alguns temas
NATIVE_FLAT_CSS = """
button, frame, entry, window, box, flowboxchild, headerbar, listbox {
    border-radius: 0px;
}
.app-card {
    border: 1px solid mix(@theme_fg_color, @theme_bg_color, 0.82);
    background-color: @theme_bg_color;
    padding: 2px;
}
.repo-row {
    padding: 8px;
    border-bottom: 1px solid mix(@theme_fg_color, @theme_bg_color, 0.9);
}
.repo-label {
    font-size: 10px;
    background: mix(@theme_fg_color, @theme_bg_color, 0.9);
    padding: 2px 6px;
    border-radius: 4px;
}
"""

class FlatpakStoreApp(Gtk.Window):
    def __init__(self):
        super().__init__(title="Gerenciador Flatpak")
        self.set_default_size(920, 660)
        self.set_border_width(12)

        icon_name = "system-software-install"
        Gtk.Window.set_default_icon_name(icon_name)
        self.set_icon_name(icon_name)

        # Estado geral da aplicação, incluindo bloqueio (Lock) para acesso seguro ao cache via threads
        self.cache_ttl = DEFAULT_CACHE_TTL
        self.clear_on_exit = False
        self.cache = {}
        self.cache_lock = threading.Lock()
        
        self.installed_apps = set()
        self.updates_available = []
        self.repositories = []
        
        # Dicionário responsável por desativar botões de apps que já estão sofrendo alguma ação
        self.active_operations = {}
        self.current_results_data = []

        logger.info("Iniciando FlatStore...")
        self.load_config()
        self.apply_native_css()
        self.load_cache_from_disk()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(main_box)

        # Configuração da HeaderBar (barra de título moderna do GTK)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("FlatStore")
        header.set_subtitle("Gerenciador Nativo para XFCE")
        self.set_titlebar(header)

        header.pack_end(self.create_settings_menu())
        
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)

        btn_refresh = Gtk.Button(label="Sincronizar")
        btn_refresh.set_tooltip_text("Sincroniza AppStream, atualiza repositórios e limpa o cache local")
        btn_refresh.connect("clicked", self.on_refresh_clicked)
        header.pack_start(btn_refresh)

        # Configuração do container de pesquisa
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar aplicativos no Flathub...")
        self.search_entry.connect("activate", self.on_search)

        btn_search = Gtk.Button(label="Buscar")
        btn_search.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
        btn_search.connect("clicked", self.on_search)

        search_box.pack_start(self.search_entry, True, True, 0)
        search_box.pack_start(btn_search, False, False, 0)
        main_box.pack_start(search_box, False, False, 0)

        self.status_label = Gtk.Label(label="Iniciando...")
        self.status_label.set_xalign(0)
        self.status_label.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        main_box.pack_start(self.status_label, False, False, 0)

        # Sistema de abas principal
        self.notebook = Gtk.Notebook()
        main_box.pack_start(self.notebook, True, True, 0)

        # Inicialização da Aba 1: Busca e Destaques
        self.flow_results = self.create_flowbox()
        self.notebook.append_page(self.wrap_in_scroll(self.flow_results), Gtk.Label(label="✨ Início"))

        # Inicialização da Aba 2: Aplicativos Instalados
        self.flow_installed = self.create_flowbox()
        self.notebook.append_page(self.wrap_in_scroll(self.flow_installed), Gtk.Label(label="📦 Instalados"))

        # Inicialização da Aba 3: Atualizações
        self.updates_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        upd_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        upd_header_box.set_margin_start(6); upd_header_box.set_margin_end(6)
        upd_header_box.set_margin_top(8); upd_header_box.set_margin_bottom(4)

        self.lbl_updates_count = Gtk.Label(label="Verificando atualizações...")
        self.lbl_updates_count.set_xalign(0)
        
        self.btn_update_all = Gtk.Button(label="Atualizar Tudo")
        self.btn_update_all.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
        self.btn_update_all.connect("clicked", self.on_update_all_clicked)

        upd_header_box.pack_start(self.lbl_updates_count, True, True, 0)
        upd_header_box.pack_end(self.btn_update_all, False, False, 0)
        self.updates_box.pack_start(upd_header_box, False, False, 0)

        self.flow_updates = self.create_flowbox()
        self.updates_box.pack_start(self.wrap_in_scroll(self.flow_updates), True, True, 0)
        self.tab_updates_label = Gtk.Label(label="🔄 Atualizações")
        self.notebook.append_page(self.updates_box, self.tab_updates_label)

        # Inicialização da Aba 4: Gestão de Repositórios (Remotes)
        self.repos_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        repo_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        repo_header.set_margin_start(6); repo_header.set_margin_end(6)
        repo_header.set_margin_top(8); repo_header.set_margin_bottom(4)
        
        lbl_repo = Gtk.Label(label="Gerencie suas fontes de aplicativos (Remotes):", xalign=0)
        btn_add_repo = Gtk.Button(label="Adicionar Repositório")
        btn_add_repo.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
        btn_add_repo.connect("clicked", self.on_add_repo_clicked)
        
        repo_header.pack_start(lbl_repo, True, True, 0)
        repo_header.pack_end(btn_add_repo, False, False, 0)
        self.repos_box.pack_start(repo_header, False, False, 0)

        self.listbox_repos = Gtk.ListBox()
        self.listbox_repos.set_selection_mode(Gtk.SelectionMode.NONE)
        self.repos_box.pack_start(self.wrap_in_scroll(self.listbox_repos), True, True, 0)
        self.notebook.append_page(self.repos_box, Gtk.Label(label="🌐 Repositórios"))

        # Carrega os dados assincronamente assim que a interface estiver pronta
        self.init_data()

    def _run_flatpak_cmd(self, args, check=False):
        """
        Executa comandos de leitura do Flatpak (ex: list, search) com timeout de segurança.
        Retorna a saída padrão e eventuais erros processados pelo subprocess.
        """
        cmd = ['flatpak'] + args
        logger.debug(f"Executando (Leitura): {' '.join(cmd)}")
        try:
            return subprocess.run(
                cmd, capture_output=True, text=True, check=check, timeout=CMD_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Comando excedeu o tempo limite: {' '.join(cmd)}")
            raise
        except Exception as e:
            logger.error(f"Falha ao executar flatpak: {e}")
            raise

    def _run_flatpak_action(self, cmd):
        """
        Executa comandos de escrita do Flatpak (ex: install, update, uninstall).
        Utiliza Popen para iterar sobre o buffer e imprimir o progresso no terminal em tempo real.
        """
        logger.info(f">>> INICIANDO AÇÃO: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            for line in iter(process.stdout.readline, ''):
                if line.strip():
                    logger.info(f"[Flatpak] {line.strip()}")
                    
            process.stdout.close()
            return_code = process.wait()
            
            logger.info(f">>> AÇÃO FINALIZADA COM CÓDIGO: {return_code}")
            
            if return_code != 0:
                raise Exception(f"Comando falhou com código de saída {return_code}")
        except Exception as e:
            logger.error(f"Erro Crítico durante a ação: {e}")
            raise

    def run_async(self, func, *args):
        """Dispara funções em threads separadas (daemon=True) para evitar congelamento da UI."""
        threading.Thread(target=func, args=args, daemon=True).start()

    def load_config(self):
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    self.cache_ttl = cfg.get("cache_ttl", DEFAULT_CACHE_TTL)
                    self.clear_on_exit = cfg.get("clear_on_exit", False)
        except Exception:
            self.cache_ttl = DEFAULT_CACHE_TTL

    def save_config(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"cache_ttl": self.cache_ttl, "clear_on_exit": self.clear_on_exit}, f)
        except Exception:
            pass

    def load_cache_from_disk(self):
        with self.cache_lock:
            try:
                if os.path.exists(CACHE_FILE):
                    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        now = time.time()
                        self.cache = {k: v for k, v in data.items() if now - v.get('timestamp', 0) < self.cache_ttl}
            except Exception:
                self.cache = {}

    def save_cache_to_disk(self):
        with self.cache_lock:
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False)
            except Exception:
                pass

    def clear_disk_cache(self):
        with self.cache_lock:
            self.cache.clear()
            if os.path.exists(CACHE_FILE):
                try: os.remove(CACHE_FILE)
                except: pass
        logger.info("Cache local limpo.")

    def get_from_cache(self, key):
        """Busca dados armazenados localmente apenas se não tiverem excedido o TTL."""
        with self.cache_lock:
            if key in self.cache:
                item = self.cache[key]
                if time.time() - item['timestamp'] < self.cache_ttl:
                    return item['data']
                else:
                    del self.cache[key]
        return None

    def save_to_cache(self, key, data):
        with self.cache_lock:
            self.cache[key] = {'timestamp': time.time(), 'data': data}
        self.save_cache_to_disk()

    def create_settings_menu(self):
        """Gera o menu dropdown (Popover) de configurações disponível na barra superior."""
        btn = Gtk.MenuButton()
        btn.set_image(Gtk.Image.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.BUTTON))
        
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)

        box.pack_start(Gtk.Label(label="<b>Configurações</b>", use_markup=True, xalign=0), False, False, 0)
        box.pack_start(Gtk.Label(label="Validade do Cache:", xalign=0), False, False, 0)

        combo = Gtk.ComboBoxText()
        for t_id, t_lbl in [("1800","30 Min"), ("3600","1 Hora"), ("21600","6 Horas"), ("86400","24 Horas")]:
            combo.append(t_id, t_lbl)
        if not combo.set_active_id(str(self.cache_ttl)): combo.set_active(1)
        combo.connect("changed", self.on_ttl_changed)
        box.pack_start(combo, False, False, 0)

        chk_clear = Gtk.CheckButton(label="Limpar cache ao sair")
        chk_clear.set_active(self.clear_on_exit)
        chk_clear.connect("toggled", self.on_clear_on_exit_toggled)
        box.pack_start(chk_clear, False, False, 0)

        btn_clear = Gtk.Button(label="🗑️ Limpar Cache Agora")
        btn_clear.connect("clicked", lambda w: (self.clear_disk_cache(), self.stop_loading("Cache limpo.")))
        box.pack_start(btn_clear, False, False, 0)

        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        btn_about = Gtk.Button(label="ℹ️ Sobre o FlatStore")
        btn_about.connect("clicked", self.show_about_dialog)
        box.pack_start(btn_about, False, False, 0)

        box.show_all()
        popover.add(box)
        btn.set_popover(popover)
        return btn

    def show_about_dialog(self, widget):
        about = Gtk.AboutDialog(transient_for=self, modal=True)
        about.set_program_name("FlatStore")
        about.set_version("1.1")
        about.set_logo_icon_name("system-software-install")
        about.set_comments("Gerenciador Flatpak rápido e nativo para ambientes GTK/XFCE.\n\nGitHub: github.com/ogoncas")
        about.set_website("https://mateuscalixto.com.br")
        about.set_website_label("mateuscalixto.com.br")
        about.set_authors(["Mateus Calixto"])
        about.set_license("Licença Livre 100%\nEste software é totalmente livre para uso, distribuição e modificação.")
        about.connect("response", lambda dialog, response: dialog.destroy())
        about.show_all()

    def on_ttl_changed(self, combo):
        if combo.get_active_id():
            self.cache_ttl = int(combo.get_active_id())
            self.save_config()

    def on_clear_on_exit_toggled(self, button):
        self.clear_on_exit = button.get_active()
        self.save_config()

    def apply_native_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(NATIVE_FLAT_CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def create_flowbox(self):
        flow = Gtk.FlowBox()
        flow.set_valign(Gtk.Align.START)
        flow.set_selection_mode(Gtk.SelectionMode.NONE)
        flow.set_row_spacing(12); flow.set_column_spacing(12)
        return flow

    def wrap_in_scroll(self, widget):
        scroll = Gtk.ScrolledWindow()
        scroll.set_border_width(4)
        scroll.add(widget)
        return scroll

    def start_loading(self, msg):
        self.status_label.set_text(msg)
        self.spinner.start()

    def stop_loading(self, msg=""):
        self.spinner.stop()
        if msg: self.status_label.set_text(msg)
        
    def show_error_dialog(self, title, message):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def _render_all_views(self):
        self._render_results()
        self._render_installed()
        self._render_updates()

    def get_app_icon(self, app_id, is_installed, is_update):
        """Tenta resgatar o ícone nativo do tema do sistema; aplica fallbacks caso o ícone não seja encontrado."""
        theme = Gtk.IconTheme.get_default()
        try:
            pixbuf = theme.load_icon(app_id, 48, 0)
            return Gtk.Image.new_from_pixbuf(pixbuf)
        except GLib.Error:
            icon_name = "software-update-available" if is_update else ("package-x-generic" if is_installed else "system-run")
            return Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)

    def create_app_card(self, name, app_id, repo, is_installed=False, is_update=False):
        """Constrói o card de aplicativo (UI) de forma isolada, definindo seu estado (Instalar, Remover, Atualizar)."""
        frame = Gtk.Frame()
        frame.get_style_context().add_class("app-card")
        frame.set_tooltip_text(f"{name}\n({app_id})") 
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        app_image = self.get_app_icon(app_id, is_installed, is_update)
        top.pack_start(app_image, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        lbl_name = Gtk.Label(label=f"<b>{name}</b>", use_markup=True, xalign=0)
        lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_name.set_max_width_chars(25) 
        
        lbl_id = Gtk.Label(label=app_id, xalign=0)
        lbl_id.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        lbl_id.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_id.set_max_width_chars(25)
        
        lbl_repo = Gtk.Label(label=f"📦 {repo}", xalign=0)
        lbl_repo.get_style_context().add_class("repo-label")
        
        meta.pack_start(lbl_name, False, False, 0)
        meta.pack_start(lbl_id, False, False, 0)
        meta.pack_start(lbl_repo, False, False, 0)
        top.pack_start(meta, True, True, 0)
        box.pack_start(top, True, True, 0)

        active_action = self.active_operations.get(app_id)

        if active_action:
            lbl_map = {
                "install": "Instalando...",
                "uninstall": "Removendo...",
                "update": "Atualizando..."
            }
            btn_label = lbl_map.get(active_action, "Processando...")
            btn = Gtk.Button(label=btn_label)
            btn.set_sensitive(False)
        elif is_update:
            btn = Gtk.Button(label="Atualizar")
            btn.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
            btn.connect("clicked", lambda w: self.on_action_click(app_id, "update", repo))
        elif is_installed:
            btn = Gtk.Button(label="Remover")
            btn.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)
            btn.connect("clicked", lambda w: self.on_action_click(app_id, "uninstall", repo))
        else:
            btn = Gtk.Button(label="Instalar")
            btn.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
            btn.connect("clicked", lambda w: self.on_action_click(app_id, "install", repo))
            
        box.pack_start(btn, False, False, 0)
        frame.add(box)
        frame.show_all()
        return frame

    def create_empty_state_label(self, message):
        lbl = Gtk.Label(label=f"<i>{message}</i>", use_markup=True, margin_top=20)
        lbl.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        lbl.set_halign(Gtk.Align.CENTER)
        return lbl

    def init_data(self):
        GLib.idle_add(self.start_loading, "Sincronizando ambiente local...")
        self.run_async(self._sync_all)

    def _sync_all(self):
        """Coordena a atualização de dados de todas as abas rodando em background."""
        logger.info("Iniciando sincronização geral das abas...")
        try:
            self._update_installed_set()
            self._fetch_updates()
            self._fetch_repos()
            
            cache_key = "__featured__"
            featured = self.get_from_cache(cache_key)
            if not featured:
                featured = self._fetch_featured_from_cli()
                self.save_to_cache(cache_key, featured)

            GLib.idle_add(self._render_results, featured)
            GLib.idle_add(self._render_installed)
            GLib.idle_add(self._render_updates)
            GLib.idle_add(self._render_repos)
            GLib.idle_add(self.stop_loading, "Pronto.")
        except Exception as e:
            logger.error(f"Erro ao sincronizar dados: {e}")
            GLib.idle_add(self.stop_loading, f"Erro ao sincronizar: {e}")

    def _update_installed_set(self):
        res = self._run_flatpak_cmd(['list', '--app', '--columns=application'])
        if res.returncode == 0:
            self.installed_apps = {a.strip() for a in res.stdout.strip().split('\n') if a.strip()}

    def _fetch_featured_from_cli(self):
        """Monta uma lista inicial de aplicativos recomendados para a página de início através de buscas silenciosas."""
        results = []
        for kw in ['browser', 'office', 'editor']:
            try:
                res = self._run_flatpak_cmd(['search', kw, '--columns=name,application,remotes'])
                if res.returncode == 0:
                    for line in res.stdout.strip().split('\n')[:2]:
                        parts = [p.strip() for p in line.split('\t')]
                        if len(parts) >= 3:
                            n, i, r = parts[0], parts[1], parts[2].split(',')[0].strip()
                            if (n, i, r) not in results:
                                results.append((n, i, r))
                        elif len(parts) == 2:
                            n, i, r = parts[0], parts[1], "Desconhecido"
                            if (n, i, r) not in results:
                                results.append((n, i, r))
            except Exception:
                continue 
        return results

    def _fetch_updates(self):
        res = self._run_flatpak_cmd(['list', '--updates', '--columns=name,application,origin'])
        if res.returncode == 0:
            self.updates_available = []
            for l in res.stdout.strip().split('\n'):
                if '\t' in l and "Name" not in l:
                    parts = [p.strip() for p in l.split('\t')]
                    if len(parts) >= 3:
                        self.updates_available.append((parts[0], parts[1], parts[2]))
                    elif len(parts) == 2:
                        self.updates_available.append((parts[0], parts[1], "Desconhecido"))

    def _render_updates(self):
        [child.destroy() for child in self.flow_updates.get_children()]
        count = len(self.updates_available)
        
        self.lbl_updates_count.set_text(f"<b>{count} atualização(ões)</b>" if count > 0 else "Sistema atualizado.")
        self.lbl_updates_count.set_use_markup(True)
        self.tab_updates_label.set_text(f"🔄 Atualizações ({count})" if count > 0 else "🔄 Atualizações")
        self.btn_update_all.set_sensitive(count > 0)

        for item in self.updates_available:
            if len(item) >= 3:
                name, app_id, repo = item[0], item[1], item[2]
            elif len(item) == 2:
                name, app_id, repo = item[0], item[1], "Desconhecido"
            else:
                continue
            self.flow_updates.add(self.create_app_card(name, app_id, repo, True, True))
        
        if count == 0:
            self.flow_updates.add(self.create_empty_state_label("Nenhuma atualização pendente."))
        self.flow_updates.show_all()

    def on_update_all_clicked(self, widget):
        self.start_loading("Atualizando todo o sistema... Acompanhe no terminal.")
        self.run_async(self._execute_update_all)

    def _execute_update_all(self):
        logger.info("Executando atualização global...")
        try:
            self._run_flatpak_action(['flatpak', 'update', '--user', '-y', '--noninteractive'])
            self.clear_disk_cache()
            self._sync_all()
            GLib.idle_add(self.stop_loading, "Todas as atualizações concluídas com sucesso!")
        except Exception as e:
            GLib.idle_add(self.stop_loading, f"Falha na atualização: {e}")

    def _fetch_repos(self):
        res = self._run_flatpak_cmd(['remotes', '--columns=name,url'])
        if res.returncode == 0:
            self.repositories = [tuple(p.strip() for p in l.split('\t')) for l in res.stdout.strip().split('\n') if '\t' in l]

    def _render_repos(self):
        [child.destroy() for child in self.listbox_repos.get_children()]
        
        if not self.repositories:
            self.listbox_repos.add(self.create_empty_state_label("Nenhum repositório configurado."))
        
        for name, url in self.repositories:
            row = Gtk.ListBoxRow()
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            box.get_style_context().add_class("repo-row")
            
            info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            info_box.pack_start(Gtk.Label(label=f"<b>{name}</b>", use_markup=True, xalign=0), False, False, 0)
            
            lbl_url = Gtk.Label(label=url, xalign=0)
            lbl_url.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
            info_box.pack_start(lbl_url, False, False, 0)
            
            btn_remove = Gtk.Button(label="Remover")
            btn_remove.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)
            btn_remove.set_valign(Gtk.Align.CENTER)
            btn_remove.connect("clicked", lambda w, n=name: self.on_remove_repo(n))
            
            box.pack_start(info_box, True, True, 0)
            box.pack_end(btn_remove, False, False, 0)
            row.add(box)
            self.listbox_repos.add(row)
            
        self.listbox_repos.show_all()

    def on_add_repo_clicked(self, widget):
        """Constrói o dialog para inclusão de um novo remote flatpak de maneira gráfica."""
        dialog = Gtk.Dialog(title="Adicionar Repositório", transient_for=self, flags=0)
        dialog.add_buttons("Cancelar", Gtk.ResponseType.CANCEL, "Adicionar", Gtk.ResponseType.OK)
        dialog.set_default_size(400, 220)
        dialog.set_border_width(10)
        
        box = dialog.get_content_area()
        box.set_spacing(8)
        
        entry_name = Gtk.Entry(placeholder_text="Nome (ex: flathub)")
        entry_url = Gtk.Entry(placeholder_text="URL (ex: https://flathub.org/repo/flathub.flatpakrepo)")
        
        combo_scope = Gtk.ComboBoxText()
        combo_scope.append("user", "Meu Usuário (Não requer senha)")
        combo_scope.append("system", "Todo o Sistema (Requer Admin)")
        combo_scope.append("both", "Ambos (Usuário e Sistema)")
        combo_scope.set_active_id("user")
        
        box.pack_start(Gtk.Label(label="Nome do Repositório:", xalign=0), False, False, 0)
        box.pack_start(entry_name, False, False, 0)
        box.pack_start(Gtk.Label(label="URL / Arquivo .flatpakrepo:", xalign=0, margin_top=6), False, False, 0)
        box.pack_start(entry_url, False, False, 0)
        box.pack_start(Gtk.Label(label="Instalar para:", xalign=0, margin_top=6), False, False, 0)
        box.pack_start(combo_scope, False, False, 0)
        box.show_all()
        
        response = dialog.run()
        name = entry_name.get_text().strip()
        url = entry_url.get_text().strip()
        scope = combo_scope.get_active_id()
        dialog.destroy()
        
        if response == Gtk.ResponseType.OK and name and url:
            self.start_loading(f"Adicionando repositório '{name}'...")
            self.run_async(self._execute_add_repo, name, url, scope)

    def _execute_add_repo(self, name, url, scope):
        """Aciona o backend flatpak para registrar o remote via CLI. Pode usar pkexec se escopo incluir system."""
        try:
            if scope in ["user", "both"]:
                self._run_flatpak_action(['flatpak', 'remote-add', '--user', '--if-not-exists', name, url])
            
            if scope in ["system", "both"]:
                self._run_flatpak_action(['pkexec', 'flatpak', 'remote-add', '--system', '--if-not-exists', name, url])
                
            self._sync_all()
            GLib.idle_add(self.stop_loading, f"Repositório '{name}' adicionado com sucesso!")
        except Exception as e:
            GLib.idle_add(self.stop_loading, f"Erro ao adicionar repositório: {e}")

    def on_remove_repo(self, name):
        self.start_loading(f"Removendo repositório '{name}'...")
        self.run_async(self._execute_remove_repo, name)

    def _execute_remove_repo(self, name):
        try:
            self._run_flatpak_action(['flatpak', 'remote-delete', '--user', '-y', '--noninteractive', name])
            self._sync_all()
            GLib.idle_add(self.stop_loading, f"Repositório '{name}' removido!")
        except Exception as e:
            GLib.idle_add(self.stop_loading, f"Erro ao remover repositório: {e}")

    def on_search(self, widget):
        """Gatilho primário ativado ao pressionar 'Enter' no input ou ao clicar em Buscar."""
        query = self.search_entry.get_text().strip()
        if not query: return
        self.notebook.set_current_page(0)
        cached = self.get_from_cache(query)
        
        if cached:
            self._render_results(cached)
            self.stop_loading(f"Exibindo do cache: '{query}'")
            return
            
        self.start_loading(f"Buscando online por '{query}'...")
        self.run_async(self._perform_search, query)

    def _perform_search(self, query):
        try:
            res = self._run_flatpak_cmd(['search', query, '--columns=name,application,remotes'])
            items = []
            for l in res.stdout.strip().split('\n'):
                if '\t' in l and "Name" not in l:
                    parts = [p.strip() for p in l.split('\t')]
                    if len(parts) >= 3:
                        repo_name = parts[2].split(',')[0].strip()
                        items.append((parts[0], parts[1], repo_name))
                    elif len(parts) == 2:
                        items.append((parts[0], parts[1], "Desconhecido"))
                        
            self.save_to_cache(query, items)
            GLib.idle_add(self._render_results, items)
            GLib.idle_add(self.stop_loading, f"{len(items)} resultados encontrados.")
        except Exception as e:
            logger.error(f"Erro na busca: {e}")
            GLib.idle_add(self.stop_loading, f"Falha na busca: {e}")

    def _render_results(self, apps=None):
        [child.destroy() for child in self.flow_results.get_children()]
        if apps is not None:
            self.current_results_data = apps
        else:
            apps = getattr(self, 'current_results_data', [])

        if not apps:
            self.flow_results.add(self.create_empty_state_label("Nenhum resultado encontrado para a sua busca."))
        else:
            for item in apps:
                if not item:
                    continue
                if len(item) >= 3:
                    n, i, r = item[0], item[1], item[2]
                elif len(item) == 2:
                    n, i, r = item[0], item[1], "Desconhecido"
                else:
                    continue
                self.flow_results.add(self.create_app_card(n, i, r, i in self.installed_apps))
        self.flow_results.show_all()

    def _render_installed(self):
        [child.destroy() for child in self.flow_installed.get_children()]
        try:
            res = self._run_flatpak_cmd(['list', '--app', '--columns=name,application,origin'])
            items = []
            if res.returncode == 0:
                for l in res.stdout.strip().split('\n'):
                    if '\t' in l and "Name" not in l:
                        parts = [p.strip() for p in l.split('\t')]
                        if len(parts) >= 3:
                            items.append((parts[0], parts[1], parts[2]))
                        elif len(parts) == 2:
                            items.append((parts[0], parts[1], "Desconhecido"))
            
            if items:
                for item in items:
                    if len(item) >= 3:
                        n, i, r = item[0], item[1], item[2]
                    elif len(item) == 2:
                        n, i, r = item[0], item[1], "Desconhecido"
                    else:
                        continue
                    self.flow_installed.add(self.create_app_card(n, i, r, True))
            else:
                self.flow_installed.add(self.create_empty_state_label("Você ainda não instalou nenhum aplicativo via Flatpak."))
        except Exception as e:
            self.flow_installed.add(self.create_empty_state_label(f"Erro ao listar apps: {e}"))
            
        self.flow_installed.show_all()

    def on_action_click(self, app_id, action, repo):
        """Avalia a ação solicitada na UI e exibe caixas de diálogo para confirmar escopo antes de enviar ao CLI."""
        scope = "--user"
        
        if action == "install":
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text=f"Instalação de {app_id}"
            )
            dialog.format_secondary_text(
                f"Este aplicativo pertence ao repositório '{repo}'.\n\n"
                "Deseja instalá-lo apenas para o seu usuário (não requer senha) ou "
                "para todo o sistema (requer senha de administrador)?"
            )
            dialog.add_button("Meu Usuário", 1)
            dialog.add_button("Todo o Sistema (Admin)", 2)
            dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
            
            response = dialog.run()
            dialog.destroy()
            
            if response == Gtk.ResponseType.CANCEL or response < 0:
                return
                
            scope = "--user" if response == 1 else "--system"
            
            check_res = self._run_flatpak_cmd(['remotes', scope, '--columns=name'])
            available_repos = check_res.stdout.split() if check_res.returncode == 0 else []
            
            if repo not in available_repos and repo != "Desconhecido":
                self.show_error_dialog(
                    "Repositório Indisponível", 
                    f"O repositório '{repo}' não está configurado para a opção escolhida ({scope}).\n\n"
                    f"Dica: Vá na aba 'Repositórios' e adicione a fonte de aplicativos adequadamente."
                )
                return
        elif action in ["uninstall", "update"]:
            res_sys = self._run_flatpak_cmd(['info', '--system', app_id])
            if res_sys.returncode == 0:
                scope = "--system"
            else:
                scope = "--user"

        self.active_operations[app_id] = action
        self._render_all_views()

        msg = {"install": "Instalando", "uninstall": "Removendo", "update": "Atualizando"}.get(action, "Processando")
        logger.info(f"Usuário solicitou ação: {action} para {app_id} no escopo {scope}")
        self.start_loading(f"{msg} {app_id}... Acompanhe no terminal.")
        self.run_async(self._execute_action, action, app_id, scope)

    def _execute_action(self, action, app_id, scope):
        try:
            cmd = []
            if scope == "--system":
                cmd = ['pkexec', 'flatpak', action, '--system', '-y', '--noninteractive', app_id]
            else:
                cmd = ['flatpak', action, '--user', '-y', '--noninteractive', app_id]

            self._run_flatpak_action(cmd)
            
            self.clear_disk_cache()
            self._sync_all()
            
            q = self.search_entry.get_text().strip()
            if q: self._perform_search(q)
            
            GLib.idle_add(self.stop_loading, f"Operação em {app_id} concluída com sucesso.")
        except Exception as e:
            GLib.idle_add(self.stop_loading, f"Falha ao modificar {app_id}: verifique o terminal.")
        finally:
            self.active_operations.pop(app_id, None)
            GLib.idle_add(self._render_all_views)

    def on_refresh_clicked(self, widget):
        self.clear_disk_cache()
        self.start_loading("Sincronizando catálogos... Acompanhe no terminal.")
        self.run_async(self._refresh_repos)

    def _refresh_repos(self):
        logger.info("Executando atualização de repositórios (AppStream)...")
        try:
            self._run_flatpak_action(['flatpak', 'update', '--appstream'])
            self._sync_all()
        except Exception as e:
            GLib.idle_add(self.stop_loading, f"Falha na sincronização: {e}")

    def on_destroy(self, widget):
        logger.info("Encerrando FlatStore...")
        if self.clear_on_exit: self.clear_disk_cache()
        Gtk.main_quit()

if __name__ == "__main__":
    app = FlatpakStoreApp()
    app.connect("destroy", app.on_destroy)
    app.show_all()
    Gtk.main()