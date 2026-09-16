#!/usr/bin/env bash
set -euo pipefail

# This script is intended to run inside an already-created Linux LXC. It does not create or reconfigure an LXC
# on the host because LXC/Proxmox settings are
# runtime-specific. The default action is a read-only preflight.
action=check
force=0
require_gpu=0

usage() {
  cat <<'EOF'
Usage: setup_lxc_docker.sh [--check|--apply|--print-config] [--force] [--require-gpu]

  --check         Validate the LXC and Docker prerequisites (default).
  --apply         Install Docker Engine and Compose on Debian/Ubuntu (root).
  --print-config  Print host/LXC nesting guidance without changing anything.
  --force         Allow --check/--apply outside a detected LXC.
  --require-gpu   Fail unless NVIDIA devices and nvidia-smi are available.
EOF
}

while (($#)); do
  case "$1" in
    --check) action=check; shift ;;
    --apply) action=apply; shift ;;
    --print-config) action=print-config; shift ;;
    --force) force=1; shift ;;
    --require-gpu) require_gpu=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$(uname -s)" != Linux ]]; then
  printf '%s\n' 'This helper is Linux-only. Use Docker Desktop directly on Windows/macOS.' >&2
  exit 2
fi

if [[ "$action" == print-config ]]; then
  cat <<'EOF'
LXC -> Docker -> Compose guidance

1. Create an unprivileged or privileged LXC using your platform's normal tool.
2. Enable nesting and keyctl where required by the LXC runtime.
   Proxmox example: pct set <CTID> -features nesting=1,keyctl=1
3. Provide cgroup delegation and network access to the LXC.
4. Run this script inside the LXC with --check, then --apply as root.
5. Run the normal LLM-Wiki Compose commands inside that LXC.

GPU mode is not configured by this helper. NVIDIA device nodes, drivers, and
the container toolkit must be passed through by the LXC host first.
EOF
  exit 0
fi

virt=""
if command -v systemd-detect-virt >/dev/null 2>&1; then
  virt="$(systemd-detect-virt --container 2>/dev/null || true)"
fi
if [[ -z "$virt" ]] && grep -Eqi '(^|/)lxc($|/)' /proc/1/cgroup 2>/dev/null; then
  virt=lxc
fi
if [[ "$virt" != lxc && "$virt" != lxc-libvirt && "$force" != 1 ]]; then
  printf '%s\n' 'No LXC container detected. Run inside the LXC, or use --force after reviewing the risks.' >&2
  exit 2
fi
printf 'Detected virtualization: %s\n' "${virt:-unknown (forced)}"

if [[ ! -d /sys/fs/cgroup ]]; then
  printf '%s\n' 'cgroup filesystem is unavailable; Docker cannot be validated safely.' >&2
  exit 2
fi

if [[ "$require_gpu" == 1 ]]; then
  if [[ ! -e /dev/nvidia0 ]] || ! command -v nvidia-smi >/dev/null 2>&1; then
    printf '%s\n' 'GPU was required but NVIDIA devices or nvidia-smi are unavailable in this LXC.' >&2
    exit 2
  fi
  nvidia-smi --query-gpu=name --format=csv,noheader | sed 's/^/GPU: /'
else
  if [[ -e /dev/nvidia0 ]] && command -v nvidia-smi >/dev/null 2>&1; then
    printf '%s\n' 'NVIDIA device detected; GPU Compose still requires toolkit/runtime configuration.'
  else
    printf '%s\n' 'No NVIDIA device detected; CPU Compose is the available mode.'
  fi
fi

if [[ "$action" == check ]]; then
  if command -v docker >/dev/null 2>&1; then
    docker info >/dev/null 2>&1 && printf '%s\n' 'Docker daemon: reachable' || printf '%s\n' 'Docker CLI found, daemon not reachable yet.'
    if docker compose version >/dev/null 2>&1; then
      printf '%s\n' 'Docker Compose plugin: available'
    else
      printf '%s\n' 'Docker Compose plugin: missing'
    fi
  else
    printf '%s\n' 'Docker: not installed (run with --apply as root on Debian/Ubuntu).'
  fi
  exit 0
fi

if [[ "$EUID" != 0 ]]; then
  printf '%s\n' '--apply must run as root inside the LXC.' >&2
  exit 2
fi
if [[ ! -r /etc/os-release ]]; then
  printf '%s\n' 'Cannot identify the Linux distribution.' >&2
  exit 2
fi
# shellcheck disable=SC1091
. /etc/os-release
if [[ "${ID:-}" != debian && "${ID_LIKE:-}" != *debian* ]]; then
  printf 'Unsupported distribution: %s. Install Docker using its native package instructions, then rerun --check.\n' "${ID:-unknown}" >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl gnupg
install -m 0755 -d /etc/apt/keyrings
tmp_key="$(mktemp)"
trap 'rm -f "$tmp_key"' EXIT
docker_os=debian
if [[ "${ID}" == ubuntu ]]; then docker_os=ubuntu; fi
curl -fsSL "https://download.docker.com/linux/${docker_os}/gpg" -o "$tmp_key"
gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg "$tmp_key"
chmod a+r /etc/apt/keyrings/docker.gpg
arch="$(dpkg --print-architecture)"
codename="${VERSION_CODENAME:-bookworm}"
printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' "$arch" "$docker_os" "$codename" > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker info >/dev/null
docker compose version
printf '%s\n' 'Docker is installed and reachable. Continue with the documented LLM-Wiki Compose command.'
