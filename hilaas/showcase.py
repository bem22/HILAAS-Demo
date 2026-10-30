"""Publish target-neutral topology with target-specific evidence verification."""

import argparse
import os
from pathlib import Path

from hilaas.bundle import read_json, require
from hilaas.bundle import digest, validate as validate_firmware
from hilaas.client import validate_config, verify_evidence as verify_firmware_evidence
from hilaas.mock_client import verify_evidence
from hilaas.native import validate
from hilaas.progress import table


ARCHITECTURE = """## Architecture: Source to Target

```mermaid
flowchart LR
    GitHub[GitHub: exact source commit] --> Runner[AWS self-hosted runner]
    Runner --> Spread[Spread: orchestration]
    Spread --> Workshop[Workshop: target SDK, build and build-time tests]
    Workshop --> Worker[Spread worker: target bundle and manifest]
    Worker --> TF[Testflinger: restricted queue and attachments]
    TF --> Agent[Testflinger agent: target-specific adapter]
    Agent --> Native[Laptop target: sandboxed native execution]
    Agent --> MCU[MCU target: identity check, flash, verify, runtime test]
    Native --> Evidence[Testflinger: phase results and target proof]
    MCU --> Evidence
    Evidence --> Verify[Spread client: evidence verification]
    Verify --> Artifacts[GitHub: independent verification and artifacts]
```

| Boundary | Responsibility |
| --- | --- |
| GitHub -> AWS runner | Check out the exact dispatched commit; manual dispatch only |
| Runner -> Spread -> Workshop | Select the target SDK and build recipe; native builds run CTest, MCU builds produce firmware |
| Workshop -> Spread worker | Validate the target bundle and manifest; Workshop runs on the host for native builds and in the worker for the current MCU path |
| Worker -> Testflinger | Check the dedicated queue, submit once, and upload the binary attachments |
| Testflinger -> target adapter | Native: sandboxed execution. MCU: device identity, flash policy, one write, verification, and runtime proof |
| Target -> Testflinger -> Spread -> GitHub | Return phase results, verify target-specific evidence, and publish artifacts |

Only the selected target and mode are exercised. Build-only mode never submits a hardware job.
The laptop is the verified native reference target; ESP32-C6 hardware integration remains unverified.
micro:bit V2 and NUCLEO-H753ZI support preliminary builds only; their hardware adapters remain disabled.
Offline failure scenarios and the proposed device registry are not live hardware evidence.
"""


def native_evidence(root, run_id, commit):
    manifest, _ = validate(root / "build/bundle")
    receipt = read_json(root / "laptop/receipt.json")
    results = read_json(root / "laptop/phase-results.json")
    require(manifest["run_id"] == receipt["run_id"] == run_id, "Run identity mismatch")
    require(manifest["commit"] == receipt["commit"] == commit, "Source identity mismatch")
    require(manifest["nonce"] == receipt["nonce"], "Nonce mismatch")
    require(manifest["binary_sha256"] == receipt["binary_sha256"], "Binary identity mismatch")
    require(receipt["kind"] == "native" and receipt["stage"] == "complete"
            and receipt["status"] == "pass", "Missing native completion receipt")
    proof = verify_evidence(root / "laptop/artifacts.tar.gz", results, receipt)
    return [
        ("Source commit", manifest["commit"]), ("Run identity", manifest["run_id"]),
        ("Binary SHA256", manifest["binary_sha256"]), ("Per-build nonce", manifest["nonce"]),
        ("Testflinger job", proof["job_id"]), ("Agent / queue", proof["agent"]),
        ("Testflinger setup / test / cleanup", "0 / 0 / 0"),
        ("Native self-tests / exit code", f"{proof['checks']} passed / {proof['exit_code']}"),
        ("MCU access / flash", "false / false"),
    ]


def firmware_evidence(root, target, run_id, commit, mode, config_path):
    output = root / "targets" / target
    manifest, captured = validate_firmware(output / "bundle")
    require(manifest["commit"] == commit, "Source identity mismatch")
    manifest_bytes = (output / "bundle/manifest.json").read_bytes()
    rows = [("Source commit", manifest["commit"]), ("Run identity", run_id),
            ("Firmware manifest SHA256", digest(manifest_bytes)), ("Per-build nonce", manifest["nonce"])]
    if mode == "build":
        require(not (output / "hardware/receipt.json").exists(), "Unexpected hardware submission in build-only run")
        return rows + [("Hardware submission / flash / runtime", "not run (build-only)")]
    require(config_path is not None, "Private target configuration is required for hardware evidence")
    config = validate_config(read_json(config_path))
    require(digest(captured["partition-table.bin"]) == config["partition_sha256"], "Partition policy mismatch")
    if (output / "hardware/summary.json").exists():
        summary = read_json(output / "hardware/summary.json")
        require(summary["status"] == "pass" and summary["run_id"] == run_id
            and summary["commit"] == commit, "Incomplete firmware demo")
        require(summary["selected"] in (["crc32"], ["uint32-wrap"], ["crc32", "uint32-wrap"]), "Invalid demo suites")
        require([result["suite"] for result in summary["tests"]] == summary["selected"], "Missing suite result")
        for result in summary["tests"]:
            suite = result["suite"]
            directory = output / "hardware" / suite
            receipt = read_json(directory / "receipt.json")
            require(receipt["status"] == "pass" and receipt["stage"] == "complete"
                and receipt["run_id"] == run_id + "-" + suite
                and receipt["target"] == target and receipt["manifest_sha256"] == digest(manifest_bytes),
                "Invalid suite receipt")
            proof = verify_firmware_evidence(directory / "artifacts.tar.gz",
                             read_json(directory / "phase-results.json"), manifest_bytes,
                             dict(config, suite=suite))
            require(result["status"] == "pass" and result["job_id"] == receipt["job_id"]
                and result["proof"] == receipt["proof"] == proof, "Suite proof mismatch")
            rows.append((suite + " / Testflinger job", "PASS / " + receipt["job_id"]))
        return rows
    receipt = read_json(output / "hardware/receipt.json")
    require(receipt["target"] == target and receipt["run_id"] == run_id, "Target/run identity mismatch")
    require(receipt["manifest_sha256"] == digest(manifest_bytes), "Firmware identity mismatch")
    require(receipt["stage"] == "complete" and receipt["status"] == "pass", "Missing hardware completion receipt")
    results = read_json(output / "hardware/phase-results.json")
    proof = verify_firmware_evidence(output / "hardware/artifacts.tar.gz", results, manifest_bytes, config)
    require(receipt["proof"] == proof, "Hardware receipt differs from verified proof")
    return rows + [("Testflinger job", receipt["job_id"]), ("Agent / queue", config["agent"] + " / " + config["queue"]),
                   ("Testflinger setup / test / cleanup", "0 / 0 / 0"),
                   ("Device identity", proof["device_mac"]), ("Verified flash count", proof["flash_count"]),
                   ("Runtime proof", "three increasing device/nonce sequences")]


def report(root, run_id, commit, pipeline_outcome, target="laptop", mode="native", config_path=None):
    if mode == "preliminary":
        from hilaas.preliminary import report as preliminary_report
        return preliminary_report(Path(__file__).resolve().parents[1], root, target, commit, run_id, pipeline_outcome)
    lines = ["## Downstream Execution Evidence", "",
             f"Spread orchestration outcome: **{pipeline_outcome}**", ""]
    lines.append(table(root / "progress", run_id, target))
    try:
        require(pipeline_outcome == "success", "Spread did not succeed")
        targets = read_json(Path(__file__).resolve().parents[1] / "targets.json")
        require(target in targets and targets[target]["enabled"], "Target is disabled or unknown")
        adapter = targets[target].get("adapter")
        if adapter == "native":
            require(mode == "native", "Native target cannot flash firmware")
            rows = native_evidence(root, run_id, commit)
        elif adapter == "esp32c6":
            require(mode in ("build", "hardware"), "Firmware target requires build or hardware mode")
            rows = firmware_evidence(root, target, run_id, commit, mode, config_path)
        else:
            raise ValueError("No evidence adapter for this target")
    except Exception as error:
        lines += ["**INCOMPLETE: end-to-end success has not been verified.**", "",
                  f"Verification stopped with `{type(error).__name__}`. Inspect the failed step and available artifacts.",
                  "Do not resubmit automatically: an existing Testflinger job may still be active."]
        return "\n".join(lines) + "\n", False
    scope = "build evidence only; hardware execution NOT RUN" if mode == "build" else "target execution evidence verified"
    lines += [f"**PASS: {scope}.**", "", f"Target: `{target}`. Mode: `{mode}`.", "",
              "| Evidence | Verified value |", "| --- | --- |"]
    lines += [f"| {label} | `{value}` |" for label, value in rows]
    lines += ["",
              "### Evidence to Inspect", "",
              "- `build/workshop-build.log`: compiler and CTest output.",
              "- `build/bundle/`: compiled ELF binary and source/run/digest manifest.",
              "- `laptop/receipt.json`: Testflinger job identity and completion receipt.",
              "- `laptop/phase-results.json`: downstream phase statuses.",
              "- `laptop/artifacts.tar.gz`: laptop proof and `artifacts/native-output.log`.",
              "- `targets/<target>/bundle/`: MCU firmware and manifest (MCU runs).",
              "- `targets/<target>/hardware/`: receipt, phase results, and flash/serial proof (hardware mode only).",
              "- `showcase.md`: this run's architecture and verification report.", "",
              "Artifact publication is a separate step; check its outcome before sharing the bundle."]
    return "\n".join(lines) + "\n", True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--outcome", choices=("success", "failure", "cancelled", "skipped"))
    parser.add_argument("--target", default="laptop")
    parser.add_argument("--mode", choices=("native", "build", "hardware", "preliminary"), default="native")
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    text, passed = ARCHITECTURE, True
    text += f"\nSelected target: `{args.target}`. Mode: `{args.mode}`.\n"
    if args.report is not None:
        require(args.outcome is not None, "Missing orchestration outcome")
        text, passed = report(args.report, os.environ["HILAAS_RUN_ID"],
                              os.environ["HILAAS_COMMIT"], args.outcome, args.target, args.mode, args.config)
        args.report.mkdir(parents=True, exist_ok=True)
        (args.report / "showcase.md").write_text(ARCHITECTURE + "\n" + text)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as summary:
            summary.write(text + "\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())