"""Open only verified browser ingress, staging HTTP validation before HTTPS."""

import argparse
import json
import socket
import subprocess


HOST = "testflinger.16-16-193-47.sslip.io"
KUBECTL = ["/snap/bin/microk8s", "kubectl"]


def resources(*arguments):
    return json.loads(subprocess.check_output([*KUBECTL, *arguments, "-o", "json"], text=True, timeout=30))


def validate_routes(ingresses):
    gateway_found = False
    for ingress in ingresses:
        spec = ingress["spec"]
        if spec.get("ingressClassName") != "public":
            raise ValueError("Unexpected ingress class; review all routes before publishing")
        if ingress["metadata"]["namespace"] != "testflinger" or spec.get("defaultBackend"):
            raise ValueError("Unexpected namespace or default backend")
        challenge = any(owner.get("kind") == "Challenge" for owner in ingress["metadata"].get("ownerReferences", []))
        for rule in spec.get("rules", []):
            if rule.get("host") != HOST:
                raise ValueError("An ingress for another hostname remains; refusing publication")
            for route in rule["http"]["paths"]:
                service = route["backend"]["service"]
                if service["name"] == "hilaas-browser" and service["port"].get("number") == 4180 and route["path"] == "/":
                    gateway_found = True
                elif not (challenge and route["path"].startswith("/.well-known/acme-challenge/") and service["name"].startswith("cm-acme-http-solver-")):
                    raise ValueError("A route bypasses the GitHub gateway")
    if not gateway_found:
        raise ValueError("No authenticated browser route exists yet")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("private", "http", "https"))
    parser.add_argument("--expected-hostname", required=True)
    args = parser.parse_args()
    if socket.gethostname() != args.expected_hostname:
        raise SystemExit("Refusing to modify an unconfirmed host")
    if args.stage != "private":
        validate_routes(resources("get", "ingress", "-A")["items"])
        deployment = resources("get", "deployment", "hilaas-browser", "-n", "testflinger")
        if deployment.get("status", {}).get("availableReplicas", 0) < 1:
            raise SystemExit("The authentication gateway is unavailable")
    if args.stage == "https":
        certificate = resources("get", "certificate", "hilaas-browser", "-n", "testflinger")
        ready = any(condition["type"] == "Ready" and condition["status"] == "True" for condition in certificate.get("status", {}).get("conditions", []))
        if not ready or certificate["spec"]["dnsNames"] != [HOST]:
            raise SystemExit("A valid browser certificate is not ready")
    daemonset = resources("get", "daemonset", "nginx-ingress-microk8s-controller", "-n", "ingress")
    containers = daemonset["spec"]["template"]["spec"]["containers"]
    if len(containers) != 1:
        raise SystemExit("Unexpected ingress controller layout")
    container = containers[0]
    for port in container["ports"]:
        external = port.get("hostPort") == 80 and args.stage != "private"
        external |= port.get("hostPort") == 443 and args.stage == "https"
        port["hostIP"] = "0.0.0.0" if external else "127.0.0.1"
    patch = {"spec": {"template": {"spec": {"containers": [{"name": container["name"], "ports": container["ports"]}]}}}}
    subprocess.run([*KUBECTL, "patch", "daemonset", "nginx-ingress-microk8s-controller", "-n", "ingress", "--type=strategic", "-p", json.dumps(patch)], check=True, timeout=30)
    subprocess.run([*KUBECTL, "rollout", "status", "daemonset/nginx-ingress-microk8s-controller", "-n", "ingress", "--timeout=300s"], check=True, timeout=330)
    print("Ingress publication stage:", args.stage)


if __name__ == "__main__":
    main()