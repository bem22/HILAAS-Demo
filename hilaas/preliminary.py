"""Build Zephyr targets in host Workshops without any Testflinger operations."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess

from hilaas.bundle import digest, read_json, require
from hilaas.progress import stage, table


FILES = ("zephyr.elf", "zephyr.hex", "zephyr.bin")
STOP_MESSAGE = "PRELIMINARY_BUILD_COMPLETE: binaries verified; Testflinger handoff deliberately blocked. No job submitted or device flashed."


def selected(target):
    return ("microbit", "nucleo") if target == "zephyr-all" else (target,)


def profile(source, target):
    targets = read_json(source / "targets.json")
    require(target in targets, "Unknown preliminary target")
    config = targets[target]
    require(config.get("build_enabled") is True and config.get("adapter") == "zephyr", "Target has no approved preliminary build")
    return config


def validate(bundle, target, board, commit, run_id):
    manifest = read_json(bundle / "build-manifest.json")
    require(manifest.get("kind") == "zephyr-preliminary" and manifest.get("target") == target
            and manifest.get("board") == board and manifest.get("commit") == commit
            and manifest.get("run_id") == run_id, "Preliminary build identity mismatch")
    require(re.fullmatch(r"[0-9a-f]{32}", manifest.get("nonce", "")), "Invalid build nonce")
    require(set(manifest.get("files", {})) == set(FILES), "Missing build outputs")
    for name in FILES:
        path = bundle / name
        require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= 16 * 2**20, "Invalid build output")
        content = path.read_bytes()
        require(manifest["files"][name] == {"sha256": digest(content), "size": len(content)}, "Build output digest mismatch")
        if name == "zephyr.elf":
            require(content[:7] == b"\x7fELF\x01\x01\x01" and content[18:20] == b"\x28\x00", "Expected 32-bit ARM ELF")
            require(all(value.encode() in content for value in (commit, run_id, manifest["nonce"], board)), "ELF is missing build identity")
    return manifest


def build(source, root, project, target, commit, run_id):
    import yaml

    config = profile(source, target)
    output = root / "targets" / target
    require(not project.exists() and not output.exists(), "Use fresh Workshop and artifact directories")
    output.mkdir(parents=True)
    shutil.copytree(source / config["workshop"], project)
    recipe = project / "workshop.yaml"
    definition = yaml.safe_load(recipe.read_text())
    definition["name"] = "hilaas-zephyr-" + digest((run_id + "-" + target).encode())[:12]
    recipe.write_text(yaml.safe_dump(definition, sort_keys=False))
    for name in ("common", "zephyr"):
        shutil.copytree(source / "firmware" / name, project / "firmware" / name)
    nonce = secrets.token_hex(16)
    command = ["workshop", "-p", str(project)]
    launched = False
    try:
        with stage("workshop.launch"), (output / "workshop-launch.log").open("w") as log:
            subprocess.run([*command, "launch"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1800)
        launched = True
        with stage("workshop.build"), (output / "workshop-build.log").open("w") as log:
            subprocess.run([*command, "run", "--non-interactive", "--timeout", "20m",
                            "--env", "HILAAS_BOARD=" + config["board"], "--env", "HILAAS_COMMIT=" + commit,
                            "--env", "HILAAS_RUN_ID=" + run_id, "--env", "HILAAS_NONCE=" + nonce, "--", "build"],
                           stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1260)
        with stage("bundle.validate"):
            bundle = output / "bundle"
            bundle.mkdir()
            manifest = {"kind": "zephyr-preliminary", "target": target, "board": config["board"],
                        "commit": commit, "run_id": run_id, "nonce": nonce, "files": {}}
            for name in FILES:
                original = project / "build/zephyr/zephyr" / name
                require(original.is_file() and not original.is_symlink(), "Workshop did not produce " + name)
                shutil.copyfile(original, bundle / name)
                content = (bundle / name).read_bytes()
                manifest["files"][name] = {"sha256": digest(content), "size": len(content)}
            (bundle / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            validate(bundle, target, config["board"], commit, run_id)
    finally:
        if launched:
            with stage("workshop.stop"), (output / "workshop-stop.log").open("w") as log:
                subprocess.run([*command, "stop"], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    return manifest


def report(source, root, target, commit, run_id, outcome):
    lines = ["## Preliminary Build Checkpoint", "",
             "Testflinger submission, flashing, and device runtime tests: **NOT RUN**.", ""]
    for name in selected(target):
        lines.append(table(root / "progress", run_id, name))
    try:
        receipt = read_json(root / "preliminary.json")
        require(outcome == "failure" and receipt == {"run_id": run_id, "commit": commit,
                "targets": list(selected(target)), "status": "build_complete", "testflinger_submitted": False,
                "flashed": False}, "Missing deliberate-stop checkpoint")
        for name in selected(target):
            config = profile(source, name)
            manifest = validate(root / "targets" / name / "bundle", name, config["board"], commit, run_id)
            lines += [f"### {name}: {config['board']}", "", f"Source: `{commit}`. Run: `{run_id}`.", "",
                      "| File | SHA256 | Bytes |", "| --- | --- | --- |"]
            lines += [f"| {filename} | `{entry['sha256']}` | {entry['size']} |" for filename, entry in manifest["files"].items()]
    except Exception as error:
        lines += ["", f"**BUILD INCOMPLETE ({type(error).__name__}): this is not the expected preliminary stop.**"]
        return "\n".join(lines) + "\n", False
    lines += ["", "**EXPECTED STOP AFTER SUCCESSFUL BUILDS**", "", STOP_MESSAGE]
    return "\n".join(lines) + "\n", True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    source = Path.cwd()
    commit, run_id, target = (os.environ[name] for name in ("HILAAS_COMMIT", "HILAAS_RUN_ID", "HILAAS_TARGET"))
    require(os.environ.get("HILAAS_MODE") == "preliminary" and os.environ.get("HILAAS_ENABLE_HARDWARE") == "no", "Preliminary mode must disable hardware")
    require(os.environ.get("HILAAS_ALLOW_DOWNLOADS") == "yes", "SDK download permission required")
    require(re.fullmatch(r"[0-9a-f]{40}", commit) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid build identity")
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == commit, "Dispatched commit mismatch")
    require(not subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=normal"], text=True), "Clean committed source required")
    root = source / "artifacts" / run_id
    require(not (root / "preliminary.json").exists(), "Preliminary run already completed")
    try:
        for name in selected(target):
            os.environ["HILAAS_TARGET"] = name
            build(source, root, Path.home() / ".local/state/HILAAS-Demo/preliminary-builds" / run_id / name,
                  name, commit, run_id)
    finally:
        os.environ["HILAAS_TARGET"] = target
    (root / "preliminary.json").write_text(json.dumps({"run_id": run_id, "commit": commit,
        "targets": list(selected(target)), "status": "build_complete", "testflinger_submitted": False, "flashed": False}, indent=2) + "\n")
    print(STOP_MESSAGE, flush=True)


if __name__ == "__main__":
    main()