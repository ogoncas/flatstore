#!/usr/bin/env python3
import os
import shutil
import subprocess
import sys
import urllib.request
import tempfile
import threading

try:
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk, GLib
except ImportError:
    print("ERRO CRÍTICO: O pacote python3-gi (PyGObject / GTK3) não está instalado neste sistema.")
    print("Instale-o usando o gerenciador de pacotes da sua distribuição.")
    sys.exit(1)

GITHUB_APP_URL = "https://raw.githubusercontent.com/ogoncas/flatstore/main/app.py"

class FlatStoreManager(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.set_default_size(500, 340)
        self.set_border_width(18)
        self.set_position(Gtk.WindowPosition.CENTER)

        icon_name = "system-software-install"
        Gtk.Window.set_default_icon_name(icon_name)
        self.set_icon_name(icon_name)

        # HeaderBar (Visual moderno para GTK)
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.props.title = "Gerenciador do FlatStore"
        self.set_titlebar(header)

        # Layout Principal
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(main_box)

        # Status atual da instalação
        self.lbl_status_info = Gtk.Label(xalign=0)
        main_box.pack_start(self.lbl_status_info, False, False, 0)

        main_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        # Opção de Escopo
        self.scope_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        lbl_scope = Gtk.Label(label="Escolha o escopo da instalação:", xalign=0)
        self.scope_box.pack_start(lbl_scope, False, False, 0)

        self.radio_user = Gtk.RadioButton.new_with_label(None, "Apenas para o meu usuário (~/.local)")
        self.radio_system = Gtk.RadioButton.new_with_label_from_widget(self.radio_user, "Para todo o sistema (/opt - Requer Admin)")
        
        self.scope_box.pack_start(self.radio_user, False, False, 0)
        self.scope_box.pack_start(self.radio_system, False, False, 0)
        main_box.pack_start(self.scope_box, False, False, 0)

        # Layout de Status (Spinner + Label)
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner = Gtk.Spinner()
        self.status_label = Gtk.Label(label="Aguardando ação...", xalign=0)
        self.status_label.get_style_context().add_class(Gtk.STYLE_CLASS_DIM_LABEL)
        
        status_box.pack_start(self.spinner, False, False, 0)
        status_box.pack_start(self.status_label, True, True, 0)
        main_box.pack_start(status_box, True, True, 0)

        # Botões de Ação
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)

        btn_cancel = Gtk.Button(label="Sair")
        btn_cancel.connect("clicked", lambda w: Gtk.main_quit())

        self.btn_remove = Gtk.Button(label="Desinstalar")
        self.btn_remove.get_style_context().add_class(Gtk.STYLE_CLASS_DESTRUCTIVE_ACTION)
        self.btn_remove.connect("clicked", self.on_remove_clicked)

        self.btn_action = Gtk.Button()
        self.btn_action.connect("clicked", self.on_action_clicked)

        btn_box.pack_start(btn_cancel, False, False, 0)
        btn_box.pack_start(self.btn_remove, False, False, 0)
        btn_box.pack_start(self.btn_action, False, False, 0)
        main_box.pack_start(btn_box, False, False, 0)

        # Inicializa o estado visual
        self.refresh_ui_state()

    def detect_installation(self):
        self.is_installed_system = os.path.exists("/opt/flatstore/app.py")
        self.is_installed_user = os.path.exists(os.path.expanduser("~/.local/share/flatstore/app.py"))
        self.is_installed = self.is_installed_system or self.is_installed_user

    def refresh_ui_state(self):
        self.detect_installation()

        if self.is_installed_system:
            self.lbl_status_info.set_markup("Status: <span foreground='#2ecc71'><b>Instalado no Sistema (/opt)</b></span>")
        elif self.is_installed_user:
            self.lbl_status_info.set_markup("Status: <span foreground='#2ecc71'><b>Instalado no Usuário (~/.local)</b></span>")
        else:
            self.lbl_status_info.set_markup("Status: <span foreground='#f39c12'><b>Não instalado</b></span>")

        # Gerencia botões
        if self.is_installed:
            self.btn_action.set_label("Atualizar Versão")
            self.btn_action.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
            
            self.btn_remove.set_visible(True)
            self.btn_remove.set_no_show_all(False)
            self.scope_box.set_sensitive(False)
            
            if self.is_installed_system:
                self.radio_system.set_active(True)
            else:
                self.radio_user.set_active(True)
        else:
            self.btn_action.set_label("Instalar Agora")
            self.btn_action.get_style_context().add_class(Gtk.STYLE_CLASS_SUGGESTED_ACTION)
            
            self.btn_remove.set_visible(False)
            self.btn_remove.set_no_show_all(True)
            self.scope_box.set_sensitive(True)

    def show_msg(self, title, text, mtype=Gtk.MessageType.INFO):
        dialog = Gtk.MessageDialog(
            transient_for=self, flags=0, message_type=mtype,
            buttons=Gtk.ButtonsType.OK, text=title
        )
        dialog.format_secondary_text(text)
        dialog.run()
        dialog.destroy()

    def set_ui_busy(self, is_busy, status_text=""):
        self.btn_action.set_sensitive(not is_busy)
        self.btn_remove.set_sensitive(not is_busy)
        self.radio_system.set_sensitive(not is_busy if not self.is_installed else False)
        self.radio_user.set_sensitive(not is_busy if not self.is_installed else False)
        
        if is_busy:
            self.spinner.start()
            self.status_label.set_text(status_text)
        else:
            self.spinner.stop()
            self.status_label.set_text(status_text)

    # --- FLUXO DE INSTALAÇÃO (THREADED) ---
    def on_action_clicked(self, widget):
        is_system = self.radio_system.get_active()
        
        if is_system and not shutil.which("pkexec"):
            self.show_msg("Erro", "O utilitário 'pkexec' é obrigatório para instalações no sistema.", Gtk.MessageType.ERROR)
            return

        self.set_ui_busy(True, "Iniciando processo...")
        # Executa em Thread separada para não travar a GUI
        threading.Thread(target=self._worker_install, args=(is_system,), daemon=True).start()

    def _worker_install(self, is_system):
        try:
            GLib.idle_add(self.status_label.set_text, "Baixando aplicativo do GitHub...")
            python_path = shutil.which("python3") or sys.executable

            # Usa diretório temporário seguro (evita race conditions)
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_app_path = os.path.join(tmpdir, "app.py")
                
                try:
                    urllib.request.urlretrieve(GITHUB_APP_URL, tmp_app_path)
                except Exception as e:
                    raise Exception(f"Falha na rede ao conectar com GitHub:\n{e}")

                if not os.path.exists(tmp_app_path) or os.path.getsize(tmp_app_path) < 100:
                    raise Exception("O arquivo baixado está corrompido ou vazio.")

                # Preparar Wrapper localmente no tmp
                wrapper_path = os.path.join(tmpdir, "flatstore_wrapper")
                install_target = "/opt/flatstore" if is_system else os.path.expanduser("~/.local/share/flatstore")
                with open(wrapper_path, "w") as f:
                    f.write(f"#!/bin/bash\nexec {python_path} {install_target}/app.py \"$@\"\n")

                # Preparar Desktop Entry localmente no tmp
                desktop_path = os.path.join(tmpdir, "flatstore.desktop")
                exec_path = "/usr/local/bin/flatstore" if is_system else os.path.expanduser("~/.local/bin/flatstore")
                with open(desktop_path, "w") as f:
                    f.write(f"[Desktop Entry]\nName=FlatStore\nComment=Gerenciador Nativo Flatpak para GTK/XFCE\nExec={exec_path}\nIcon=system-software-install\nTerminal=false\nType=Application\nCategories=Utility\nStartupNotify=true\n")

                if is_system:
                    GLib.idle_add(self.status_label.set_text, "Solicitando privilégios (Digite sua senha)...")
                    # Agrupa os comandos em uma única chamada pkexec para eficiência
                    cmd = f"""
                    mkdir -p /opt/flatstore && \
                    cp "{tmp_app_path}" /opt/flatstore/app.py && \
                    chmod +x /opt/flatstore/app.py && \
                    cp "{wrapper_path}" /usr/local/bin/flatstore && \
                    chmod +x /usr/local/bin/flatstore && \
                    mkdir -p /usr/share/applications && \
                    cp "{desktop_path}" /usr/share/applications/flatstore.desktop && \
                    chmod 644 /usr/share/applications/flatstore.desktop
                    """
                    subprocess.run(['pkexec', 'bash', '-c', cmd], check=True)
                    
                    if shutil.which("update-desktop-database"):
                        subprocess.run(['pkexec', 'update-desktop-database', '/usr/share/applications'], capture_output=True)

                else:
                    GLib.idle_add(self.status_label.set_text, "Instalando localmente para o usuário...")
                    bin_dir = os.path.expanduser("~/.local/bin")
                    app_desktop_dir = os.path.expanduser("~/.local/share/applications")

                    os.makedirs(install_target, exist_ok=True)
                    os.makedirs(bin_dir, exist_ok=True)
                    os.makedirs(app_desktop_dir, exist_ok=True)

                    shutil.move(tmp_app_path, os.path.join(install_target, "app.py"))
                    os.chmod(os.path.join(install_target, "app.py"), 0o755)

                    shutil.move(wrapper_path, exec_path)
                    os.chmod(exec_path, 0o755)

                    shutil.move(desktop_path, os.path.join(app_desktop_dir, "flatstore.desktop"))
                    os.chmod(os.path.join(app_desktop_dir, "flatstore.desktop"), 0o644)

                    if update_cmd := shutil.which("update-desktop-database"):
                        subprocess.run([update_cmd, app_desktop_dir], capture_output=True)

            GLib.idle_add(self._on_operation_complete, True, "Sucesso", "O FlatStore foi instalado/atualizado com sucesso!")

        except subprocess.CalledProcessError:
            GLib.idle_add(self._on_operation_complete, False, "Erro", "A operação foi cancelada ou a senha estava incorreta.")
        except Exception as e:
            GLib.idle_add(self._on_operation_complete, False, "Erro", f"Falha na instalação:\n{e}")

    # --- FLUXO DE DESINSTALAÇÃO (THREADED) ---
    def on_remove_clicked(self, widget):
        dialog = Gtk.MessageDialog(
            transient_for=self, flags=0, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO, text="Confirmar Desinstalação"
        )
        dialog.format_secondary_text("Deseja realmente remover o FlatStore do seu sistema?")
        response = dialog.run()
        dialog.destroy()

        if response != Gtk.ResponseType.YES:
            return

        self.set_ui_busy(True, "Preparando desinstalação...")
        threading.Thread(target=self._worker_uninstall, daemon=True).start()

    def _worker_uninstall(self):
        try:
            if self.is_installed_system:
                GLib.idle_add(self.status_label.set_text, "Removendo do sistema (Digite sua senha)...")
                cmd = """
                rm -rf /opt/flatstore ;
                rm -f /usr/local/bin/flatstore ;
                rm -f /usr/share/applications/flatstore.desktop
                """
                subprocess.run(['pkexec', 'bash', '-c', cmd], check=True)
                
                if shutil.which("update-desktop-database"):
                    subprocess.run(['pkexec', 'update-desktop-database', '/usr/share/applications'], capture_output=True)
            
            if self.is_installed_user:
                GLib.idle_add(self.status_label.set_text, "Removendo arquivos de usuário...")
                app_desktop_dir = os.path.expanduser("~/.local/share/applications")
                shutil.rmtree(os.path.expanduser("~/.local/share/flatstore"), ignore_errors=True)
                
                for file_path in [os.path.expanduser("~/.local/bin/flatstore"), os.path.join(app_desktop_dir, "flatstore.desktop")]:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

                if update_cmd := shutil.which("update-desktop-database"):
                    subprocess.run([update_cmd, app_desktop_dir], capture_output=True)

            GLib.idle_add(self._on_operation_complete, True, "Removido", "O FlatStore foi desinstalado com sucesso do sistema.")

        except subprocess.CalledProcessError:
            GLib.idle_add(self._on_operation_complete, False, "Erro", "Operação cancelada ou permissão negada.")
        except Exception as e:
            GLib.idle_add(self._on_operation_complete, False, "Erro ao desinstalar", f"Não foi possível remover completamente:\n{e}")

    def _on_operation_complete(self, success, title, message):
        """ Callback final chamado pela main thread do GTK após terminar """
        self.set_ui_busy(False, "Operação concluída." if success else "Falha na operação.")
        self.refresh_ui_state()
        mtype = Gtk.MessageType.INFO if success else Gtk.MessageType.ERROR
        self.show_msg(title, message, mtype)


if __name__ == "__main__":
    app = FlatStoreManager()
    app.connect("destroy", Gtk.main_quit)
    app.show_all()
    Gtk.main()
