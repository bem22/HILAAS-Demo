"""Workshop-built native binary contract and host build lifecycle."""

import hashlib
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from hilaas.bundle import read_json, require
from hilaas.client import save
from hilaas.progress import stage


BINARY = "hilaas-selftest"
MANIFEST = "native-manifest.json"
TOKEN = "HILAAS_NATIVE_V1"
SUCCESS = "HILAAS_SELFTEST_V1 status=pass checks=5 failures=0"


def validate(directory):
    directory = Path(directory)
    require(not directory.is_symlink(), "Native bundle directory is a symlink")
    manifest = read_json(directory / MANIFEST)
    require(set(manifest) == {"kind", "architecture", "commit", "run_id", "nonce", "binary_sha256", "build_system"}, "Invalid native manifest fields")
    require(manifest["kind"] == "native" and manifest["architecture"] == "x86_64"
            and manifest["build_system"] == "workshop", "Expected a native Workshop build")
    for field, pattern in (("commit", r"[0-9a-f]{40}"), ("run_id", r"[A-Za-z0-9_-]{1,100}"),
                           ("nonce", r"[0-9a-f]{32}"), ("binary_sha256", r"[0-9a-f]{64}")):
        require(isinstance(manifest[field], str) and re.fullmatch(pattern, manifest[field]), "Invalid native " + field)
    binary = directory / BINARY
    require(binary.is_file() and not binary.is_symlink() and 64 <= binary.stat().st_size <= 2**20, "Invalid native binary file")
    content = binary.read_bytes()
    require(content[:7] == b"\x7fELF\x02\x01\x01" and content[18:20] == b"\x3e\x00", "Expected x86-64 little-endian ELF")
    require(hashlib.sha256(content).hexdigest() == manifest["binary_sha256"], "Native binary digest mismatch")
    return manifest, {BINARY: content, MANIFEST: (directory / MANIFEST).read_bytes()}


def verify_output(output, manifest):
    expected = f"HILAAS_BUILD_V1 commit={manifest['commit']} run_id={manifest['run_id']} nonce={manifest['nonce']}"
    lines = output.splitlines()
    require(lines.count(expected) == 1, "Native binary build identity mismatch")
    require(lines.count(SUCCESS) == 1 and not any("status=fail" in line for line in lines), "Native self-test failed or missing")


def build(source, output, project, commit, run_id):
    import yaml

    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Committed source required for Workshop")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid native run ID")
    source, output, project = Path(source), Path(output), Path(project)
    require(not project.exists() and not output.exists(), "Use fresh native build directories")
    output.mkdir(parents=True)
    nonce = secrets.token_hex(16)
    shutil.copytree(source / "config/native-workshop", project)
    recipe = project / "workshop.yaml"
    definition = yaml.safe_load(recipe.read_text())
    definition["name"] = "hilaas-native-" + hashlib.sha256(run_id.encode()).hexdigest()[:12]
    recipe.write_text(yaml.safe_dump(definition, sort_keys=False))
    for name in ("common", "native"):
        shutil.copytree(source / "firmware" / name, project / "firmware" / name)
    command = ["workshop", "-p", str(project)]
    launched = False
    try:
        with stage("workshop.launch"), (output / "workshop-launch.log").open("w") as log:
            subprocess.run([*command, "launch"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        launched = True
        with stage("workshop.build"), (output / "workshop-build.log").open("w") as log:
            subprocess.run([*command, "run", "--non-interactive", "--timeout", "10m",
                            "--env", "HILAAS_COMMIT=" + commit,
                            "--env", "HILAAS_RUN_ID=" + run_id,
                            "--env", "HILAAS_NONCE=" + nonce, "--", "build"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=660)
        binary = project / "build/native" / BINARY
        require(binary.is_file() and not binary.is_symlink(), "Workshop did not produce the native binary")
        manifest = {"kind": "native", "architecture": "x86_64", "commit": commit,
                    "run_id": run_id, "nonce": nonce, "build_system": "workshop",
                    "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        bundle = output / "bundle"
        bundle.mkdir()
        shutil.copyfile(binary, bundle / BINARY)
        save(bundle / MANIFEST, manifest)
        with stage("bundle.validate"):
            validate(bundle)
        print("Workshop build passed; binary SHA-256 " + manifest["binary_sha256"], flush=True)
        return manifest
    finally:
        if launched:
            with stage("workshop.stop"), (output / "workshop-stop.log").open("w") as log:
                subprocess.run([*command, "stop"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)


if __name__ == "__main__":
    root = Path.cwd()
    commit = os.environ["HILAAS_COMMIT"]
    run_id = os.environ["HILAAS_RUN_ID"]
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    require(actual == commit, "Dispatched commit differs from the checkout")
    require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], text=True), "Native build requires a clean checkout")
    build(root, root / "artifacts" / run_id / "build",
          Path.home() / ".local/state/HILAAS-Demo/native-builds" / run_id, commit, run_id)