<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:0a0a1a,50:0d1b2a,100:1a1a2e&height=180&section=header&text=PyBox&fontSize=72&fontColor=4fc3f7&fontAlignY=38&desc=Python-native+Container+Runtime+from+scratch&descSize=18&descAlignY=62&animation=fadeIn" width="100%"/>

<br/>

[![Python](https://img.shields.io/badge/Python_3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Linux](https://img.shields.io/badge/Linux_5.4+-FCC624?style=for-the-badge&logo=linux&logoColor=black)](https://kernel.org)
[![OCI](https://img.shields.io/badge/OCI_Compatible-0DB7ED?style=for-the-badge&logo=docker&logoColor=white)](https://opencontainers.org)
[![cgroups v2](https://img.shields.io/badge/cgroups_v2-FF6B35?style=for-the-badge&logo=linux&logoColor=white)](https://kernel.org)
[![License](https://img.shields.io/badge/License-MIT-4fc3f7?style=for-the-badge)](./LICENSE)
[![Tests](https://img.shields.io/badge/Tests-158_passing-22c55e?style=for-the-badge&logo=pytest&logoColor=white)](./tests)

<br/>

> **PyBox** é um container runtime construído do zero em Python puro, usando diretamente as primitivas do kernel Linux que sustentam o Docker — namespaces, cgroups v2 e OverlayFS. Nada de Go, nada de binários opacos. Só Python falando com o kernel.

</div>

---

## 📋 Índice

- [Sobre o Projeto](#-sobre-o-projeto)
- [Funcionalidades](#-funcionalidades)
- [Arquitetura](#-arquitetura)
- [Como Funciona](#-como-funciona)
- [Tecnologias](#-tecnologias)
- [Estrutura do Projeto](#-estrutura-do-projeto)
- [Pré-requisitos](#-pré-requisitos)
- [Instalação](#-instalação)
- [Como Rodar](#-como-rodar)
- [boxfile.toml](#-boxfiletoml)
- [Variáveis de Ambiente](#-variáveis-de-ambiente)
- [Desenvolvimento](#-desenvolvimento)
- [Testes](#-testes)
- [Makefile — Comandos Úteis](#-makefile--comandos-úteis)
- [Daemon (pyboxd)](#-daemon-pyboxd)
- [Roadmap](#-roadmap)
- [Contribuindo](#-contribuindo)
- [Licença](#-licença)

---

## 💡 Sobre o Projeto

O **PyBox** nasceu de uma pergunta simples: *como um container realmente funciona?*

Em vez de usar bibliotecas de alto nível ou chamar `runc` como subprocess, o PyBox implementa cada peça do puzzle diretamente — desde a syscall `unshare(2)` que cria namespaces até o `pivot_root(2)` que troca o filesystem raiz do container. Cada linha de código é auditável, cada syscall é documentada.

O resultado é um container runtime completo e **OCI-compatível** que roda imagens do Docker Hub, constrói novas imagens via `boxfile.toml`, gerencia redes virtuais com NAT, e suporta containers rootless com user namespaces.

---

## ✨ Funcionalidades

- 🐳 **Container Runner** — pull de imagens OCI + isolamento completo via namespaces Linux
- 🏷️ **Nomes de Container** — `--name meu-app` para referenciar containers por nome em todos os comandos
- 🔄 **Ciclo de Vida Completo** — `run`, `start`, `stop`, `exec`, `rm` aceitam nome ou ID (inclusive prefixo)
- 🌙 **Modo Background** — `--detach / -d` inicia containers em background e retorna o ID imediatamente
- ⌨️ **Tab Completion** — autocompletar IDs e nomes de containers no shell (`--install-completion`)
- 🏗️ **Image Builder** — construção de imagens a partir de `boxfile.toml` com cache de layers
- 🌐 **Container Networking** — bridge virtual, veth pairs, IPAM e NAT com nftables
- 📡 **Daemon Persistente** — `pyboxd` com socket Unix, estado persistente e recuperação de crash
- 📜 **Log Streaming** — captura e streaming em tempo real de stdout/stderr
- 🔗 **Exec em Container** — entra em container em execução via `setns(2)`
- 🐍 **Python First-Class** — instala venv e `pip` nativamente como step de build
- 🔐 **Rootless Containers** — user namespaces, fuse-overlayfs e slirp4netns (sem root)
- 📦 **Registry Push/Pull** — push e pull OCI para Docker Hub, GHCR e registries privadas
- 🔒 **cgroups v2** — limites de CPU, memória, PIDs e I/O via unified hierarchy

---

## 🏗 Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│                     PyBox CLI (typer + rich)                     │
│  run │ start │ stop │ exec │ ps │ logs │ build │ push │ pull │ info  │
└──────────────────────────────┬──────────────────────────────────┘
                                │  IPC via Unix Socket (msgpack)
┌──────────────────────────────▼──────────────────────────────────┐
│                       pyboxd  (asyncio daemon)                   │
│                                                                  │
│  ┌──────────────┐  ┌────────────────┐  ┌─────────────────────┐  │
│  │  Container   │  │     Image      │  │      Network        │  │
│  │  Manager     │  │    Manager     │  │      Manager        │  │
│  │  lifecycle   │  │  pull / build  │  │  veth / bridge / NAT│  │
│  │  state FSM   │  │  layer cache   │  │  IPAM / nftables    │  │
│  └──────┬───────┘  └──────┬─────────┘  └──────────┬──────────┘  │
│         │                  │                       │              │
│  ┌──────▼──────────────────▼───────────────────────▼──────────┐  │
│  │                    Core Runtime Layer                        │  │
│  │                                                              │  │
│  │  ┌────────────┐   ┌────────────┐   ┌──────────────────┐    │  │
│  │  │ Namespace  │   │  cgroups   │   │  Storage Driver  │    │  │
│  │  │  Manager   │   │  Manager   │   │ OverlayFS / FUSE │    │  │
│  │  │ unshare()  │   │ /sys/fs/   │   │ layer diff/apply │    │  │
│  │  │ pivot_root │   │ cgroup/    │   │ snapshot / commit│    │  │
│  │  └────────────┘   └────────────┘   └──────────────────┘    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                                │
               ┌────────────────▼────────────────┐
               │         Linux Kernel             │
               │  namespaces · cgroups v2         │
               │  overlayfs · netfilter           │
               └─────────────────────────────────┘
```

### Estado do Container (Máquina de Estados)

```
CREATED ──► RUNNING ──► PAUSED
   │            │           │
   │            ▼           │
   │         STOPPED ◄──────┘
   │            │
   └────────────► REMOVED
```

---

## ⚙️ Como Funciona

O PyBox usa três primitivas do kernel Linux para criar isolamento completo:

<div align="center">

| Primitiva | Mecanismo | O que garante |
|-----------|-----------|---------------|
| **Namespaces** | `unshare(2)` via ctypes | Isolamento de PID, rede, filesystem, hostname, IPC |
| **cgroups v2** | `/sys/fs/cgroup/pybox/<id>/` | Limites de CPU, memória, PIDs, I/O |
| **OverlayFS** | `mount -t overlay` | Filesystem em camadas copy-on-write |

</div>

```
Image Layers (read-only)         Container Layer (read-write)
────────────────────────         ─────────────────────────────
layer-3 (app code)     ──┐
layer-2 (pip install)  ──┤──►  overlayfs mount ──► /container/rootfs
layer-1 (apt install)  ──┤        (merged view)
layer-0 (ubuntu base)  ──┘
                                  upper/  ← writes do container
                                  work/   ← overlayfs internal
```

---

## 🛠 Tecnologias

<div align="center">

| Camada | Tecnologia | Motivo |
|--------|-----------|--------|
| **CLI** | Typer + Rich | DX excelente, output rico no terminal |
| **Daemon** | asyncio + anyio | Servidor Unix socket assíncrono |
| **HTTP / Registry** | httpx | Cliente async, tipado, moderno |
| **Config / Validation** | Pydantic v2 + pydantic-settings | Parsing seguro e validado |
| **Syscalls** | ctypes (stdlib) | Zero dependências para operações core |
| **Serialização IPC** | msgpack | Protocolo binário rápido para daemon |
| **TOML (leitura)** | tomllib (stdlib 3.11+) | Zero dependências |
| **TOML (escrita)** | tomli-w | Geração de configurações |
| **Testes** | pytest + pytest-asyncio | Suite completa: unit, integration, e2e |
| **Linting** | ruff + mypy | Fast, strict, moderno |
| **Rede** | iproute2 + nftables | Bridge, veth pairs, NAT |
| **Rootless** | fuse-overlayfs + slirp4netns | Containers sem root |

</div>

---

## 📁 Estrutura do Projeto

```
pybox/
├── .github/
│   └── workflows/
│       └── ci.yml              # CI — lint, typecheck, unit tests, integration
├── pybox/
│   ├── __init__.py             # __version__ = "0.1.0"
│   ├── config.py               # PyBoxConfig via pydantic-settings
│   ├── exceptions.py           # hierarquia de exceções tipadas
│   │
│   ├── cli/                    # Interface de usuário
│   │   ├── main.py             # app Typer raiz (pybox)
│   │   ├── output.py           # rich formatters, tabelas, progress bars
│   │   └── commands/
│   │       ├── run.py          # pybox run
│   │       ├── build.py        # pybox build
│   │       ├── ps.py           # pybox ps
│   │       ├── logs.py         # pybox logs
│   │       ├── exec.py         # pybox exec
│   │       ├── stop.py         # pybox stop
│   │       ├── rm.py           # pybox rm
│   │       ├── images.py       # pybox images
│   │       ├── rmi.py          # pybox rmi
│   │       ├── pull.py         # pybox pull
│   │       ├── push.py         # pybox push
│   │       ├── login.py        # pybox login
│   │       ├── network.py      # pybox network ls/create/rm/inspect
│   │       └── info.py         # pybox info
│   │
│   ├── container/              # Ciclo de vida do container
│   │   ├── runtime.py          # ContainerManager: create/start/stop/remove
│   │   ├── state.py            # StateManager + ContainerState FSM
│   │   ├── config.py           # ContainerConfig (pydantic)
│   │   ├── init.py             # processo init (PID 1) — roda dentro do container
│   │   ├── exec.py             # exec via setns(2)
│   │   └── logs.py             # LogManager: captura e streaming
│   │
│   ├── namespace/              # Linux namespaces via ctypes
│   │   ├── constants.py        # CLONE_NEW* flags, SYS_PIVOT_ROOT
│   │   ├── unshare.py          # unshare() + sethostname()
│   │   ├── pivot_root.py       # pivot_root() syscall 155
│   │   └── user_map.py         # IDMapping, write_uid_map/gid_map
│   │
│   ├── cgroups/                # Resource limiting (cgroups v2)
│   │   ├── specs.py            # CgroupSpec com parsing de "256m", "0.5 CPU"
│   │   └── v2.py               # CgroupV2: create/delete/apply_limits/add_pid
│   │
│   ├── image/                  # Gerenciamento de imagens OCI
│   │   ├── manifest.py         # OciManifest, OciConfig, OciLayer (pydantic)
│   │   ├── puller.py           # ImagePuller: pull paralelo com semaphore
│   │   ├── layer.py            # extract_layer() com whiteout handling
│   │   ├── build_spec.py       # BuildSpec, RunStep, CopyStep, EnvStep
│   │   ├── parser.py           # parse_boxfile() via tomllib
│   │   ├── builder.py          # ImageBuilder com layer cache
│   │   ├── commit.py           # commit_image(): monta OCI manifest
│   │   ├── python_step.py      # PythonStepRunner: venv + pip install
│   │   └── tag.py              # TagManager: tag/resolve/untag
│   │
│   ├── storage/                # Filesystem driver
│   │   ├── overlay.py          # OverlayMount, prepare_container_overlay()
│   │   ├── layer_cache.py      # LayerCache: cache de layers por step hash
│   │   └── snapshot.py         # SnapshotManager: upper dir → layer tar
│   │
│   ├── network/                # Container networking
│   │   ├── models.py           # Network, NetworkEndpoint (pydantic)
│   │   ├── ipam.py             # IpamManager: pool CIDR com file lock
│   │   ├── bridge.py           # BridgeManager: pybox0 bridge interface
│   │   ├── veth.py             # VethManager: veth pairs
│   │   ├── nat.py              # NatManager: nftables MASQUERADE + DNAT
│   │   ├── dns.py              # write_resolv_conf()
│   │   └── manager.py          # NetworkManager: orquestra tudo
│   │
│   ├── registry/               # OCI Registry client
│   │   ├── auth.py             # DockerHubAuth: Bearer token + credenciais
│   │   ├── client.py           # RegistryClient: pull manifest + blobs
│   │   ├── push.py             # OciRegistryPusher: push layers + manifest
│   │   └── mirror.py           # RegistryMirror: cache local de blobs
│   │
│   └── rootless/               # Containers sem root
│       ├── __init__.py         # is_rootless(), get_subuid_range()
│       ├── user_ns.py          # setup_user_namespace() via newuidmap
│       ├── fuse_overlay.py     # FuseOverlayFSDriver
│       ├── slirp.py            # SlirpNetworkManager + port forwards
│       └── port_proxy.py       # PortProxy: TCP/UDP asyncio forward
│
├── daemon/
│   ├── protocol.py             # Method enum, DaemonRequest/Response, msgpack wire
│   ├── client.py               # DaemonClient: connect/call/stream
│   └── main.py                 # PyBoxDaemon: asyncio Unix socket server
│
├── tests/
│   ├── unit/                   # 156 testes, sem root necessário
│   │   ├── test_namespace.py
│   │   ├── test_cgroups.py
│   │   ├── test_image.py
│   │   ├── test_parser.py
│   │   ├── test_builder.py
│   │   ├── test_ipam.py
│   │   ├── test_network_models.py
│   │   ├── test_daemon_protocol.py
│   │   ├── test_logs.py
│   │   ├── test_rootless.py
│   │   └── test_push.py
│   └── integration/            # 2 testes (daemon real em socket temporário)
│       └── test_daemon.py
│
├── scripts/
│   ├── install-dev.sh          # setup completo do ambiente de dev
│   └── pyboxd.service          # systemd unit para o daemon
├── boxfile.toml                # exemplo completo de image definition
├── Makefile                    # comandos automatizados
└── pyproject.toml              # build config, dependências, entry points
```

---

## 📦 Pré-requisitos

### Sistema Operacional

- **Linux** com kernel **5.4+** (Ubuntu 22.04+, Debian 11+, Fedora 35+)
- cgroups v2 habilitado: `/sys/fs/cgroup/cgroup.controllers` deve existir
- OverlayFS disponível: `grep overlay /proc/filesystems` deve retornar resultado

### Verificação rápida

```bash
# Verificar kernel
uname -r   # >= 5.4

# Verificar cgroups v2
cat /sys/fs/cgroup/cgroup.controllers
# Deve mostrar: cpuset cpu io memory hugetlb pids rdma

# Verificar OverlayFS
grep overlay /proc/filesystems
# Deve mostrar: nodev overlay
```

### Dependências do Sistema (para funcionalidades completas)

| Pacote | Para que serve | Obrigatório |
|--------|---------------|-------------|
| `iproute2` | Gerenciamento de interfaces de rede | Networking |
| `nftables` | NAT e firewall para containers | Networking |
| `fuse-overlayfs` | OverlayFS sem root | Rootless |
| `slirp4netns` | Networking sem root | Rootless |
| `uidmap` | `newuidmap`/`newgidmap` para user namespaces | Rootless |

```bash
# Ubuntu / Debian
sudo apt-get install -y iproute2 nftables fuse-overlayfs slirp4netns uidmap

# Fedora / RHEL
sudo dnf install -y iproute nftables fuse-overlayfs slirp4netns shadow-utils
```

### Python

- **Python 3.11+** (usa `tomllib` da stdlib)

```bash
python3 --version   # >= 3.11
```

---

## 🚀 Instalação

### Instalação Rápida (desenvolvimento)

```bash
# 1. Clone o repositório
git clone https://github.com/br00tm/pybox.git
cd pybox

# 2. Instale o pacote em modo editável com dependências de dev
pip install -e ".[dev]"

# 3. Verifique a instalação
pybox --version
```

### Instalação Completa (com dependências do sistema)

```bash
# Setup automático — instala deps do sistema, configura subuid/subgid,
# cria diretórios de storage e instala o pacote Python
sudo ./scripts/install-dev.sh
```

O script `install-dev.sh` faz:
- Instala `fuse-overlayfs`, `slirp4netns`, `uidmap`, `nftables`, `iproute2`
- Adiciona entradas em `/etc/subuid` e `/etc/subgid` para o seu usuário
- Cria `/var/lib/pybox/{images,containers,volumes,networks,cache}`
- Instala o pacote Python com `pip install -e ".[dev]"`
- Habilita `loginctl enable-linger` para serviços de usuário

---

## 🎯 Como Rodar

### Containers

#### Rodar um container interativo

```bash
# Container Ubuntu básico
pybox run --image ubuntu:24.04 -- /bin/bash

# Com limites de recursos
pybox run --image ubuntu:24.04 --memory 256m --cpu 0.5 -- /bin/bash

# Com variáveis de ambiente
pybox run --image ubuntu:24.04 -e FOO=bar -e DEBUG=1 -- /bin/bash

# Com bind mount (volume)
pybox run --image ubuntu:24.04 -v /host/path:/container/path -- /bin/bash

# Remover automaticamente ao sair
pybox run --image ubuntu:24.04 --rm -- /bin/bash

# Modo de rede (bridge padrão ou none)
pybox run --image ubuntu:24.04 --network bridge -- /bin/bash
```

#### Listar containers

```bash
# Containers em execução
pybox ps

# Todos os containers (incluindo parados)
pybox ps --all
```

#### Logs de um container

```bash
# Últimas 50 linhas
pybox logs <container-id>

# Seguir logs em tempo real
pybox logs --follow <container-id>

# Últimas N linhas
pybox logs --tail 100 <container-id>
```

#### Executar comando em container em execução

```bash
pybox exec <container-id> -- /bin/sh
pybox exec <container-id> -- bash -c "echo hello"
```

#### Parar e remover containers

```bash
# Parar (SIGTERM → SIGKILL após timeout)
pybox stop <container-id>

# Remover container parado
pybox rm <container-id>

# Parar e remover
pybox stop <container-id> && pybox rm <container-id>
```

---

### Imagens

#### Pull de imagem do registry

```bash
# Docker Hub (padrão)
pybox pull ubuntu:24.04
pybox pull python:3.12-slim
pybox pull nginx:alpine

# Registry privada
pybox pull registry.example.com/myapp:v1
```

#### Listar imagens locais

```bash
pybox images
```

#### Remover imagem

```bash
pybox rmi ubuntu:24.04
```

#### Login em registry

```bash
# Docker Hub
pybox login

# Registry específica
pybox login registry.example.com

# Com credenciais diretas
pybox login -u myuser -p mytoken registry.example.com
```

#### Push de imagem

```bash
pybox push myapp:v1
pybox push registry.example.com/myapp:v1
```

---

### Build de Imagem

#### Construir a partir de um boxfile.toml

```bash
# Usando o boxfile.toml do diretório atual
pybox build -t myapp:v1

# Especificando o arquivo
pybox build -f /path/to/boxfile.toml -t myapp:v1

# Forçar rebuild sem cache
pybox build -f boxfile.toml -t myapp:v1 --no-cache
```

---

### Redes

```bash
# Listar redes
pybox network ls

# Inspecionar rede
pybox network inspect <network-name>

# Criar rede customizada
pybox network create --cidr 10.100.0.0/24 mynet

# Remover rede
pybox network rm mynet
```

---

### Informações do Sistema

```bash
# Versão, modo rootless, storage driver, kernel, disk usage
pybox info
```

---

## 📄 boxfile.toml

O `boxfile.toml` é o equivalente PyBox do `Dockerfile` — mas com sintaxe TOML moderna e suporte nativo a Python.

```toml
# boxfile.toml — definição completa de uma imagem PyBox

[box]
name    = "my-app"
version = "1.0.0"
base    = "ubuntu:24.04"

[box.meta]
author      = "Pedro Lucas"
description = "Exemplo de imagem PyBox"
labels      = { env = "production", team = "backend" }

# ── Python first-class ──────────────────────────────────────────
# PyBox instala o venv e as dependências automaticamente,
# com cache inteligente baseado no hash dos pacotes.
[python]
version  = "3.12"
packages = [
  "fastapi==0.111.0",
  "uvicorn[standard]==0.30.0",
  "httpx>=0.27",
]

# ── Steps de build (executados em ordem) ────────────────────────
[[steps]]
name = "instalar dependências do sistema"
run  = "apt-get update && apt-get install -y curl"

[[steps]]
name = "copiar código fonte"
copy = { src = "./src", dst = "/app" }

[[steps]]
name = "definir workdir"
workdir = "/app"

[[steps]]
name = "variáveis de build"
env = { BUILD_ENV = "production" }

# ── Configuração de runtime ──────────────────────────────────────
[run]
cmd    = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
expose = [8000]
env    = { PYTHONUNBUFFERED = "1", LOG_LEVEL = "info" }
user   = "appuser"

# ── Limites de recursos (cgroups v2) ────────────────────────────
[limits]
memory = "256m"    # 256 MB de RAM
cpu    = 0.5       # 50% de um core
pids   = 100       # máximo 100 processos

# ── Healthcheck ──────────────────────────────────────────────────
[healthcheck]
cmd      = ["curl", "-f", "http://localhost:8000/health"]
interval = "30s"
timeout  = "5s"
retries  = 3
```

### Tipos de Steps

| Step | Campo | Exemplo |
|------|-------|---------|
| **Shell command** | `run` | `run = "apt-get install -y curl"` |
| **Copiar arquivos** | `copy` | `copy = { src = "./src", dst = "/app" }` |
| **Workdir** | `workdir` | `workdir = "/app"` |
| **Variáveis** | `env` | `env = { FOO = "bar" }` |

### Cache de Layers

O PyBox usa cache automático por step — idêntico ao Docker layer cache. Cada step tem um `cache_key` calculado como `sha256(step_content + parent_digest)`. Se o step e todos os anteriores não mudaram, o PyBox reutiliza a layer em cache e pula a execução.

```bash
# Rebuild forçado (sem cache)
pybox build -f boxfile.toml -t myapp:v1 --no-cache
```

---

## 🔧 Variáveis de Ambiente

| Variável | Descrição | Padrão |
|----------|-----------|--------|
| `PYBOX_ROOT` | Diretório raiz de storage | `/var/lib/pybox` |
| `PYBOX_CGROUP_ROOT` | Raiz do cgroup do PyBox | `/sys/fs/cgroup/pybox` |
| `PYBOX_SOCKET` | Path do socket Unix do daemon | `/run/pybox/pyboxd.sock` |
| `PYBOX_LOG_LEVEL` | Nível de log (`DEBUG`, `INFO`, `WARNING`) | `INFO` |
| `DOCKER_CONFIG` | Diretório das credenciais de registry | `~/.docker` |

---

## 👨‍💻 Desenvolvimento

### Setup do ambiente

```bash
# Clone e instale em modo editável
git clone https://github.com/br00tm/pybox.git
cd pybox
pip install -e ".[dev]"
```

### Estrutura de diretórios de storage (criada automaticamente)

```
/var/lib/pybox/
├── images/         # imagens OCI armazenadas por digest
├── containers/     # estado e rootfs dos containers
├── volumes/        # volumes persistentes
├── networks/       # estado das redes (ipam.json, etc.)
└── cache/
    └── layers/     # cache de layers de build por step hash
```

### Rodar com modo debug

```bash
# Ativa logging detalhado (syscalls, mounts, cgroup writes)
pybox --debug run --image ubuntu:24.04 -- /bin/bash
```

---

## 🧪 Testes

### Testes unitários (sem root)

```bash
# Rodar todos os testes unitários
pytest tests/unit/ -v

# Com cobertura de código
pytest tests/unit/ --cov=pybox --cov-report=term-missing

# Parar no primeiro erro
pytest tests/unit/ -x
```

### Testes de integração (requerem root)

Os testes de integração iniciam um `PyBoxDaemon` real em um socket temporário e testam o protocolo IPC completo.

```bash
# Com sudo
sudo python3 -m pytest tests/integration/ -v
```

### Testes end-to-end (requerem root + rede)

```bash
sudo python3 -m pytest tests/e2e/ -v
```

### Suite completa

```bash
# Unit + Integration
make test-all

# Apenas unit (CI-friendly, sem root)
make test-unit
```

### Status atual dos testes

```
tests/unit/       → 156 passed ✅
tests/integration/ →   2 passed ✅
```

---

## 🔨 Makefile — Comandos Úteis

```bash
make install-dev    # Instala o pacote em modo editável com deps de dev
make test-unit      # Roda testes unitários (sem root)
make test-integration  # Roda testes de integração (requer root)
make test-e2e       # Roda testes end-to-end (requer root + rede)
make test-all       # unit + integration
make lint           # Roda ruff (linter)
make lint-fix       # Roda ruff com auto-fix
make typecheck      # Roda mypy (type checker)
make format         # Formata código com ruff format
make run-daemon     # Inicia pyboxd em foreground
make build-example  # Constrói a imagem de exemplo do boxfile.toml
make clean          # Remove __pycache__, .mypy_cache, .ruff_cache, *.pyc
make clean-data     # ⚠️  Remove todos os dados em /var/lib/pybox
```

---

## 🔌 Daemon (pyboxd)

O `pyboxd` é o daemon central do PyBox — um servidor asyncio que escuta em um socket Unix e processa comandos da CLI.

### Iniciar o daemon

```bash
# Iniciar em foreground
pyboxd

# Iniciar como serviço systemd (após install-dev.sh)
systemctl --user start pyboxd
systemctl --user enable pyboxd   # iniciar no boot

# Ver logs do daemon
journalctl --user -u pyboxd -f
```

### Protocolo IPC

O daemon usa **msgpack** com prefixo de 4 bytes big-endian para framing:

```
[4 bytes: length][msgpack payload]
```

Métodos suportados:

| Método | Descrição |
|--------|-----------|
| `PS` | Lista containers |
| `INSPECT` | Detalhes de um container |
| `STOP` | Para um container |
| `RM` | Remove um container |
| `LOGS` | Stream de logs (resposta múltipla) |
| `EXEC` | Executa comando em container |
| `INFO` | Versão e informações do sistema |
| `IMAGES` | Lista imagens locais |

### Recuperação de crash

Na inicialização, o `pyboxd` verifica todos os containers com estado `RUNNING` e os marca como `STOPPED` caso o processo não esteja mais em execução — garantindo que o estado persista corretamente entre reinicializações.

---

## 🗺 Roadmap

- [x] **Fase 1** — Container Runner MVP (`pybox run --image ubuntu:24.04 -- /bin/bash`)
- [x] **Fase 2** — Image Builder (`pybox build -f boxfile.toml -t app:v1`)
- [x] **Fase 3** — Container Networking (bridge, veth, IPAM, NAT, DNS)
- [x] **Fase 4** — Daemon (`pyboxd`) + `ps`, `logs`, `exec`, `stop`, `rm`
- [x] **Fase 5** — OCI Registry Push/Pull + `pybox login`, `push`, `images`
- [x] **Fase 6** — Rootless Containers (user namespaces, fuse-overlayfs, slirp4netns)
- [ ] **Fase 7** — `pybox compose` — múltiplos containers declarativos
- [ ] **Fase 8** — Prometheus metrics endpoint no daemon
- [ ] **Fase 9** — SQLite para estado persistente (substituir JSON)
- [ ] **Fase 10** — Plugin system via Python entry points

---

## 🤝 Contribuindo

1. Faça um fork do repositório
2. Crie sua branch de feature

   ```bash
   git checkout -b feature/minha-feature
   ```

3. Commit suas alterações

   ```bash
   git commit -m "feat: adiciona minha feature"
   ```

4. Garanta que os testes passam

   ```bash
   make lint && make test-unit
   ```

5. Push para a branch

   ```bash
   git push origin feature/minha-feature
   ```

6. Abra um **Pull Request**

### Diretrizes

- Testes unitários são obrigatórios para novos módulos
- Testes não devem precisar de root (use mocks para syscalls)
- Siga o estilo existente: ruff + mypy strict
- Docstrings em inglês, comentários em português são bem-vindos

---

## 📄 Licença

Este projeto está licenciado sob a **MIT License** — consulte o arquivo [LICENSE](./LICENSE) para mais detalhes.

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a1a2e,50:0d1b2a,100:0a0a1a&height=80&section=footer" width="100%"/>

Feito com ❤️ por [**br00tm**](https://github.com/br00tm)

*"The best way to understand containers is to build one."*

</div>
