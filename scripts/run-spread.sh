#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
mode=${1:-build}
[[ "$mode" == build || "$mode" == hardware ]] || { printf 'Use build or hardware\n' >&2; exit 1; }
command -v spread >/dev/null || { printf 'Install Canonical Spread first\n' >&2; exit 1; }
[[ ${HILAAS_WORKER_IMAGE:-} =~ ^[0-9a-f]{64}$ ]] || { printf 'Set a pre-provisioned local VM image fingerprint\n' >&2; exit 1; }
[[ ${HILAAS_ALLOW_DOWNLOADS:-no} == yes ]] || { printf 'Set HILAAS_ALLOW_DOWNLOADS=yes on an unmetered connection\n' >&2; exit 1; }
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || { printf 'Commit or remove pending changes before a real build\n' >&2; exit 1; }
export HILAAS_COMMIT
HILAAS_COMMIT=$(git rev-parse HEAD)
export HILAAS_MODE="$mode"
export HILAAS_RUN_ATTEMPT=${GITHUB_RUN_ATTEMPT:-1}
if [[ "$mode" == hardware ]]; then
    [[ ${HILAAS_ENABLE_HARDWARE:-no} == yes ]]
    [[ "$HILAAS_RUN_ATTEMPT" == 1 ]]
    : "${HILAAS_RUN_ID:=${GITHUB_RUN_ID:-}}"
    : "${HILAAS_RUN_ID:?Set a unique, stable run ID; do not change it to retry an uncertain job}"
    : "${HILAAS_CLIENT_CONFIG:?Set a private client configuration path}"
    : "${HILAAS_AUTH_DIR:?Set a dedicated CLI authentication directory}"
else
    HILAAS_RUN_ID=${HILAAS_RUN_ID:-${GITHUB_RUN_ID:-build-$(date +%Y%m%d%H%M%S)-$$}}
fi
export HILAAS_RUN_ID
[[ "$HILAAS_RUN_ID" =~ ^[A-Za-z0-9_-]{1,100}$ ]]
export HILAAS_TARGET=${HILAAS_TARGET:-esp32c6-devkitc-1}
[[ "$HILAAS_TARGET" == esp32c6-devkitc-1 ]] || { printf 'Unsupported firmware adapter\n' >&2; exit 1; }
export HILAAS_STATE_DIR="$HOME/.local/state/HILAAS-Demo"
mkdir -p "$HILAAS_STATE_DIR"
chmod 700 "$HILAAS_STATE_DIR"
exec 9>"$HILAAS_STATE_DIR/orchestrator.lock"
flock -n 9 || { printf 'Another HILAAS Spread run is active\n' >&2; exit 1; }
status=0
python3 -m hilaas.progress run --target "$HILAAS_TARGET" --output "artifacts/$HILAAS_RUN_ID/progress" -- \
    spread -artifacts="artifacts/$HILAAS_RUN_ID" local-vm:spread/boards:esp32c6 || status=$?
results="artifacts/$HILAAS_RUN_ID/local-vm:ubuntu-24.04:spread/boards:esp32c6/results"
if [[ -d "$results" ]]; then
    mkdir -p "artifacts/$HILAAS_RUN_ID/targets/$HILAAS_TARGET"
    cp -a "$results/." "artifacts/$HILAAS_RUN_ID/targets/$HILAAS_TARGET/"
fi
exit "$status"
