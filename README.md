# 🐍 PyBox

> *A Python-native container runtime — containers, reimagined.*

PyBox é um container runtime construído do zero em Python puro, usando diretamente as primitivas do kernel Linux (namespaces, cgroups v2, OverlayFS) que sustentam o Docker. Nada de Go, nada de binários opacos — só Python falando com o kernel.

```bash
pybox run --image ubuntu:24.04 -- /bin/bash
pybox build -f boxfile.toml -t minha-app:v1
pybox ps
```

## Por que PyBox?

- **Transparente**: cada syscall é visível e auditável
- **Python-first**: suporte nativo a `venv` e `pip` no `boxfile.toml`
- **Moderno**: cgroups v2 only, OverlayFS, OCI-compatible
- **Educativo**: o código é a documentação

## Documentação

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — arquitetura completa, fluxos e roadmap
- [`examples/boxfile.toml`](examples/boxfile.toml) — exemplo de image definition

## Fundamentos

PyBox usa três primitivas do kernel Linux:

| Primitiva | Uso |
|-----------|-----|
| **Namespaces** | Isolamento de PID, rede, filesystem, hostname |
| **cgroups v2** | Limites de CPU, memória, I/O, PIDs |
| **OverlayFS** | Sistema de camadas de imagem (copy-on-write) |

## Requisitos

- Python 3.11+
- Linux kernel 5.4+ (Ubuntu 22.04+, Debian 11+)
- cgroups v2 habilitado (`/sys/fs/cgroup/cgroup.controllers` deve existir)

## Desenvolvimento

```bash
git clone https://github.com/br00tm/pybox
cd pybox
pip install -e ".[dev]"
pytest tests/unit/
```

## Status

🚧 Em construção — Fase 1 (Container Runner MVP)

## Licença

MIT
# pybox
