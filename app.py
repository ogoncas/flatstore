import os
import shutil
import sys
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

# CSS nativo para um visual plano/limpo mantendo sintonia com temas GTK/XFCE
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

        # Estado geral da aplicação e Locks para threads
        self.cache_ttl = DEFAULT_CACHE_TTL
        self.clear_on_exit = False
        self.cache = {}
        self.cache_lock = threading.Lock()
        
        self.installed_apps = set()
        self.updates_available = []
        self.repositories = []
        
        # Estado de operações ativas e resultados
        self.active_operations = {}
        self.current_results_data = {}
        # Lock (em vez de bool) para tornar a checagem "já sincronizando?" atômica
        # e evitar corridas quando duas ações disparam _sync_all quase ao mesmo tempo.
        self.sync_lock = threading.Lock()

        logger.info("Iniciando FlatStore...")
        self.load_config()
        self.apply_native_css()
        self.load_cache_from_disk()

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.add(main_box)

        # Configuração da HeaderBar
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title("FlatStore")
        header.set_subtitle("Gerenciador Nativo para XFCE")
        self.set_titlebar(header)

        header.pack_end(self.create_settings_menu())
        
        self.spinner = Gtk.Spinner()
        header.pack_end(self.spinner)

        self.btn_refresh = Gtk.Button(label="Sincronizar")
        self.btn_refresh.set_tooltip_text("Sincroniza AppStream, atualiza repositórios e limpa o cache local")
        self.btn_refresh.connect("clicked", self.on_refresh_clicked)
        header.pack_start(self.btn_refresh)

        # Container de pesquisa
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

        # Aba 1: Busca e Destaques
        self.results_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.notebook.append_page(self.wrap_in_scroll(self.results_container), Gtk.Label(label="✨ Início"))

        # Aba 2: Aplicativos Instalados
        self.flow_installed = self.create_flowbox()
        self.notebook.append_page(self.wrap_in_scroll(self.flow_installed), Gtk.Label(label="📦 Instalados"))

        # Aba 3: Atualizações
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

        # Aba 4: Gestão de Repositórios
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

    def _clear_container(self, container):
        """Remove e destrói widgets filhos de forma limpa sem causar vazamento de memória."""
        for child in container.get_children():
            container.remove(child)
            child.destroy()

    def _run_flatpak_cmd(self, args, check=False):
        """Executa comandos de leitura do Flatpak com timeout e prevenção de bloqueio."""
        cmd = ['flatpak'] + args
        logger.debug(f"Executando (Leitura): {' '.join(cmd)}")
        try:
            return subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=check,
                timeout=CMD_TIMEOUT
            )
        except subprocess.TimeoutExpired:
            logger.warning(f"Comando excedeu o tempo limite: {' '.join(cmd)}")
            raise
        except Exception as e:
            logger.error(f"Falha ao executar flatpak: {e}")
            raise

    def _run_flatpak_action(self, cmd):
        """Executa comandos de modificação garantindo suporte a PolKit/pkexec."""
        logger.info(f">>> INICIANDO AÇÃO: {' '.join(cmd)}")
        try:
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if line.strip():
                        logger.info(f"[Flatpak] {line.strip()}")
                process.stdout.close()
                
            return_code = process.wait()
            logger.info(f">>> AÇÃO FINALIZADA COM CÓDIGO: {return_code}")
            
            if return_code != 0:
                raise RuntimeError(f"Comando falhou com código de saída {return_code}")
        except Exception as e:
            logger.error(f"Erro Crítico durante a ação: {e}")
            raise

    def _parse_flatpak_table(self, stdout, num_cols=3):
        """Converte a saída tabular (separada por TAB) de comandos flatpak em uma lista
        de tuplas de tamanho fixo, ignorando a linha de cabeçalho e preenchendo colunas
        ausentes com 'Desconhecido'."""
        rows = []
        if not stdout or not stdout.strip():
            return rows
        for line in stdout.strip().split('\n'):
            if '\t' not in line or line.startswith('Name\t'):
                continue
            parts = [p.strip() for p in line.split('\t')]
            if not parts or not parts[0]:
                continue
            parts = (parts + ["Desconhecido"] * num_cols)[:num_cols]
            rows.append(tuple(parts))
        return rows

    def _list_remote_names(self, scope_flag):
        """Retorna os nomes de repositórios configurados no escopo indicado
        ('--user' ou '--system'), a partir de uma coluna única, ignorando o
        cabeçalho. Usa quebra de linha (não .split() genérico) para não
        corromper nomes de repositório que eventualmente contenham espaços."""
        res = self._run_flatpak_cmd(['remotes', scope_flag, '--columns=name'])
        if res.returncode != 0 or not res.stdout.strip():
            return []
        return [
            line.strip() for line in res.stdout.strip().splitlines()
            if line.strip() and line.strip().lower() != "name"
        ]

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
        except Exception as e:
            logger.warning(f"Erro ao carregar configurações: {e}")
            self.cache_ttl = DEFAULT_CACHE_TTL

    def save_config(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump({"cache_ttl": self.cache_ttl, "clear_on_exit": self.clear_on_exit}, f)
        except Exception as e:
            logger.error(f"Erro ao salvar configurações: {e}")

    def load_cache_from_disk(self):
        with self.cache_lock:
            try:
                if os.path.exists(CACHE_FILE):
                    with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        now = time.time()
                        self.cache = {k: v for k, v in data.items() if now - v.get('timestamp', 0) < self.cache_ttl}
            except Exception as e:
                logger.warning(f"Erro ao carregar cache do disco: {e}")
                self.cache = {}

    def save_cache_to_disk(self):
        with self.cache_lock:
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.cache, f, ensure_ascii=False)
            except Exception as e:
                logger.error(f"Erro ao salvar cache no disco: {e}")

    def clear_disk_cache(self):
        with self.cache_lock:
            self.cache.clear()
            if os.path.exists(CACHE_FILE):
                try: 
                    os.remove(CACHE_FILE)
                except Exception as e: 
                    logger.warning(f"Falha ao deletar arquivo de cache: {e}")
        logger.info("Cache local limpo.")

    def get_from_cache(self, key):
        with self.cache_lock:
            if key in self.cache:
                item = self.cache[key]
                if time.time() - item.get('timestamp', 0) < self.cache_ttl:
                    return item.get('data')
                else:
                    del self.cache[key]
        return None

    def save_to_cache(self, key, data):
        with self.cache_lock:
            self.cache[key] = {'timestamp': time.time(), 'data': data}
        self.save_cache_to_disk()

    def create_settings_menu(self):
        btn = Gtk.MenuButton()
        btn.set_image(Gtk.Image.new_from_icon_name("preferences-system-symbolic", Gtk.IconSize.BUTTON))
        
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_border_width(14)

        box.pack_start(Gtk.Label(label="<b>Configurações</b>", use_markup=True, xalign=0), False, False, 0)
        box.pack_start(Gtk.Label(label="Validade do Cache:", xalign=0), False, False, 0)

        combo = Gtk.ComboBoxText()
        ttl_options = [("1800", "30 Min"), ("3600", "1 Hora"), ("21600", "6 Horas"), ("86400", "24 Horas")]
        for t_id, t_lbl in ttl_options:
            combo.append(t_id, t_lbl)
        if not combo.set_active_id(str(self.cache_ttl)):
            # self.cache_ttl não corresponde a nenhuma opção (ex.: config.json editado
            # manualmente); aplica o padrão e mantém o modelo sincronizado com a UI.
            combo.set_active(1)
            self.cache_ttl = int(ttl_options[1][0])
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
        about.set_version("1.2")
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
        flow.set_row_spacing(12)
        flow.set_column_spacing(12)
        # Torna todos os itens do FlowBox do mesmo tamanho
        flow.set_homogeneous(True) 
        return flow

    def wrap_in_scroll(self, widget):
        scroll = Gtk.ScrolledWindow()
        scroll.set_border_width(4)
        scroll.add(widget)
        return scroll

    def start_loading(self, msg):
        self.status_label.set_text(msg)
        self.spinner.start()
        self.btn_refresh.set_sensitive(False)

    def stop_loading(self, msg=""):
        self.spinner.stop()
        self.btn_refresh.set_sensitive(True)
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

    def _require_pkexec(self, on_main_thread=True):
        """Confere se 'pkexec' está disponível antes de qualquer operação em nível
        de sistema. Se ausente, avisa o usuário com uma mensagem acionável e
        retorna False, para o chamador cancelar com uma causa clara em vez de
        deixar o subprocesso falhar silenciosamente mais adiante."""
        if shutil.which("pkexec"):
            return True

        title = "Ferramenta Indisponível"
        message = (
            "O utilitário 'pkexec' (PolicyKit) não foi encontrado neste sistema.\n\n"
            "Operações em nível de sistema — que afetam todos os usuários — "
            "dependem dele para solicitar a senha de administrador.\n\n"
            "Instale o pacote 'policykit-1' (ou equivalente da sua distribuição) "
            "ou utilize a opção 'Meu Usuário', que não exige privilégios administrativos."
        )
        if on_main_thread:
            self.show_error_dialog(title, message)
        else:
            GLib.idle_add(self.show_error_dialog, title, message)
        return False

    def _render_all_views(self):
        self._render_results()
        self._render_installed()
        self._render_updates()

    def get_app_icon(self, app_id, is_installed, is_update):
        theme = Gtk.IconTheme.get_default()
        try:
            pixbuf = theme.load_icon(app_id, 48, 0)
            return Gtk.Image.new_from_pixbuf(pixbuf)
        except Exception:
            icon_name = "software-update-available" if is_update else ("package-x-generic" if is_installed else "system-run")
            return Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.DND)

    def create_app_card(self, name, app_id, repo, is_installed=False, is_update=False):
        frame = Gtk.Frame()
        frame.get_style_context().add_class("app-card")
        frame.set_tooltip_text(f"{name}\n({app_id})") 
        
        # Define um tamanho fixo (Largura x Altura) para manter a uniformidade
        frame.set_size_request(320, 130)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        app_image = self.get_app_icon(app_id, is_installed, is_update)
        # Impede a imagem de esticar
        top.pack_start(app_image, False, False, 0)

        meta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        # Faz o bloco de texto expandir horizontalmente para preencher o card
        meta.set_hexpand(True) 

        lbl_name = Gtk.Label(label=f"<b>{name}</b>", use_markup=True, xalign=0)
        lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
        # Força a label a aceitar encolher em vez de esticar o card
        lbl_name.set_max_width_chars(1) 
        
        lbl_id = Gtk.Label(label=app_id, xalign=0)
        lbl_id.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        lbl_id.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_id.set_max_width_chars(1)
        
        lbl_repo = Gtk.Label(label=f"📦 {repo}", xalign=0)
        lbl_repo.get_style_context().add_class("repo-label")
        # Previne que nomes de repositórios gigantes quebrem o layout
        lbl_repo.set_ellipsize(Pango.EllipsizeMode.END)
        lbl_repo.set_max_width_chars(1)
        
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
            
        # Alinha o botão na base do card para que fiquem todos na mesma altura
        btn.set_valign(Gtk.Align.END)
        box.pack_end(btn, False, False, 0)
        
        frame.add(box)
        frame.show_all()
        return frame

    def create_empty_state_label(self, message):
        lbl = Gtk.Label(label=f"<i>{message}</i>", use_markup=True, margin_top=20)
        lbl.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        lbl.set_halign(Gtk.Align.CENTER)
        return lbl

    def init_data(self):
        if shutil.which("flatpak") is None:
            logger.error("Binário 'flatpak' não encontrado no PATH.")
            self.status_label.set_text("Flatpak não encontrado neste sistema.")
            self.results_container.pack_start(
                self.create_empty_state_label(
                    "O Flatpak não está instalado neste sistema.\n"
                    "Instale o pacote 'flatpak' e reinicie o FlatStore."
                ),
                False, False, 0
            )
            self.results_container.show_all()
            return
        GLib.idle_add(self.start_loading, "Sincronizando ambiente local...")
        self.run_async(self._sync_all)

    def _sync_all(self):
        # Tenta adquirir o lock sem bloquear: se já houver uma sincronização em
        # andamento, ignora esta chamada em vez de empilhar trabalho duplicado.
        if not self.sync_lock.acquire(blocking=False):
            logger.info("Sincronização já em andamento; chamada duplicada ignorada.")
            return
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
        finally:
            self.sync_lock.release()

    def _update_installed_set(self):
        res = self._run_flatpak_cmd(['list', '--app', '--columns=application'])
        if res.returncode == 0 and res.stdout.strip():
            self.installed_apps = {a.strip() for a in res.stdout.strip().split('\n') if a.strip()}
        else:
            self.installed_apps = set()

    def _fetch_featured_from_cli(self):
        """Otimizado para buscar rapidamente destaques sem engasgar a interface."""
        categories = {
            "🚀 Produtividade e Utilidades": "office",
            "🎨 Mídia e Gráficos": "media",
            "💻 Desenvolvimento": "development",
            "🎮 Jogos": "games"
        }
        
        results = {}
        for cat_name, kw in categories.items():
            try:
                res = self._run_flatpak_cmd(['search', kw, '--columns=name,application,remotes'])
                rows = self._parse_flatpak_table(res.stdout) if res.returncode == 0 else []
            except Exception as e:
                logger.warning(f"Erro ao buscar destaques para {cat_name}: {e}")
                continue

            cat_apps = []
            for n, i, r in rows[:4]:
                entry = (n, i, r.split(',')[0].strip())
                if entry not in cat_apps:
                    cat_apps.append(entry)

            if cat_apps:
                results[cat_name] = cat_apps
                
        return results

    def _fetch_updates(self):
        res = self._run_flatpak_cmd(['list', '--updates', '--columns=name,application,origin'])
        self.updates_available = self._parse_flatpak_table(res.stdout) if res.returncode == 0 else []

    def _render_updates(self):
        self._clear_container(self.flow_updates)
        count = len(self.updates_available)
        
        self.lbl_updates_count.set_text(f"<b>{count} atualização(ões)</b>" if count > 0 else "Sistema atualizado.")
        self.lbl_updates_count.set_use_markup(True)
        self.tab_updates_label.set_text(f"🔄 Atualizações ({count})" if count > 0 else "🔄 Atualizações")
        self.btn_update_all.set_sensitive(count > 0)

        for name, app_id, repo in self.updates_available:
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

            # A lista de atualizações pendentes (aba "Atualizações") combina apps de
            # escopo usuário E sistema, mas antes só o escopo usuário era de fato
            # atualizado aqui. Sem pkexec, avisamos e seguimos (não bloqueamos o
            # que já foi atualizado com sucesso no escopo usuário).
            if shutil.which("pkexec"):
                self._run_flatpak_action(['pkexec', 'flatpak', 'update', '--system', '-y', '--noninteractive'])
            else:
                logger.warning("pkexec indisponível: atualizações em nível de sistema foram puladas.")

            self.clear_disk_cache()
            self._sync_all()
            GLib.idle_add(self.stop_loading, "Todas as atualizações concluídas com sucesso!")
        except Exception as e:
            logger.error(f"Erro ao atualizar todos os aplicativos: {e}")
            GLib.idle_add(self.stop_loading, f"Falha na atualização: {e}")

    def _fetch_repos(self):
        res = self._run_flatpak_cmd(['remotes', '--columns=name,url'])
        self.repositories = self._parse_flatpak_table(res.stdout, num_cols=2) if res.returncode == 0 else []

    def _render_repos(self):
        self._clear_container(self.listbox_repos)
        
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
        
        if response != Gtk.ResponseType.OK:
            return

        if not name or not url:
            self.show_error_dialog("Dados Incompletos", "Informe um nome e uma URL/arquivo válidos para o repositório.")
            return

        if name.startswith('-') or url.startswith('-'):
            self.show_error_dialog("Valor Inválido", "O nome e a URL do repositório não podem começar com '-'.")
            return

        if scope in ("system", "both") and not self._require_pkexec():
            return

        self.start_loading(f"Adicionando repositório '{name}'...")
        self.run_async(self._execute_add_repo, name, url, scope)

    def _execute_add_repo(self, name, url, scope):
        try:
            if scope in ["user", "both"]:
                self._run_flatpak_action(['flatpak', 'remote-add', '--user', '--if-not-exists', name, url])
            
            if scope in ["system", "both"]:
                self._run_flatpak_action(['pkexec', 'flatpak', 'remote-add', '--system', '--if-not-exists', name, url])
                
            self._sync_all()
            GLib.idle_add(self.stop_loading, f"Repositório '{name}' adicionado com sucesso!")
        except Exception as e:
            logger.error(f"Erro ao adicionar repositório '{name}': {e}")
            GLib.idle_add(self.stop_loading, f"Erro ao adicionar repositório: {e}")

    def on_remove_repo(self, name):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Remover o repositório '{name}'?"
        )
        dialog.format_secondary_text(
            "Os aplicativos já instalados a partir dele continuarão funcionando, mas "
            "você não poderá mais instalar novidades nem receber atualizações desta "
            "fonte até adicioná-la novamente."
        )
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        btn_confirm = dialog.add_button("Remover", Gtk.ResponseType.OK)
        btn_confirm.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)
        response = dialog.run()
        dialog.destroy()

        if response != Gtk.ResponseType.OK:
            return

        self.start_loading(f"Removendo repositório '{name}'...")
        self.run_async(self._execute_remove_repo, name)

    def _execute_remove_repo(self, name):
        try:
            # O botão "Remover" não sabe em qual escopo o repositório foi cadastrado.
            # Antes, a remoção assumia sempre '--user', então repositórios adicionados
            # como 'Sistema' ou 'Ambos' pareciam remover mas continuavam lá. Aqui
            # detectamos em qual(is) escopo(s) o nome existe e removemos de cada um.
            in_user = name in self._list_remote_names('--user')
            in_system = name in self._list_remote_names('--system')

            if not in_user and not in_system:
                # Não foi possível confirmar o escopo (ex.: já removido antes);
                # tenta o caminho mais comum, que não exige privilégios administrativos.
                in_user = True

            if in_user:
                self._run_flatpak_action(['flatpak', 'remote-delete', '--user', '-y', '--noninteractive', name])

            removed_system = False
            if in_system:
                if self._require_pkexec(on_main_thread=False):
                    self._run_flatpak_action(['pkexec', 'flatpak', 'remote-delete', '--system', '-y', '--noninteractive', name])
                    removed_system = True
                elif not in_user:
                    GLib.idle_add(self.stop_loading, f"Não foi possível remover '{name}': pkexec indisponível para o escopo de sistema.")
                    return

            self._sync_all()
            if in_system and not removed_system:
                GLib.idle_add(self.stop_loading, f"'{name}' removido apenas do escopo de usuário (sistema exige pkexec).")
            else:
                GLib.idle_add(self.stop_loading, f"Repositório '{name}' removido!")
        except Exception as e:
            logger.error(f"Erro ao remover repositório '{name}': {e}")
            GLib.idle_add(self.stop_loading, f"Erro ao remover repositório: {e}")

    def on_search(self, widget):
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
            items = self._parse_flatpak_table(res.stdout) if res.returncode == 0 else []
            items = [(n, i, r.split(',')[0].strip()) for n, i, r in items]

            self.save_to_cache(query, items)
            GLib.idle_add(self._render_results, items)
            GLib.idle_add(self.stop_loading, f"{len(items)} resultados encontrados.")
        except Exception as e:
            logger.error(f"Erro na busca: {e}")
            GLib.idle_add(self.stop_loading, f"Falha na busca: {e}")

    def _render_results(self, apps_data=None):
        self._clear_container(self.results_container)
            
        if apps_data is not None:
            self.current_results_data = apps_data
        else:
            apps_data = getattr(self, 'current_results_data', {})

        if not apps_data:
            self.results_container.pack_start(self.create_empty_state_label("Nenhum resultado encontrado para a sua busca."), False, False, 0)
            self.results_container.show_all()
            return
            
        if isinstance(apps_data, list):
            apps_data = {"🔍 Resultados da Busca": apps_data}

        for category, apps in apps_data.items():
            if not apps: continue
            
            lbl_cat = Gtk.Label(label=f"<b>{category}</b>", use_markup=True, xalign=0)
            lbl_cat.set_margin_top(12)
            lbl_cat.set_margin_start(6)
            self.results_container.pack_start(lbl_cat, False, False, 0)
            
            flow = self.create_flowbox()
            for item in apps:
                if not item or len(item) < 3:
                    continue
                n, i, r = item[0], item[1], item[2]
                flow.add(self.create_app_card(n, i, r, i in self.installed_apps))
                
            self.results_container.pack_start(flow, False, False, 0)

        self.results_container.show_all()

    def _render_installed(self):
        self._clear_container(self.flow_installed)
        try:
            res = self._run_flatpak_cmd(['list', '--app', '--columns=name,application,origin'])
            items = self._parse_flatpak_table(res.stdout) if res.returncode == 0 else []

            if items:
                for n, i, r in items:
                    self.flow_installed.add(self.create_app_card(n, i, r, True))
            else:
                self.flow_installed.add(self.create_empty_state_label("Você ainda não instalou nenhum aplicativo via Flatpak."))
        except Exception as e:
            logger.error(f"Erro ao listar aplicativos instalados: {e}")
            self.flow_installed.add(self.create_empty_state_label(f"Erro ao listar apps: {e}"))
            
        self.flow_installed.show_all()

    def on_action_click(self, app_id, action, repo):
        scope_pref = None

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
                
            scope_pref = "user" if response == 1 else "system"
            if scope_pref == "system" and not self._require_pkexec():
                return
            # A verificação de disponibilidade do repositório (e, para uninstall/update,
            # a resolução do escopo) é feita em segundo plano em _execute_action, para não
            # bloquear a interface com chamadas ao flatpak na thread principal do GTK.

        elif action == "uninstall":
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text=f"Remover {app_id}?"
            )
            dialog.format_secondary_text(
                "Esta ação irá desinstalar o aplicativo do seu sistema.\n"
                "Os dados pessoais salvos por ele (em ~/.var/app) geralmente não são apagados."
            )
            dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
            btn_confirm = dialog.add_button("Remover", Gtk.ResponseType.OK)
            btn_confirm.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)

            response = dialog.run()
            dialog.destroy()

            if response != Gtk.ResponseType.OK:
                return

        self.active_operations[app_id] = action
        self._render_all_views()

        # Obtém a query atual na thread principal do GTK para evitar violação de thread-safety
        current_query = self.search_entry.get_text().strip()

        msg = {"install": "Instalando", "uninstall": "Removendo", "update": "Atualizando"}.get(action, "Processando")
        self.start_loading(f"{msg} {app_id}... Acompanhe no terminal.")
        self.run_async(self._execute_action, action, app_id, repo, scope_pref, current_query)

    def _execute_action(self, action, app_id, repo, scope_pref, current_query=""):
        try:
            if action == "install":
                scope = "--system" if scope_pref == "system" else "--user"

                if scope == "--system" and not self._require_pkexec(on_main_thread=False):
                    GLib.idle_add(self.stop_loading, "Instalação cancelada: pkexec indisponível.")
                    return

                available_repos = self._list_remote_names(scope)

                if repo not in available_repos and repo != "Desconhecido":
                    GLib.idle_add(
                        self.show_error_dialog,
                        "Repositório Indisponível",
                        f"O repositório '{repo}' não está configurado para a opção escolhida ({scope}).\n\n"
                        "Dica: Vá na aba 'Repositórios' e adicione a fonte de aplicativos adequadamente."
                    )
                    GLib.idle_add(self.stop_loading, "Instalação cancelada: repositório indisponível.")
                    return

                cmd = ['pkexec', 'flatpak', 'install', '--system', '-y', '--noninteractive', app_id] \
                    if scope == "--system" else ['flatpak', 'install', '--user', '-y', '--noninteractive', app_id]
            elif action in ("uninstall", "update"):
                res_sys = self._run_flatpak_cmd(['info', '--system', app_id])
                scope = "--system" if res_sys.returncode == 0 else "--user"

                if scope == "--system" and not self._require_pkexec(on_main_thread=False):
                    GLib.idle_add(self.stop_loading, "Operação cancelada: pkexec indisponível.")
                    return

                cmd = ['pkexec', 'flatpak', action, '--system', '-y', '--noninteractive', app_id] \
                    if scope == "--system" else ['flatpak', action, '--user', '-y', '--noninteractive', app_id]
            else:
                logger.error(f"Ação desconhecida recebida: {action}")
                return

            logger.info(f"Usuário solicitou ação: {action} para {app_id} no escopo {scope}")
            self._run_flatpak_action(cmd)
            
            self.clear_disk_cache()
            self._sync_all()
            
            if current_query:
                self._perform_search(current_query)
            
            GLib.idle_add(self.stop_loading, f"Operação em {app_id} concluída com sucesso.")
        except Exception as e:
            logger.error(f"Erro na ação {action} para {app_id}: {e}")
            GLib.idle_add(self.stop_loading, f"Falha ao modificar {app_id}: verifique os logs.")
        finally:
            def _cleanup():
                self.active_operations.pop(app_id, None)
                self._render_all_views()
            GLib.idle_add(_cleanup)

    def install_from_ref(self, ref_file):
        if not os.path.exists(ref_file):
            self.show_error_dialog("Arquivo não encontrado", f"Não foi possível localizar o arquivo:\n{ref_file}")
            return

        app_id = os.path.basename(ref_file)
        
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Instalação via Arquivo Local"
        )
        dialog.format_secondary_text(
            f"Você abriu o arquivo de instalação '{app_id}'.\n\n"
            "Escolha se deseja instalá-lo apenas para o seu usuário (não requer senha) ou "
            "para todo o sistema (requer senha de administrador)."
        )
        dialog.add_button("Meu Usuário", 1)
        dialog.add_button("Todo o Sistema (Admin)", 2)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        
        response = dialog.run()
        dialog.destroy()
        
        if response == Gtk.ResponseType.CANCEL or response < 0:
            return
            
        scope = "--user" if response == 1 else "--system"

        if scope == "--system" and not self._require_pkexec():
            return
        
        self.start_loading(f"Instalando {app_id}... Acompanhe no terminal.")
        self.run_async(self._execute_install_ref, ref_file, scope, app_id)

    def _execute_install_ref(self, ref_file, scope, app_id):
        try:
            if scope == "--system":
                cmd = ['pkexec', 'flatpak', 'install', '--system', '-y', '--noninteractive', ref_file]
            else:
                cmd = ['flatpak', 'install', '--user', '-y', '--noninteractive', ref_file]

            self._run_flatpak_action(cmd)
            
            self.clear_disk_cache()
            self._sync_all()
            
            GLib.idle_add(self.stop_loading, f"Instalação do arquivo {app_id} concluída.")
        except Exception as e:
            logger.error(f"Erro ao instalar arquivo .flatpakref: {e}")
            GLib.idle_add(self.stop_loading, "Falha na instalação: verifique o terminal.")

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
            logger.error(f"Erro ao atualizar AppStream: {e}")
            GLib.idle_add(self.stop_loading, f"Falha na sincronização: {e}")

    def on_destroy(self, widget):
        logger.info("Encerrando FlatStore...")
        if self.clear_on_exit: 
            self.clear_disk_cache()
        Gtk.main_quit()

if __name__ == "__main__":
    app = FlatpakStoreApp()
    app.connect("destroy", app.on_destroy)
    app.show_all()
    
    if len(sys.argv) > 1 and sys.argv[1].endswith((".flatpakref", ".flatpak")):
        ref_file = sys.argv[1]
        GLib.idle_add(app.install_from_ref, ref_file)
        
    Gtk.main()