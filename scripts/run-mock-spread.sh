#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:/snap/bin:$PATH"
command -v spread >/dev/null
export HILAAS_MODE=mock HILAAS_ENABLE_HARDWARE=no
export HILAAS_COMMIT=${HILAAS_COMMIT:-}
export HILAAS_RUN_ID=${HILAAS_RUN_ID:?Set a unique mock run ID}
[[ "$HILAAS_RUN_ID" =~ ^[A-Za-z0-9_-]{1,100}$ ]]
[[ ${GITHUB_RUN_ATTEMPT:-1} == 1 ]] || { printf 'Inspect previous submission before retrying\n' >&2; exit 1; }
export HILAAS_MOCK_WORKER_IMAGE=${HILAAS_MOCK_WORKER_IMAGE:?Set the approved mock image fingerprint}
export HILAAS_MOCK_TOKEN_FILE=${HILAAS_MOCK_TOKEN_FILE:-$HOME/.config/hilaas-mock/submitter.json}
export HILAAS_MOCK_SERVER=${HILAAS_MOCK_SERVER:-http://10.152.183.194:5000}
mkdir -p "$HOME/.local/state/HILAAS-Demo"
exec 9>"$HOME/.local/state/HILAAS-Demo/mock-spread.lock"
flock -n 9 || { printf 'Another mock Spread run is active\n' >&2; exit 1; }
status=0
python3 -m hilaas.progress run --target laptop --output "artifacts/$HILAAS_RUN_ID/progress" -- \
	spread -artifacts "artifacts/$HILAAS_RUN_ID" mock-container:ubuntu-24.04:spread/mock:laptop || status=$?
results="artifacts/$HILAAS_RUN_ID/mock-container:ubuntu-24.04:spread/mock:laptop/results"
if [[ -d "$results" ]]; then
	mkdir -p "artifacts/$HILAAS_RUN_ID/laptop"
	cp -a "$results/." "artifacts/$HILAAS_RUN_ID/laptop/"
fi
exit "$status"