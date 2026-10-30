"""Explicit build-only and opt-in hardware paths for a Spread worker."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
from pathlib import Path

from hilaas.bundle import SDK_VERSION, TARGET, package, read_json, require, validate
from hilaas.progress import stage


ROOT = Path(__file__).resolve().parents[1]


def build_host(source, output, project, commit, run_id):
    import hashlib
    import yaml

    require(not project.exists() and not output.exists(), "Use fresh firmware build directories")
    output.mkdir(parents=True)
    project.mkdir(parents=True)
    for name in ("hilaas", "firmware", "scripts", ".workshop"):
        shutil.copytree(source / name, project / name, ignore=shutil.ignore_patterns("__pycache__", "build"))
    shutil.copyfile(source / "targets.json", project / "targets.json")
    definition = yaml.safe_load((source / "workshop.yaml").read_text())
    definition["name"] = "hilaas-c6-" + hashlib.sha256(run_id.encode()).hexdigest()[:12]
    (project / "workshop.yaml").write_text(yaml.safe_dump(definition, sort_keys=False))
    command = ["workshop", "-p", str(project)]
    launched = False
    try:
        with stage("workshop.launch"), (output / "workshop-launch.log").open("w") as log:
            subprocess.run([*command, "launch"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
        launched = True
        with stage("workshop.build"), (output / "workshop-build.log").open("w") as log:
            subprocess.run([*command, "run", "--non-interactive", "--timeout", "30m",
                            "--env", "HILAAS_COMMIT=" + commit, "--", "build"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1860)
        manifest, _ = validate(project / "build/bundle")
        require(manifest["commit"] == commit, "Build commit mismatch")
        shutil.copytree(project / "build/bundle", output / "bundle")
    finally:
        if launched:
            with stage("workshop.stop"), (output / "workshop-stop.log").open("w") as log:
                subprocess.run([*command, "stop"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("targets", "identity", "package", "run", "host-build"))
    parser.add_argument("--target", default=TARGET)
    parser.add_argument("--mode", choices=("build", "hardware"), default="build")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    targets = read_json(ROOT / "targets.json")
    if args.command == "targets":
        print(json.dumps(targets, indent=2))
        return
    require(args.target in targets and targets[args.target]["enabled"], "Target is unconfigured; confirm the exact board first")
    require(args.target == TARGET, "This firmware adapter only supports the configured ESP32-C6 target")
    commit = os.environ.get("HILAAS_COMMIT", "")
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "HILAAS_COMMIT must identify committed source")
    build = ROOT / "build"
    if args.command == "host-build":
        run_id = os.environ["HILAAS_RUN_ID"]
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id), "Invalid build run ID")
        require(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip() == commit,
            "Dispatched commit differs from checkout")
        require(not subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True),
            "Firmware build requires a clean checkout")
        build_host(ROOT, args.output, Path.home() / ".local/state/HILAAS-Demo/c6-builds" / run_id, commit, run_id)
    elif args.command == "identity":
        identity = build / "identity"
        identity.mkdir(parents=True, exist_ok=True)
        nonce = secrets.token_hex(16)
        (identity / "nonce").write_text(nonce)
        (identity / "build_identity.h").write_text(f'#define HILAAS_NONCE "{nonce}"\n')
    elif args.command == "package":
        destination = build / "bundle"
        if destination.exists():
            require(not destination.is_symlink(), "Bundle path is a symlink")
            shutil.rmtree(destination)
        package(build / "idf", destination, commit, (build / "identity/nonce").read_text(), SDK_VERSION)
    else:
        config = None
        if args.mode == "hardware":
            require(os.environ.get("HILAAS_ENABLE_HARDWARE") == "yes", "Hardware execution is not enabled")
            from hilaas.client import validate_config
            config = validate_config(read_json(Path(os.environ["HILAAS_CLIENT_CONFIG"])))
            require(os.environ.get("HILAAS_RUN_ATTEMPT", "1") == "1", "Hardware reruns are disabled")
            require(Path(os.environ["HILAAS_LEDGER_DIR"]).is_dir(), "Persistent host ledger mount is required")
        args.output.mkdir(parents=True, exist_ok=True)
        bundle = Path(os.environ.get("HILAAS_FIRMWARE_BUNDLE", str(build / "bundle")))
        if "HILAAS_FIRMWARE_BUNDLE" not in os.environ:
            with stage("workshop.build"):
                subprocess.run(["workshop", "run", "--non-interactive", "--timeout", "30m",
                                "--env", f"HILAAS_COMMIT={commit}", "--", "build"], cwd=ROOT, check=True)
        with stage("bundle.validate"):
            manifest, _ = validate(bundle)
            require(manifest["commit"] == commit, "Build commit mismatch")
        destination = args.output / "bundle"
        require(not destination.exists(), "Use a fresh artifact directory")
        shutil.copytree(bundle, destination)
        if args.mode == "hardware":
            from hilaas.client import API
            from hilaas.demo import run_suites
            run_suites(destination, config, API(config), os.environ["HILAAS_RUN_ID"], commit,
                       Path(os.environ["HILAAS_LEDGER_DIR"]), args.output / "hardware",
                       os.environ.get("HILAAS_TEST_SUITE", "both"))


if __name__ == "__main__":
    main()
