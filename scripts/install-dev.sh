#!/usr/bin/env bash
# install-dev.sh — Set up the PyBox development environment
# Usage: sudo ./scripts/install-dev.sh
set -euo pipefail

CURRENT_USER="${SUDO_USER:-${USER}}"

echo "==> Installing system dependencies for PyBox development..."

apt-get update -q

# Core container runtime dependencies
apt-get install -y --no-install-recommends \
    fuse-overlayfs \
    slirp4netns \
    uidmap \
    nftables \
    iproute2 \
    iptables \
    fuse3 \
    nsenter

# Python build dependencies
apt-get install -y --no-install-recommends \
    python3-pip \
    python3-venv \
    python3-dev

echo "==> Configuring /etc/subuid and /etc/subgid for ${CURRENT_USER}..."

# Add subordinate UID/GID entries if not already present
if ! grep -q "^${CURRENT_USER}:" /etc/subuid 2>/dev/null; then
    echo "${CURRENT_USER}:100000:65536" >> /etc/subuid
    echo "    Added subuid entry for ${CURRENT_USER}"
else
    echo "    subuid entry already exists for ${CURRENT_USER}"
fi

if ! grep -q "^${CURRENT_USER}:" /etc/subgid 2>/dev/null; then
    echo "${CURRENT_USER}:100000:65536" >> /etc/subgid
    echo "    Added subgid entry for ${CURRENT_USER}"
else
    echo "    subgid entry already exists for ${CURRENT_USER}"
fi

echo "==> Creating PyBox storage directories..."
mkdir -p /var/lib/pybox/{images,containers,volumes,networks,cache}
mkdir -p /sys/fs/cgroup/pybox || true
mkdir -p /run/pybox

# Give the current user ownership of the pybox storage
chown -R "${CURRENT_USER}:${CURRENT_USER}" /var/lib/pybox 2>/dev/null || true

echo "==> Installing PyBox Python package (editable mode)..."
# Run as the non-root user to install into their Python environment
sudo -u "${CURRENT_USER}" pip3 install --break-system-packages -e ".[dev]" 2>/dev/null || \
    sudo -u "${CURRENT_USER}" pip3 install -e ".[dev]"

echo "==> Enabling systemd lingering for ${CURRENT_USER} (allows user services)..."
loginctl enable-linger "${CURRENT_USER}" 2>/dev/null || true

echo ""
echo "==> PyBox dev environment ready!"
echo ""
echo "    Run tests:     python3 -m pytest tests/unit/ -q"
echo "    Start daemon:  pyboxd &"
echo "    Run container: pybox run --image ubuntu:24.04 -- /bin/bash"
echo ""
