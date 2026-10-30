#!/bin/bash
set -euo pipefail
[[ $(id -u) == 0 && $(hostname) == "${HILAAS_EXPECTED_HOSTNAME:?Set the verified EC2 hostname}" ]] || exit 1
export DEBIAN_FRONTEND=noninteractive
apt-get install -y python3-yaml
python3 - <<'PY' | /snap/bin/microk8s kubectl apply -f -
import json
import sys
from pathlib import Path
from string import Template

import yaml

source = Path("/var/snap/microk8s/common/addons/core/addons/ingress/ingress.yaml")
rendered = Template(source.read_text()).safe_substitute(
    IMAGE="registry.k8s.io/ingress-nginx/controller",
    TAG="v1.13.8",
    DEFAULT_CERT="- ' '",
    EXTRA_ARGS="- --publish-status-address=127.0.0.1",
)
resources = list(yaml.safe_load_all(rendered))
controllers = [resource for resource in resources if resource.get("kind") == "DaemonSet"]
assert len(controllers) == 1, "Unexpected ingress addon layout"
pod = controllers[0]["spec"]["template"]["spec"]
assert not pod.get("hostNetwork"), "Refusing host-network ingress"
for container in pod["containers"]:
    for port in container.get("ports", []):
        if "hostPort" in port:
            port["hostIP"] = "127.0.0.1"
json.dump({"apiVersion": "v1", "kind": "List", "items": resources}, sys.stdout)
PY
/snap/bin/microk8s kubectl rollout status -n ingress daemonset/nginx-ingress-microk8s-controller --timeout=300s
printf 'Ingress host ports are loopback-only; use SSH forwarding for remote access.\n'