#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/snap/bin:$PATH"
: "${HILAAS_RUN_ID:?}" "${HILAAS_COMMIT:?}" "${HILAAS_TARGET:?}"
[[ "$HILAAS_RUN_ID" =~ ^[A-Za-z0-9_-]{1,100}$ ]]
[[ "${HILAAS_ENABLE_HARDWARE:-no}" == no ]] || { printf 'Preliminary builds cannot enable hardware\n' >&2; exit 1; }
[[ "${HILAAS_ALLOW_DOWNLOADS:-no}" == yes ]] || { printf 'Explicit SDK download permission required\n' >&2; exit 1; }
export HILAAS_MODE=preliminary HILAAS_ENABLE_HARDWARE=no
case "$HILAAS_TARGET" in
    microbit|nucleo) events=(--event-target "$HILAAS_TARGET") ;;
    zephyr-all) events=(--event-target microbit --event-target nucleo) ;;
    *) printf 'Unsupported preliminary target\n' >&2; exit 1 ;;
esac
mkdir -p "$HOME/.local/state/HILAAS-Demo"
exec 9>"$HOME/.local/state/HILAAS-Demo/orchestrator.lock"
flock -n 9 || { printf 'Another target build is active\n' >&2; exit 1; }
python3 -m hilaas.progress run --target "$HILAAS_TARGET" "${events[@]}" --output "artifacts/$HILAAS_RUN_ID/progress" -- \
    spread -artifacts "artifacts/$HILAAS_RUN_ID" preliminary:ubuntu-26.04:spread/preliminary:checkpoint