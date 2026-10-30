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


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("targets", "identity", "package", "run"))
    parser.add_argument("--target", default=TARGET)
    parser.add_argument("--mode", choices=("build", "hardware"), default="build")
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts")
    args = parser.parse_args()
    targets = read_json(ROOT / "targets.json")
    if args.command == "targets":
        print(json.dumps(targets, indent=2))
        return
    require(args.target in targets and targets[args.target]["enabled"], "Target is unconfigured; confirm the exact board first")
    commit = os.environ.get("HILAAS_COMMIT", "")
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "HILAAS_COMMIT must identify committed source")
    build = ROOT / "build"
    if args.command == "identity":
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
        subprocess.run(["workshop", "run", "--non-interactive", "--timeout", "30m",
                        "--env", f"HILAAS_COMMIT={commit}", "--", "build"], cwd=ROOT, check=True)
        manifest, _ = validate(build / "bundle")
        require(manifest["commit"] == commit, "Build commit mismatch")
        destination = args.output / "bundle"
        require(not destination.exists(), "Use a fresh artifact directory")
        shutil.copytree(build / "bundle", destination)
        if args.mode == "hardware":
            from hilaas.client import API, execute
            execute(destination, config, API(config), os.environ["HILAAS_RUN_ID"], commit,
                    Path(os.environ["HILAAS_LEDGER_DIR"]), args.output / "hardware")


if __name__ == "__main__":
    main()
