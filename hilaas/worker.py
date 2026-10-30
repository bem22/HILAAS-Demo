"""Disposable LXD worker lifecycle for Spread's AdHoc backend."""

import argparse
import ipaddress
import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path

from hilaas.bundle import read_json, require
from hilaas.client import save
from hilaas.native import BINARY, MANIFEST, validate as validate_native


def lxc(*arguments, input_text=None, check=True):
    result = subprocess.run(["lxc", "--force-local", "--project", "default", *arguments], input=input_text,
                            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
    if check and result.returncode:
        raise RuntimeError(f"LXD operation failed: {arguments[0]}; inspect the dedicated VM locally")
    return result


def lease_path():
    run_id = os.environ["HILAAS_RUN_ID"]
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid run ID")
    state = Path.home() / ".local/state/HILAAS-Demo"
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    return state / f"worker-{run_id}.json"


def discard(path):
    if not path.exists():
        return
    lease = read_json(path)
    name = lease["name"]
    require(re.fullmatch(r"hilaas-spread-[0-9a-f]{12}", name), "Refusing unrelated instance name")
    instances = json.loads(lxc("list", name, "--format=json").stdout)
    if not instances:
        path.unlink()
        return
    require(len(instances) == 1 and instances[0]["name"] == name, "Ambiguous VM identity")
    require(instances[0].get("config", {}).get("user.hilaas-owner") == lease["owner"], "VM ownership marker differs")
    if instances[0]["status"] != "Stopped":
        lxc("stop", name, "--timeout", "30")
    lxc("delete", name)
    path.unlink()


def allocate(path):
    mock = os.environ.get("HILAAS_MODE") == "mock"
    fingerprint = os.environ.get("HILAAS_MOCK_WORKER_IMAGE" if mock else "HILAAS_WORKER_IMAGE", "")
    require(re.fullmatch(r"[0-9a-f]{64}", fingerprint), "HILAAS_WORKER_IMAGE must be a cached local VM image fingerprint")
    worker_type = "container" if mock else os.environ.get("HILAAS_WORKER_TYPE", "virtual-machine")
    require(worker_type in {"virtual-machine", "container"}, "Unsupported worker type")
    require(not path.exists(), "A worker lease exists; inspect or discard it before reusing this run ID")
    images = json.loads(lxc("image", "list", fingerprint, "--format=json").stdout)
    require(len(images) == 1 and images[0]["fingerprint"] == fingerprint and images[0]["type"] == worker_type, "A pre-provisioned local image matching the worker type is required; no automatic image download")
    password = os.environ["SPREAD_PASSWORD"]
    require(password and "\n" not in password and ":" not in password, "Invalid Spread session password")
    mock_token = None
    native_bundle = None
    if mock:
        mock_token = Path(os.environ["HILAAS_MOCK_TOKEN_FILE"]).resolve(strict=True)
        require(mock_token.is_file() and read_json(mock_token).get("client_id") == "hilaas-laptop-mock-submitter", "Dedicated mock submitter file required")
        if os.environ.get("HILAAS_NATIVE_BUNDLE"):
            native_bundle = Path(os.environ["HILAAS_NATIVE_BUNDLE"]).resolve(strict=True)
            validate_native(native_bundle)
    config = auth = None
    if os.environ.get("HILAAS_MODE", "build") == "hardware":
        config = Path(os.environ["HILAAS_CLIENT_CONFIG"]).resolve(strict=True)
        auth = Path(os.environ["HILAAS_AUTH_DIR"]).resolve(strict=True)
        require(config.is_file() and config.name == "client.json" and auth.is_dir(), "Dedicated client.json and auth directory required")
        require(set(config.parent.iterdir()) == {config}, "Client configuration must be alone in a dedicated directory")
    owner = uuid.uuid4().hex
    name = "hilaas-spread-" + owner[:12]
    lease = {"name": name, "owner": owner}
    with path.open("x") as stream:
        json.dump(lease, stream)
    try:
        options = ["--vm", "-c", "limits.memory=6GiB", "-d", "root,size=40GiB"]
        if worker_type == "container":
            options = ["-c", "security.nesting=true", "-c", "limits.memory=4GiB"]
        if mock:
            options = ["-c", "security.nesting=false", "-c", "security.privileged=false", "-c", "limits.memory=512MiB"]
        lxc("init", fingerprint, name, *options, "-c", "limits.cpu=2",
            "-c", "user.hilaas-owner=" + owner)
        if mock:
            ledger = path.parent / "mock-submissions"
            ledger.mkdir(mode=0o700, exist_ok=True)
            lxc("config", "device", "add", name, "hilaas-ledger", "disk", "source=" + str(ledger), "path=/var/lib/hilaas-ledger", "shift=true")
        if config is not None:
            ledger = path.parent / "submissions"
            ledger.mkdir(mode=0o700, exist_ok=True)
            mount_options = ["shift=true"] if worker_type == "container" else []
            lxc("config", "device", "add", name, "hilaas-client", "disk", "source=" + str(config.parent), "path=/etc/hilaas", "readonly=true", *mount_options)
            lxc("config", "device", "add", name, "hilaas-auth", "disk", "source=" + str(auth), "path=/var/lib/hilaas-auth", *mount_options)
            lxc("config", "device", "add", name, "hilaas-ledger", "disk", "source=" + str(ledger), "path=/var/lib/hilaas-ledger", *mount_options)
        lxc("start", name)
        deadline = time.monotonic() + 180
        while True:
            response = lxc("exec", name, "--", "ip", "-j", "-4", "route", "get", "1.1.1.1", check=False)
            if response.returncode == 0:
                routes = json.loads(response.stdout)
                if routes and routes[0].get("prefsrc"):
                    address = str(ipaddress.IPv4Address(routes[0]["prefsrc"]))
                    break
            require(time.monotonic() < deadline, f"VM did not acquire an address: {name}")
            time.sleep(2)
        marker = lxc("exec", name, "--", "cat", "/etc/hilaas-spread-template").stdout.strip()
        require(marker == ("mock-v1" if mock else "v1"), "Image is not an approved HILAAS disposable worker template")
        if mock:
            lxc("exec", name, "--", "install", "-d", "-m", "700", "/etc/hilaas-mock")
            lxc("file", "push", "--mode=0600", str(mock_token), name + "/etc/hilaas-mock/submitter.json")
        if native_bundle is not None:
            lxc("exec", name, "--", "install", "-d", "-m", "700", "/opt/hilaas-native")
            for filename in (BINARY, MANIFEST):
                lxc("file", "push", "--mode=0600", str(native_bundle / filename), name + "/opt/hilaas-native/" + filename)
        lxc("exec", name, "--", "chpasswd", input_text="root:" + password + "\n")
        lease["address"] = address
        save(path, lease)
        print("ADDRESS " + address + ":22", flush=True)
    except BaseException:
        discard(path)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("allocate", "discard"))
    args = parser.parse_args()
    path = lease_path()
    if args.action == "allocate":
        allocate(path)
    else:
        discard(path)


if __name__ == "__main__":
    main()
