#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == "${HILAAS_EXPECTED_HOSTNAME:?Set the verified EC2 hostname}" ]] || {
    printf 'Refusing to provision an unconfirmed host\n' >&2
    exit 1
}
[[ $(nproc) -ge 4 ]] || {
    printf 'Warning: below the official Testflinger recommendation of 4 CPU cores\n' >&2
    [[ ${HILAAS_ALLOW_UNDERSIZED:-no} == yes ]] || exit 1
}
awk '/MemTotal:/ { exit ($2 < 6000000) }' /proc/meminfo || {
    printf 'The official Testflinger deployment guide requires at least 6 GB RAM\n' >&2
    exit 1
}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git jq python3-venv zstd
if ! snap list lxd >/dev/null 2>&1; then
    snap install lxd --channel=5.21/stable
fi
/snap/bin/lxd waitready --timeout=300
if ! /snap/bin/lxc --force-local storage show default >/dev/null 2>&1; then
    /snap/bin/lxd init --auto --storage-backend=dir
fi
if ! snap list juju >/dev/null 2>&1; then
    snap install juju --channel=3.6/stable
fi
if ! snap list microk8s >/dev/null 2>&1; then
    snap install microk8s --channel=1.34-strict/stable
fi
if ! snap list testflinger-cli >/dev/null 2>&1; then
    snap install testflinger-cli
fi
usermod -aG lxd,snap_microk8s ubuntu
timeout 600 /snap/bin/microk8s status --wait-ready
/snap/bin/microk8s enable dns hostpath-storage
printf 'MicroK8s/Juju prerequisites installed; controller, charms, and ingress remain to be deployed.\n'