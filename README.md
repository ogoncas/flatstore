# FlatStore

Um gerenciador gráfico de pacotes Flatpak rápido, leve e nativo, ideal para ambientes de desktop GTK, como o XFCE. Desenvolvido em Python 3 com PyGObject (GTK3), o FlatStore oferece uma interface limpa para buscar, instalar, atualizar e remover aplicativos, além de gerenciar repositórios (remotes) diretamente da interface.

![Badge de Licença](https://img.shields.io/badge/Licen%C3%A7a-Software%20Livre-blue.svg)
![Badge de Versão](https://img.shields.io/badge/Vers%C3%A3o-1.2-green.svg)
![Screenshot 1.1](https://github.com/ogoncas/flatstore/blob/main/screenshot_1.1.png)
# Funcionalidades

* **Busca Rápida:** Integração direta com a CLI do Flatpak com sistema de cache inteligente para não travar a interface.
* **Gerenciamento Completo:** Instale, remova e atualize aplicativos com um clique.
* **Controle de Escopo:** Escolha se deseja instalar aplicativos apenas para o seu usuário (sem precisar de senha) ou para todo o sistema (usando o `pkexec`).
* **Gestão de Repositórios (Remotes):** Adicione ou remova fontes de aplicativos (como o Flathub) de forma 100% visual.
* **Interface Assíncrona:** A UI não congela durante operações pesadas; você pode acompanhar o status diretamente no terminal ou pela barra de carregamento.

---

## Dependências e Pré-requisitos

O **FlatStore** depende do Python 3, das bibliotecas GTK3 (PyGObject) e, claro, do utilitário de linha de comando do próprio Flatpak. O `pkexec` (Polkit) também é necessário caso você queira gerenciar instalações a nível de sistema.

Abaixo, veja como instalar as dependências na sua distribuição favorita:

### Ubuntu, Linux Mint, Debian e derivados
```bash
sudo apt update
sudo apt install python3 python3-gi gir1.2-gtk-3.0 flatpak policykit-1

```

### Fedora

```bash
sudo dnf install python3 python3-gobject gtk3 flatpak polkit

```

### Arch Linux, Manjaro, EndeavourOS

```bash
sudo pacman -S python python-gobject gtk3 flatpak polkit

```

### openSUSE

```bash
sudo zypper install python3 python3-gobject gtk3 flatpak polkit

```

---

## Como Executar

Com as dependências instaladas, basta clonar o repositório e executar o script principal:

1. **Clone o repositório:**
```bash
git clone https://github.com/ogoncas/flatstore.git
cd flatstore

```


2. **Inicie o aplicativo:**
```bash
python3 app.py

```



> **Dica:** Como o programa faz uso do terminal para exibir logs detalhados, rodá-lo diretamente via linha de comando ajudará você a acompanhar o processo exato de download e extração dos pacotes Flatpak.

---

## Configurações e Cache

O FlatStore gera arquivos de cache e configuração localmente para acelerar as buscas. Eles ficam armazenados em:
`~/.cache/flatstore/`

Você pode limpar esse cache manualmente pelo menu de configurações do aplicativo ou configurar para que ele seja limpo automaticamente ao fechar o programa.

---

## Contribuições

Contribuições são sempre bem-vindas! Sinta-se à vontade para abrir uma *Issue* relatando bugs, sugerindo melhorias ou enviar um *Pull Request*.

## Licença

Este software é 100% livre. Você pode usar, distribuir e modificar à vontade.
