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

> **PyBox** is a container runtime built from scratch in pure Python, talking directly to the Linux kernel primitives that power Docker — namespaces, cgroups v2, and OverlayFS. No Go, no opaque binaries. Just Python speaking to the kernel.

</div>

---

## Table of Contents

- [About](#-about)
- [Features](#-features)
- [Architecture](#-architecture)
- [How It Works](#-how-it-works)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Usage](#-usage)
- [Command Reference](#-command-reference)
- [boxfile.toml](#-boxfiletoml)
- [Environment Variables](#-environment-variables)
- [Development](#-development)
- [Testing](#-testing)
- [Makefile](#-makefile)
- [Daemon (pyboxd)](#-daemon-pyboxd)
- [Roadmap](#-roadmap)
- [Contributing](#-contributing)
- [License](#-license)

---

## About

**PyBox** started with a simple question: *how does a container actually work?*

Instead of using high-level libraries or shelling out to `runc`, PyBox implements every piece of the puzzle directly — from the `unshare(2)` syscall that creates namespaces to the `pivot_root(2)` that switches the container's filesystem root. Every line of code is auditable, every syscall is documented.

The result is a complete, **OCI-compatible** container runtime that runs images from Docker Hub, builds new images via `boxfile.toml`, manages virtual networks with NAT, and supports rootless containers via user namespaces.

---

## Features

- **Container Runner** — OCI image pull + full isolation via Linux namespaces
- **Container Names** — `--name my-app` to reference containers by name in all commands
- **Full Lifecycle** — `run`, `start`, `stop`, `exec`, `rm` accept name, full ID, or ID prefix
- **Background Mode** — `--detach / -d` starts containers in the background and returns the ID immediately
- **Tab Completion** — autocomplete container IDs and names in the shell (`--install-completion`)
- **Image Builder** — build images from `boxfile.toml` with per-step layer caching
- **Container Networking** — virtual bridge, veth pairs, IPAM, and NAT via nftables
- **Persistent Daemon** — `pyboxd` with Unix socket, persistent state, and crash recovery
- **Log Streaming** — real-time capture and streaming of container stdout/stderr
- **Exec into Container** — run commands in a running container via `setns(2)`
- **Python First-Class** — installs venv and pip natively as a build step
- **Rootless Containers** — user namespaces, fuse-overlayfs, and slirp4netns (no root required)
- **Registry Push/Pull** — push and pull OCI images to/from Docker Hub, GHCR, and private registries
- **cgroups v2** — CPU, memory, PID, and I/O limits via unified hierarchy
- **Populated /dev** — essential devices (`null`, `zero`, `urandom`, `tty`, etc.) bind-mounted from the host so programs like `apt-get` and `sudo` work correctly

---

## Architecture

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

### Container State Machine

```
CREATED ──► RUNNING ──► PAUSED
   │            │           │
   │            ▼           │
   │         STOPPED ◄──────┘
   │            │
   └────────────► REMOVED
```

---

## How It Works

PyBox uses three Linux kernel primitives to create full isolation:

<div align="center">

| Primitive | Mechanism | What it isolates |
|-----------|-----------|-----------------|
| **Namespaces** | `unshare(2)` via ctypes | PID, network, filesystem, hostname, IPC |
| **cgroups v2** | `/sys/fs/cgroup/pybox/<id>/` | CPU, memory, PIDs, I/O |
| **OverlayFS** | `mount -t overlay` | Copy-on-write layered filesystem |

</div>

```
Image Layers (read-only)         Container Layer (read-write)
────────────────────────         ─────────────────────────────
layer-3 (app code)     ──┐
layer-2 (pip install)  ──┤──►  overlayfs mount ──► /container/rootfs
layer-1 (apt install)  ──┤        (merged view)
layer-0 (ubuntu base)  ──┘
                                  upper/  ← container writes
                                  work/   ← overlayfs internal
```

### Init sequence (inside the container)

```
fork()
  └─ child: unshare(CLONE_NEWUSER)
       ├─ signal parent → parent writes uid/gid maps
       ├─ unshare(CLONE_NEWNS | CLONE_NEWPID | CLONE_NEWNET | CLONE_NEWUTS | CLONE_NEWIPC)
       └─ fork()  ← child becomes PID 1 of the new PID namespace
            ├─ mount proc/dev/sys/tmp at rootfs/* (before pivot_root)
            ├─ bind-mount /dev/null, /dev/urandom, /dev/tty, ... from host
            ├─ pivot_root(new_root, old_root) → unmount old_root
            ├─ sethostname()
            └─ execvpe(cmd)  ← never returns
```

---

## Tech Stack

<div align="center">

| Layer | Technology | Why |
|-------|-----------|-----|
| **CLI** | Typer + Rich | Great DX, rich terminal output |
| **Daemon** | asyncio + anyio | Async Unix socket server |
| **HTTP / Registry** | httpx | Async, typed, modern HTTP client |
| **Config / Validation** | Pydantic v2 + pydantic-settings | Safe, validated parsing |
| **Syscalls** | ctypes (stdlib) | Zero dependencies for core operations |
| **IPC Serialization** | msgpack | Fast binary protocol for daemon |
| **TOML (read)** | tomllib (stdlib 3.11+) | Zero dependencies |
| **TOML (write)** | tomli-w | Config generation |
| **Tests** | pytest + pytest-asyncio | Full suite: unit, integration, e2e |
| **Linting** | ruff + mypy | Fast, strict, modern |
| **Networking** | iproute2 + nftables | Bridge, veth pairs, NAT |
| **Rootless** | fuse-overlayfs + slirp4netns | Containers without root |

</div>

---

## Project Structure

```
pybox/
├── .github/
│   └── workflows/
│       └── ci.yml              # CI — lint, typecheck, unit tests, integration
├── pybox/
│   ├── __init__.py             # __version__ = "0.1.0"
│   ├── config.py               # PyBoxConfig via pydantic-settings
│   ├── exceptions.py           # typed exception hierarchy
│   │
│   ├── cli/                    # User interface
│   │   ├── main.py             # root Typer app (pybox)
│   │   ├── output.py           # rich formatters, tables, progress bars
│   │   ├── completion.py       # tab completion (IDs, names, images)
│   │   └── commands/
│   │       ├── run.py          # pybox run  (--name, --detach/-d, tab complete)
│   │       ├── start.py        # pybox start (by name or ID, always background)
│   │       ├── build.py        # pybox build
│   │       ├── ps.py           # pybox ps
│   │       ├── logs.py         # pybox logs (tab complete by name)
│   │       ├── exec.py         # pybox exec (resolves name → ID)
│   │       ├── stop.py         # pybox stop (by name or ID, tab complete)
│   │       ├── rm.py           # pybox rm   (by name or ID, tab complete)
│   │       ├── images.py       # pybox images
│   │       ├── rmi.py          # pybox rmi
│   │       ├── pull.py         # pybox pull
│   │       ├── push.py         # pybox push
│   │       ├── login.py        # pybox login
│   │       ├── network.py      # pybox network ls/create/rm/inspect
│   │       └── info.py         # pybox info
│   │
│   ├── container/              # Container lifecycle
│   │   ├── runtime.py          # ContainerManager: create/start/stop/remove
│   │   ├── state.py            # StateManager + ContainerState FSM
│   │   ├── config.py           # ContainerConfig (pydantic)
│   │   ├── init.py             # init process (PID 1) — runs inside the container
│   │   ├── exec.py             # exec via setns(2)
│   │   └── logs.py             # LogManager: capture and streaming
│   │
│   ├── namespace/              # Linux namespaces via ctypes
│   │   ├── constants.py        # CLONE_NEW* flags, SYS_PIVOT_ROOT
│   │   ├── unshare.py          # unshare() + sethostname()
│   │   ├── pivot_root.py       # pivot_root() syscall 155
│   │   └── user_map.py         # IDMapping, write_uid_map/gid_map
│   │
│   ├── cgroups/                # Resource limiting (cgroups v2)
│   │   ├── specs.py            # CgroupSpec with parsing of "256m", "0.5 CPU"
│   │   └── v2.py               # CgroupV2: create/delete/apply_limits/add_pid
│   │
│   ├── image/                  # OCI image management
│   │   ├── manifest.py         # OciManifest, OciConfig, OciLayer (pydantic)
│   │   ├── puller.py           # ImagePuller: parallel pull with semaphore
│   │   ├── layer.py            # extract_layer() with whiteout handling
│   │   ├── build_spec.py       # BuildSpec, RunStep, CopyStep, EnvStep
│   │   ├── parser.py           # parse_boxfile() via tomllib
│   │   ├── builder.py          # ImageBuilder with layer cache
│   │   ├── commit.py           # commit_image(): assembles OCI manifest
│   │   ├── python_step.py      # PythonStepRunner: venv + pip install
│   │   └── tag.py              # TagManager: tag/resolve/untag
│   │
│   ├── storage/                # Filesystem driver
│   │   ├── overlay.py          # OverlayMount, prepare_container_overlay()
│   │   ├── layer_cache.py      # LayerCache: per-step hash cache
│   │   └── snapshot.py         # SnapshotManager: upper dir → layer tar
│   │
│   ├── network/                # Container networking
│   │   ├── models.py           # Network, NetworkEndpoint (pydantic)
│   │   ├── ipam.py             # IpamManager: CIDR pool with file lock
│   │   ├── bridge.py           # BridgeManager: pybox0 bridge interface
│   │   ├── veth.py             # VethManager: veth pairs
│   │   ├── nat.py              # NatManager: nftables MASQUERADE + DNAT
│   │   ├── dns.py              # write_resolv_conf()
│   │   └── manager.py          # NetworkManager: orchestrates everything
│   │
│   ├── registry/               # OCI Registry client
│   │   ├── auth.py             # DockerHubAuth: Bearer token + credentials
│   │   ├── client.py           # RegistryClient: pull manifest + blobs
│   │   ├── push.py             # OciRegistryPusher: push layers + manifest
│   │   └── mirror.py           # RegistryMirror: local blob cache
│   │
│   └── rootless/               # Rootless containers
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
│   ├── unit/                   # 158 tests, no root required
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
│   └── integration/            # 2 tests (real daemon on a temp socket)
│       └── test_daemon.py
│
├── scripts/
│   ├── install-dev.sh          # full dev environment setup
│   └── pyboxd.service          # systemd unit for the daemon
├── boxfile.toml                # complete image definition example
├── Makefile                    # automation commands
└── pyproject.toml              # build config, dependencies, entry points
```

---

## Prerequisites

### Operating System

- **Linux** with kernel **5.4+** (Ubuntu 22.04+, Debian 11+, Fedora 35+)
- cgroups v2 enabled: `/sys/fs/cgroup/cgroup.controllers` must exist
- OverlayFS available: `grep overlay /proc/filesystems` must return a result

### Quick check

```bash
# Check kernel version
uname -r   # >= 5.4

# Check cgroups v2
cat /sys/fs/cgroup/cgroup.controllers
# Expected: cpuset cpu io memory hugetlb pids rdma

# Check OverlayFS
grep overlay /proc/filesystems
# Expected: nodev overlay
```

### System Dependencies (for full functionality)

| Package | Purpose | Required for |
|---------|---------|-------------|
| `iproute2` | Network interface management | Networking |
| `nftables` | NAT and firewall for containers | Networking |
| `fuse-overlayfs` | Rootless OverlayFS | Rootless |
| `slirp4netns` | Rootless networking | Rootless |
| `uidmap` | `newuidmap`/`newgidmap` for user namespaces | Rootless |

```bash
# Ubuntu / Debian
sudo apt-get install -y iproute2 nftables fuse-overlayfs slirp4netns uidmap

# Fedora / RHEL
sudo dnf install -y iproute nftables fuse-overlayfs slirp4netns shadow-utils
```

### Python

- **Python 3.11+** (uses `tomllib` from stdlib)

```bash
python3 --version   # >= 3.11
```

---

## Installation

### Quick install (development)

```bash
# 1. Clone the repository
git clone https://github.com/br00tm/pybox.git
cd pybox

# 2. Install in editable mode with dev dependencies
pip install -e ".[dev]"

# 3. Verify
pybox --version
```

### Full install (with system dependencies)

```bash
# Automated setup — installs system deps, configures subuid/subgid,
# creates storage directories, and installs the Python package
sudo ./scripts/install-dev.sh
```

`install-dev.sh` does the following:
- Installs `fuse-overlayfs`, `slirp4netns`, `uidmap`, `nftables`, `iproute2`
- Adds entries to `/etc/subuid` and `/etc/subgid` for your user
- Creates `/var/lib/pybox/{images,containers,volumes,networks,cache}`
- Installs the Python package with `pip install -e ".[dev]"`
- Enables `loginctl enable-linger` for user services

---

## Usage

### Tab Completion

```bash
# Install completion for bash (once)
pybox --install-completion bash

# Restart the terminal, then Tab works:
pybox stop [Tab]        # lists running containers: my-app  a1b2c3d4e5f6
pybox exec [Tab]        # lists running containers
pybox rm [Tab]          # lists all containers
pybox run --image [Tab] # lists local images
```

---

### Containers

#### Run a container (foreground)

```bash
# Basic Ubuntu container
pybox run --image ubuntu:24.04 -- /bin/bash

# With a name for easy reference
pybox run --image ubuntu:24.04 --name my-app -- /bin/bash

# With resource limits
pybox run --image ubuntu:24.04 --memory 256m --cpu 0.5 -- /bin/bash

# With environment variables
pybox run --image ubuntu:24.04 -e FOO=bar -e DEBUG=1 -- /bin/bash

# With a bind mount (volume)
pybox run --image ubuntu:24.04 -v /host/path:/container/path -- /bin/bash

# Remove automatically on exit
pybox run --image ubuntu:24.04 --rm -- /bin/echo "hello"
```

#### Run a container in the background

For a container to stay alive in detached mode, its main process must be long-running. Shells like `bash` exit immediately when there are no commands to run.

```bash
# Keep-alive container (use exec to interact with it)
pybox run --image ubuntu:24.04 --name debug-env -d -- sleep infinity
# → a1b2c3d4e5f6
# → Container 'debug-env' running in background.

# Enter it with exec
pybox exec debug-env -- /bin/bash

# Other keep-alive commands
pybox run --image ubuntu:24.04 --name daemon -d -- tail -f /dev/null
pybox run --image ubuntu:24.04 --name api -d --memory 512m -- /usr/bin/python3 server.py

# Check running containers
pybox ps
```

#### Start a stopped container

`pybox start` always starts containers in the background and returns immediately.
Use `pybox exec` to open a shell, `pybox logs` to see output, and `pybox stop` to stop it.

```bash
# By name
pybox start my-app

# By ID or prefix
pybox start a1b2c3

# Multiple at once
pybox start my-app webserver api
```

#### List containers

```bash
# Running containers
pybox ps

# All containers (including stopped)
pybox ps --all

# JSON output
pybox ps --all --format json
```

#### Execute a command in a running container

```bash
# By name (tab completion works here)
pybox exec my-app -- /bin/bash
pybox exec webserver -- bash -c "echo hello"

# By ID or prefix
pybox exec a1b2c3 -- /bin/bash
```

#### Container logs

```bash
# By name
pybox logs my-app

# Follow logs in real time
pybox logs --follow my-app

# Last N lines
pybox logs --tail 100 my-app

# With timestamps
pybox logs --timestamps my-app
```

#### Stop and remove containers

```bash
# By name (tab completion works)
pybox stop my-app
pybox stop webserver api

# By ID prefix
pybox stop a1b2c3

# With custom timeout (default 10s, then SIGKILL)
pybox stop --timeout 30 my-app

# Remove a stopped container
pybox rm my-app

# Force-remove a running container
pybox rm --force my-app

# Full lifecycle example
pybox run --image ubuntu:24.04 --name demo -d -- sleep infinity
pybox exec demo -- /bin/bash -c "echo hello from inside"
pybox stop demo
pybox start demo
pybox stop demo && pybox rm demo
```

---

### Images

#### Pull an image from a registry

```bash
# Docker Hub (default)
pybox pull ubuntu:24.04
pybox pull python:3.12-slim
pybox pull nginx:alpine

# Private registry
pybox pull registry.example.com/myapp:v1
```

#### List local images

```bash
pybox images
```

#### Remove an image

```bash
pybox rmi ubuntu:24.04
```

#### Log in to a registry

```bash
# Docker Hub
pybox login

# Specific registry
pybox login registry.example.com

# With credentials
pybox login -u myuser -p mytoken registry.example.com
```

#### Push an image

```bash
pybox push myapp:v1
pybox push registry.example.com/myapp:v1
```

---

### Building Images

#### Build from a boxfile.toml

```bash
# Using boxfile.toml in the current directory
pybox build -t myapp:v1

# Specifying the file
pybox build -f /path/to/boxfile.toml -t myapp:v1

# Force rebuild (skip cache)
pybox build -f boxfile.toml -t myapp:v1 --no-cache
```

---

### Networking

```bash
# List networks
pybox network ls

# Inspect a network
pybox network inspect <network-name>

# Create a custom network
pybox network create --cidr 10.100.0.0/24 mynet

# Remove a network
pybox network rm mynet
```

---

### System Info

```bash
# Version, rootless mode, storage driver, kernel, disk usage
pybox info
```

---

## Command Reference

| Command | Description | Accepts name? | Tab complete? |
|---------|-------------|:---:|:---:|
| `pybox run --image IMG [--name N] [-d] -- CMD` | Create and start a container | — | image |
| `pybox start NAME\|ID [...]` | Start stopped container(s) in background | ✅ | containers |
| `pybox stop NAME\|ID` | Stop container (SIGTERM → SIGKILL) | ✅ | running |
| `pybox exec NAME\|ID -- CMD` | Run command in a running container | ✅ | running |
| `pybox rm [-f] NAME\|ID` | Remove a container | ✅ | containers |
| `pybox ps [-a]` | List containers | — | — |
| `pybox logs [-f] [-n N] NAME\|ID` | Stream container logs | ✅ | containers |
| `pybox pull IMAGE` | Pull image from registry | — | — |
| `pybox push IMAGE` | Push image to registry | — | — |
| `pybox build -t TAG [-f FILE]` | Build image from boxfile.toml | — | — |
| `pybox images` | List local images | — | — |
| `pybox rmi IMAGE` | Remove a local image | — | — |
| `pybox network ls\|create\|rm\|inspect` | Manage networks | — | — |
| `pybox info` | Version, storage, rootless, kernel info | — | — |
| `pyboxd` | Start the daemon (Unix socket) | — | — |

> **Prefix resolution**: any command that accepts an ID also accepts a short prefix — `pybox stop a1b2` stops the container whose ID starts with `a1b2`.

---

## boxfile.toml

`boxfile.toml` is PyBox's equivalent of a `Dockerfile` — with modern TOML syntax and first-class Python support.

```toml
# boxfile.toml — complete PyBox image definition

[box]
name    = "my-app"
version = "1.0.0"
base    = "ubuntu:24.04"

[box.meta]
author      = "Your Name"
description = "A PyBox image example"
labels      = { env = "production", team = "backend" }

# ── Python first-class ───────────────────────────────────────────
# PyBox installs the venv and dependencies automatically,
# with smart caching based on the package hash.
[python]
version  = "3.12"
packages = [
  "fastapi==0.111.0",
  "uvicorn[standard]==0.30.0",
  "httpx>=0.27",
]

# ── Build steps (executed in order) ─────────────────────────────
[[steps]]
name = "install system dependencies"
run  = "apt-get update && apt-get install -y curl"

[[steps]]
name = "copy source code"
copy = { src = "./src", dst = "/app" }

[[steps]]
name = "set working directory"
workdir = "/app"

[[steps]]
name = "build-time variables"
env = { BUILD_ENV = "production" }

# ── Runtime configuration ────────────────────────────────────────
[run]
cmd    = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
expose = [8000]
env    = { PYTHONUNBUFFERED = "1", LOG_LEVEL = "info" }
user   = "appuser"

# ── Resource limits (cgroups v2) ─────────────────────────────────
[limits]
memory = "256m"    # 256 MB RAM
cpu    = 0.5       # 50% of one core
pids   = 100       # max 100 processes

# ── Healthcheck ──────────────────────────────────────────────────
[healthcheck]
cmd      = ["curl", "-f", "http://localhost:8000/health"]
interval = "30s"
timeout  = "5s"
retries  = 3
```

### Step Types

| Step | Field | Example |
|------|-------|---------|
| **Shell command** | `run` | `run = "apt-get install -y curl"` |
| **Copy files** | `copy` | `copy = { src = "./src", dst = "/app" }` |
| **Working directory** | `workdir` | `workdir = "/app"` |
| **Environment variables** | `env` | `env = { FOO = "bar" }` |

### Layer Cache

PyBox uses automatic per-step caching — identical to Docker's layer cache. Each step has a `cache_key` computed as `sha256(step_content + parent_digest)`. If the step and all previous steps are unchanged, PyBox reuses the cached layer and skips execution.

```bash
# Force rebuild (bypass cache)
pybox build -f boxfile.toml -t myapp:v1 --no-cache
```

---

## Environment Variables

| Variable | Description | Default (root) | Default (rootless) |
|----------|-------------|---------------|-------------------|
| `PYBOX_ROOT` | Storage root directory | `/var/lib/pybox` | `~/.local/share/pybox` |
| `PYBOX_CGROUP_ROOT` | PyBox cgroup root | `/sys/fs/cgroup/pybox` | `user@<uid>.service/pybox` |
| `PYBOX_SOCKET` | Daemon Unix socket path | `/run/pybox/pyboxd.sock` | `~/.local/share/pybox/pyboxd.sock` |
| `PYBOX_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`) | `INFO` | `INFO` |
| `DOCKER_CONFIG` | Registry credentials directory | `~/.docker` | `~/.docker` |

---

## Development

### Environment setup

```bash
# Clone and install in editable mode
git clone https://github.com/br00tm/pybox.git
cd pybox
pip install -e ".[dev]"
```

### Storage directory layout (created automatically)

```
/var/lib/pybox/          (or ~/.local/share/pybox/ for rootless)
├── images/              # OCI images stored by digest
├── containers/          # container state and rootfs
├── volumes/             # persistent volumes
├── networks/            # network state (ipam.json, etc.)
└── cache/
    └── layers/          # build layer cache by step hash
```

### Debug mode

```bash
# Enable verbose logging (syscalls, mounts, cgroup writes)
pybox --debug run --image ubuntu:24.04 -- /bin/bash
```

---

## Testing

### Unit tests (no root required)

```bash
# Run all unit tests
pytest tests/unit/ -v

# With code coverage
pytest tests/unit/ --cov=pybox --cov-report=term-missing

# Stop on first failure
pytest tests/unit/ -x
```

### Integration tests (require root)

Integration tests start a real `PyBoxDaemon` on a temporary socket and test the full IPC protocol.

```bash
sudo python3 -m pytest tests/integration/ -v
```

### End-to-end tests (require root + network)

```bash
sudo python3 -m pytest tests/e2e/ -v
```

### Full suite

```bash
# Unit + Integration
make test-all

# Unit only (CI-friendly, no root)
make test-unit
```

### Current test status

```
tests/unit/        → 158 passed ✅
tests/integration/ →   2 passed ✅  (require root)
```

---

## Makefile

```bash
make install-dev       # Install package in editable mode with dev deps
make test-unit         # Run unit tests (no root)
make test-integration  # Run integration tests (requires root)
make test-e2e          # Run end-to-end tests (requires root + network)
make test-all          # unit + integration
make lint              # Run ruff (linter)
make lint-fix          # Run ruff with auto-fix
make typecheck         # Run mypy (type checker)
make format            # Format code with ruff format
make run-daemon        # Start pyboxd in foreground
make build-example     # Build the example image from boxfile.toml
make clean             # Remove __pycache__, .mypy_cache, .ruff_cache, *.pyc
make clean-data        # ⚠️  Remove all data in /var/lib/pybox
```

---

## Daemon (pyboxd)

`pyboxd` is the PyBox central daemon — an asyncio server that listens on a Unix socket and processes CLI commands.

### Start the daemon

```bash
# Start in foreground
pyboxd

# Start as a systemd service (after install-dev.sh)
systemctl --user start pyboxd
systemctl --user enable pyboxd   # start on boot

# View daemon logs
journalctl --user -u pyboxd -f
```

### IPC Protocol

The daemon uses **msgpack** with a 4-byte big-endian length prefix for framing:

```
[4 bytes: length][msgpack payload]
```

Supported methods:

| Method | Description |
|--------|-------------|
| `PS` | List containers |
| `INSPECT` | Container details |
| `STOP` | Stop a container |
| `RM` | Remove a container |
| `LOGS` | Log stream (multi-response) |
| `EXEC` | Run command in container |
| `INFO` | Version and system info |
| `IMAGES` | List local images |

### Crash recovery

On startup, `pyboxd` checks all containers with state `RUNNING` and marks them as `STOPPED` if the process is no longer alive — ensuring state is consistent across restarts.

---

## Roadmap

- [x] **Phase 1** — Container Runner MVP (`pybox run --image ubuntu:24.04 -- /bin/bash`)
- [x] **Phase 2** — Image Builder (`pybox build -f boxfile.toml -t app:v1`)
- [x] **Phase 3** — Container Networking (bridge, veth, IPAM, NAT, DNS)
- [x] **Phase 4** — Daemon (`pyboxd`) + `ps`, `logs`, `exec`, `stop`, `rm`
- [x] **Phase 5** — OCI Registry Push/Pull + `pybox login`, `push`, `images`
- [x] **Phase 6** — Rootless Containers (user namespaces, fuse-overlayfs, slirp4netns)
- [x] **Phase 7** — Container names, background mode, tab completion, `pybox start`
- [ ] **Phase 8** — `pybox compose` — declarative multi-container definitions
- [ ] **Phase 9** — Prometheus metrics endpoint in the daemon
- [ ] **Phase 10** — SQLite for persistent state (replace JSON files)
- [ ] **Phase 11** — Plugin system via Python entry points

---

## Contributing

1. Fork the repository
2. Create your feature branch

   ```bash
   git checkout -b feature/my-feature
   ```

3. Commit your changes

   ```bash
   git commit -m "feat: add my feature"
   ```

4. Make sure tests pass

   ```bash
   make lint && make test-unit
   ```

5. Push to the branch

   ```bash
   git push origin feature/my-feature
   ```

6. Open a **Pull Request**

### Guidelines

- Unit tests are required for new modules
- Tests must not require root (mock syscalls where needed)
- Follow the existing style: ruff + mypy strict
- Docstrings and comments in English

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](./LICENSE) file for details.

---

<div align="center">

<img src="https://capsule-render.vercel.app/api?type=waving&color=0:1a1a2e,50:0d1b2a,100:0a0a1a&height=80&section=footer" width="100%"/>

Made with ❤️ by [**br00tm**](https://github.com/br00tm)

*"The best way to understand containers is to build one."*

</div>
