#!/bin/bash
set +x
set -euo pipefail
[[ $(id -un) == ubuntu && $(hostname) == "${HILAAS_EXPECTED_HOSTNAME:?Set HILAAS_EXPECTED_HOSTNAME}" ]] || {
    printf 'Run only as ubuntu on the explicitly selected AWS host\n' >&2
    exit 1
}
runner="$HOME/actions-runner-hilaas"
case ${1:-install} in
    install)
        [[ $(uname -m) == x86_64 && ! -e "$runner/.runner" && ! -e "$runner/bin/Runner.Listener" ]] || {
            printf 'Refusing to replace an existing runner or install on another architecture\n' >&2
            exit 1
        }
        install -d -m 700 "$runner"
        cd "$runner"
        archive=actions-runner-linux-x64-2.338.2.tar.gz
        curl --fail --location --silent --show-error --connect-timeout 15 --max-time 600 \
            "https://github.com/actions/runner/releases/download/v2.338.2/$archive" --output "$archive"
        printf '%s  %s\n' baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40 "$archive" | sha256sum --check -
        tar -xzf "$archive"
        ./bin/Runner.Listener --version
        ;;
    register)
        [[ -t 0 && -x "$runner/config.sh" && ! -e "$runner/.runner" ]] || {
            printf 'An installed, unregistered runner and an interactive terminal are required\n' >&2
            exit 1
        }
        sudo -n true
        cd "$runner"
        umask 077
        read -r -s -p 'GitHub runner registration token: ' ACTIONS_RUNNER_INPUT_TOKEN
        printf '\n'
        [[ -n "$ACTIONS_RUNNER_INPUT_TOKEN" && "$ACTIONS_RUNNER_INPUT_TOKEN" != *[[:space:]]* ]] || exit 1
        export ACTIONS_RUNNER_INPUT_TOKEN
        trap 'unset ACTIONS_RUNNER_INPUT_TOKEN' EXIT
        export PATH="$HOME/.local/bin:/snap/bin:$PATH"
        ./config.sh --unattended --url https://github.com/bem22/HILAAS-Demo \
            --name hilaas-aws --labels hilaas-demo --work _work
        unset ACTIONS_RUNNER_INPUT_TOKEN
        sudo -n ./svc.sh install ubuntu
        sudo -n ./svc.sh start
        ;;
    *) printf 'Use install or register\n' >&2; exit 1 ;;
esac