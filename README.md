# FlatStore

Um gerenciador gráfico de pacotes Flatpak rápido, leve e nativo, ideal para ambientes de desktop GTK, como o XFCE. Desenvolvido em Python 3 com PyGObject (GTK3), o FlatStore oferece uma interface limpa para buscar, instalar, atualizar e remover aplicativos, além de gerenciar repositórios (remotes) diretamente da interface.

## Novidades da Versão 1.3.2

### 1. Organização de Importações e Constantes

* **Adição de Bibliotecas para Otimização:** Foram incluídas importações necessárias para habilitar o paralelismo (processamento concorrente ou assíncrono).
* **Validação de URL:** Adicionadas estruturas e validações direcionadas a URLs para garantir a integridade dos dados recebidos.

---

### 2. Centralização de Caminhos e Inicialização

* **Resolução de Caminhos Absolutos:** Os caminhos para ferramentas como `flatpak` e `pkexec` foram resolvidos de forma centralizada e padronizada, evitando falhas de execução dependentes do ambiente.
* **Cache de Ícones:** Foi adicionado o cache de ícones associados ao `app_id` diretamente no estado da aplicação, otimizando o desempenho visual e evitando requisições desnecessárias.

---

### 3. Blindagem de Execução de Comandos (Segurança)

* **Prevenção de Injeção de Argumentos:** O código foi alterado para utilizar binários resolvidos em vez de chamadas genéricas, blindando a aplicação contra ataques de injeção de argumentos.
* **Uso de Caminhos Absolutos:** Na rotina de leitura (como a leitura do Flatpak), passou a ser utilizado o caminho absoluto correspondente, garantindo que o programa execute exatamente o binário esperado.

---

### 4. Validação de Dados e Configurações

* **Validação de Tipos:** Identificou-se a necessidade de validar tipos booleanos em verificações numéricas, prevenindo comportamentos inesperados causados por conversões incorretas de dados.
* **Carregamento de Configurações (JSON):** A função de carregamento de configuração recebeu validações e limites para os valores carregados via arquivo JSON, evitando falhas ou comparações de tipos incorretos.
* **Restrição de Permissões:** Foram aplicadas restrições de permissões nos diretórios e arquivos de cache, garantindo que apenas o próprio usuário autorizado tenha acesso a esses dados sensíveis.

---

## Funcionalidades

* **Busca Rápida e Otimizada:** Integração direta com a CLI do Flatpak aliada a um sistema de cache inteligente para evitar o travamento da interface.
* **Gerenciamento Completo:** Instale, remova e atualize aplicativos com um clique.
* **Controle de Escopo:** Escolha se deseja instalar aplicativos apenas para o seu usuário (sem precisar de senha) ou para todo o sistema (usando o `pkexec`).
* **Gestão de Repositórios (Remotes):** Adicione ou remova fontes de aplicativos (como o Flathub) de forma 100% visual.
* **Interface Assíncrona:** A UI não congela durante operações pesadas; você pode acompanhar o status via terminal, log centralizado ou pela barra de status no aplicativo.
* **Estilização Nativa:** CSS ajustado para harmonizar perfeitamente com temas GTK populares, como o tema Arc e ícones Papirus.

---

## Dependências e Pré-requisitos

O **FlatStore** depende do Python 3, das bibliotecas GTK3 (PyGObject) e do utilitário de linha de comando do Flatpak. O `pkexec` (Polkit) também é necessário caso você queira gerenciar instalações em nível de sistema.

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

### Método 1: A partir do instalador (Escrito em Python)

1. **Clone o repositório:**

```bash
git clone https://github.com/ogoncas/flatstore.git
cd flatstore

```

2. **Inicie o instalador:**

```bash
python3 installer.py

```

> **Dica:** Você também pode abrir arquivos de instalação diretamente pelo terminal executando: `flatstore arquivo.flatpakref`

---

### Método 2: Executando via AppImage (Portátil)

O **AppImage** empacota a aplicação num único ficheiro executável, funcionando de forma independente e mantendo acesso direto aos comandos do Flatpak do seu sistema.

#### Dependência do AppImage (FUSE)

Para que os AppImages se montem automaticamente no Linux, o sistema precisa de suporte a FUSE. Em distribuições modernas, caso ocorra algum aviso relacionado ao `libfuse.so.2`, você pode extrair o pacote diretamente sem dependências adicionais.

#### Como rodar o AppImage:

1. **Dê permissão de execução ao ficheiro:**

```bash
chmod +x FlatStore-x86_64.AppImage

```

2. **Execute o aplicativo:**

```bash
./FlatStore-x86_64.AppImage

```

> **Alternativa sem FUSE:** Se o seu sistema não possuir o suporte a FUSE nativo, você pode extrair o conteúdo do AppImage e executá-lo diretamente:
> ```bash
> ./FlatStore-x86_64.AppImage --appimage-extract
> ./squashfs-root/AppRun
> 
> ```
> 
> 

> **Acompanhando Logs:** Como o programa faz uso do terminal para exibir logs detalhados via biblioteca `logging`, rodá-lo pela linha de comando ajuda a acompanhar o progresso exato de downloads e atualizações do Flatpak.

---

## Configurações e Cache

O FlatStore gera arquivos de cache e configuração localmente para acelerar buscas e navegação. Eles são armazenados em:
`~/.cache/flatstore/`

* **Menu de Configurações:** Acesse as opções diretamente na barra superior para alterar o tempo de vida útil do cache ou forçar uma exclusão.
* **Sincronização:** Utilize o botão "Sincronizar" na interface para forçar a limpeza do cache e atualizar a lista de metadados dos repositórios instantaneamente.

---

## Contribuições

Contribuições são sempre bem-vindas! Sinta-se à vontade para abrir uma *Issue* relatando bugs, sugerindo melhorias ou enviando um *Pull Request*.

## 📄 Licença

Este software é **100% livre**. Você pode usar, distribuir e modificar à vontade.
