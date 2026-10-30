#!/bin/bash
set -euo pipefail
[[ $(id -un) == ubuntu && $(hostname) == "${HILAAS_EXPECTED_HOSTNAME:?Set the verified EC2 hostname}" ]] || {
    printf 'Run as ubuntu on the confirmed AWS host\n' >&2
    exit 1
}
export PATH=/snap/bin:$PATH
database=localhost-controller:testflinger-db
server=localhost-controller:testflinger
umask 077
config_dir="$HOME/.config/hilaas-cloud"
mkdir -p "$config_dir"
[[ ! -L "$config_dir" ]]
chmod 700 "$config_dir"
config="$config_dir/testflinger.json"
if [[ ! -e "$config" ]]; then
    python3 - "$config" <<'PY'
import json
import os
import secrets
import sys

with open(sys.argv[1], "x", encoding="utf-8") as stream:
    json.dump({"testflinger-k8s": {"jwt_signing_key": secrets.token_urlsafe(48)}}, stream)
    stream.flush()
    os.fsync(stream.fileno())
PY
fi
[[ -f "$config" && ! -L "$config" ]]
chmod 600 "$config"
juju_config_dir="$HOME/snap/juju/common/hilaas-cloud"
install -d -m 700 "$juju_config_dir"
install -m 600 "$config" "$juju_config_dir/testflinger.json"
if ! juju status -m "$database" --format=json | jq -e '.applications | has("mongodb")' >/dev/null; then
    juju deploy mongodb --channel=6/stable -m "$database" --constraints='cores=1 mem=2G'
fi
if ! juju offers -m "$database" --format=json | jq -e 'has("mongodb")' >/dev/null; then
    juju offer -c localhost-controller testflinger-db.mongodb:database mongodb
fi
if ! juju status -m "$server" --format=json | jq -e '."remote-applications" | has("mongodb")' >/dev/null; then
    juju consume -m "$server" localhost-controller:admin/testflinger-db.mongodb
fi
if ! juju status -m "$server" --format=json | jq -e '.applications | has("testflinger-k8s")' >/dev/null; then
    juju deploy testflinger-k8s --channel=stable -m "$server" --config "$juju_config_dir/testflinger.json"
fi
juju integrate -m "$server" testflinger-k8s:mongodb_client mongodb:database
juju status -m "$database" --storage --relations
juju status -m "$server" --storage --relations
printf 'Charms deployed; check workload health before configuring ingress and clients.\n'