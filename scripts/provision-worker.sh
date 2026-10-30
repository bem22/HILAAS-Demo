#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 ]] || {
    printf 'Run only as root inside the dedicated LXD template\n' >&2
    exit 1
}
case ${HILAAS_WORKER_TYPE:-virtual-machine} in
    virtual-machine)
        [[ $(systemd-detect-virt --vm) == kvm ]] || {
            printf 'Expected a KVM template; container provisioning requires explicit opt-in\n' >&2
            exit 1
        }
        ;;
    container)
        [[ $(systemd-detect-virt --container) == lxc ]] || {
            printf 'Expected an LXD template container\n' >&2
            exit 1
        }
        ;;
    *) printf 'Unsupported worker type\n' >&2; exit 1 ;;
esac
[[ $(hostname) == hilaas-worker-template-* ]] || {
    printf 'Refusing to provision a machine outside the HILAAS template namespace\n' >&2
    exit 1
}
inputs=${1:?Provide the directory containing verified SDK and snap inputs}
for input in workshop.snap sdkcraft.snap freertos-esp-idf.sdk cli/pyproject.toml project/pyproject.toml; do
    [[ -f "$inputs/$input" ]] || { printf 'Missing input: %s\n' "$input" >&2; exit 1; }
done
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y ca-certificates curl git openssh-server python3-venv squashfs-tools zstd
if ! snap list lxd >/dev/null 2>&1; then
    snap install lxd --channel=5.21/stable
fi
lxd waitready --timeout=300
if ! lxc storage show default >/dev/null 2>&1; then
    lxd init --auto --storage-backend=dir
fi
if ! snap list workshop >/dev/null 2>&1; then
    snap install "$inputs/workshop.snap" --dangerous --classic
fi
if ! snap list sdkcraft >/dev/null 2>&1; then
    snap install "$inputs/sdkcraft.snap" --dangerous --classic
fi
loginctl enable-linger root
systemctl start user@0.service
export XDG_RUNTIME_DIR=/run/user/0
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/0/bus
systemctl --user show-environment >/dev/null
sdkcraft try "$inputs/freertos-esp-idf.sdk"
python3 -m venv /opt/hilaas-client
/opt/hilaas-client/bin/python -m pip install "$inputs/cli"
/opt/hilaas-client/bin/python -m pip install "$inputs/project[client]"
install -m 600 "$inputs/project/config/worker-ssh.conf" /etc/ssh/sshd_config.d/00-hilaas.conf
install -d -m 755 /run/sshd
sshd -t
systemctl restart ssh
workshop --version
/opt/hilaas-client/bin/python -c 'import requests, jwt, testflinger_cli.auth; print("Client dependencies available")'
printf 'Worker prerequisites installed; validate Workshop before publishing this template.\n'