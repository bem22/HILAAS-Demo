#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 && $(systemd-detect-virt --container) == lxc ]] || {
    printf 'Run only inside the dedicated mock template container\n' >&2
    exit 1
}
[[ $(hostname) == hilaas-mock-template-* ]] || exit 1
inputs=${1:?Provide project directory with config/worker-ssh.conf}
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y openssh-server python3-requests python3-pytest
install -m 600 "$inputs/config/worker-ssh.conf" /etc/ssh/sshd_config.d/00-hilaas.conf
install -d -m 755 /run/sshd
sshd -t
systemctl restart ssh
python3 -c 'import requests, pytest; print("Mock worker dependencies available")'
printf 'mock-v1\n' > /etc/hilaas-spread-template