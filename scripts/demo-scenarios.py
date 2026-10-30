"""Run named offline guard demonstrations without Testflinger or device access."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import xml.etree.ElementTree as element_tree


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    "baseline": ("Compile and execute the local native binary; mock all Testflinger traffic", [
        "tests/test_mock.py::MockAgentTests::test_real_native_binary_runs_in_sandbox"]),
    "build-failure": ("A failed Workshop build produces no transferable bundle", [
        "tests/test_mock.py::MockAgentTests::test_failed_workshop_build_produces_no_bundle",
        "tests/test_mock.py::MockAgentTests::test_missing_bundle_never_submits"]),
    "integrity": ("Corrupt binaries and wrong-chip images are rejected", [
        "tests/test_mock.py::MockAgentTests::test_native_bundle_rejects_corrupt_binary",
        "tests/test_bundle.py::BundleTests::test_corruption",
        "tests/test_bundle.py::BundleTests::test_wrong_chip_even_with_matching_hash"]),
    "routing": ("Unexpected queue ownership and a different device queue are rejected", [
        "tests/test_mock.py::MockAgentTests::test_client_rejects_unexpected_queue_owner",
        "tests/test_device.py::DeviceTests::test_other_queue_rejected"]),
    "usb-missing": ("An absent USB path prevents all flasher and serial calls", [
        "tests/test_device.py::DeviceTests::test_missing_usb_port_blocks_device_access"]),
    "usb-wrong-device": ("A different physical MCU identity prevents write_flash", [
        "tests/test_device.py::DeviceTests::test_wrong_physical_mcu_never_flashes"]),
    "usb-disconnect": ("A simulated failed USB flash is logged and never retried", [
        "tests/test_device.py::DeviceTests::test_usb_failure_during_flash_is_not_retried"]),
    "partition-policy": ("An unapproved partition hash prevents job submission", [
        "tests/test_client.py::ClientTests::test_wrong_partition_never_submits"]),
    "host-disabled": ("A host-disabled device cannot pass preflight", [
        "tests/test_device.py::DeviceTests::test_disabled_device"]),
    "stale-proof": ("Wrong build identity, failed self-tests, and stale serial nonces are rejected", [
        "tests/test_mock.py::MockAgentTests::test_native_output_must_match_build_and_pass",
        "tests/test_client.py::ClientTests::test_serial_proof"]),
    "runtime-failure": ("A failed or timed-out native process cannot produce passing evidence", [
        "tests/test_mock.py::MockAgentTests::test_native_process_failure_never_reports_pass"]),
    "submission-uncertain": ("An ambiguous POST cannot cause a second submission with the same run ID", [
        "tests/test_mock.py::MockAgentTests::test_client_does_not_repeat_uncertain_submission",
        "tests/test_client.py::ClientTests::test_ambiguous_submission_blocks_repeat"]),
    "evidence": ("Complete job state alone cannot hide failed phases or mismatched evidence", [
        "tests/test_mock.py::MockAgentTests::test_client_rejects_failed_phase_or_mismatched_evidence",
        "tests/test_client.py::ClientTests::test_evidence"]),
}


def run_scenario(name, output):
    expectation, tests = SCENARIOS[name]
    report = output / (name + ".xml")
    command = [sys.executable, "-m", "pytest", "-v", "-rA", "-o", "addopts=",
               "-p", "no:cacheprovider", "--junitxml=" + str(report), *tests]
    environment = dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD="1", PYTHONPATH=str(ROOT))
    print(f"\n{name}: {expectation}", flush=True)
    try:
        completed = subprocess.run(command, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        text, returncode = completed.stdout, completed.returncode
    except subprocess.TimeoutExpired as error:
        text = error.stdout or b""
        if isinstance(text, bytes):
            text = text.decode(errors="replace")
        text += "\nScenario exceeded 120 seconds.\n"
        returncode = 124
    (output / (name + ".log")).write_text(text)
    print(text, end="", flush=True)
    counts = {"tests": 0, "skipped": 0, "failures": 0, "errors": 0}
    try:
        cases = element_tree.parse(report).getroot().findall(".//testcase")
        counts["tests"] = len(cases)
        for field, tag in (("skipped", "skipped"), ("failures", "failure"), ("errors", "error")):
            counts[field] = sum(case.find(tag) is not None for case in cases)
    except (OSError, element_tree.ParseError):
        counts["errors"] += 1
    status = "passed"
    if returncode or not counts["tests"] or counts["failures"] or counts["errors"]:
        status = "failed"
    elif counts["skipped"]:
        status = "incomplete"
    return {"scenario": name, "status": status, "expected_protection": expectation,
            "returncode": returncode, **counts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*", help="Scenario names or all; omit to list")
    parser.add_argument("--list", action="store_true", help="List scenarios without running tests")
    parser.add_argument("--output", type=Path, help="New report directory; existing directories are refused")
    args = parser.parse_args()
    if args.list or not args.scenarios:
        for name, (expectation, _) in SCENARIOS.items():
            print(f"{name:22} {expectation}")
        return 0
    selected = list(SCENARIOS) if args.scenarios == ["all"] else args.scenarios
    if any(name not in SCENARIOS for name in selected) or len(set(selected)) != len(selected):
        parser.error("Choose distinct listed scenarios, or all by itself")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "artifacts" / ("demo-" + stamp + "-" + secrets.token_hex(4))).resolve()
    if output.exists():
        parser.error("Report directory already exists; choose a fresh directory")
    output.mkdir(parents=True, mode=0o700)
    summary = {"kind": "offline-guard-demo", "hardware": False, "testflinger_submitted": False,
               "started_at": stamp, "status": "running", "scenarios": []}
    print("OFFLINE DEMO: expected rejections are passing tests; no MCU access or real Testflinger jobs.")
    print("Reports:", output, flush=True)
    for name in selected:
        summary["scenarios"].append(run_scenario(name, output))
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    summary["status"] = "passed" if all(result["status"] == "passed" for result in summary["scenarios"]) else "not-passed"
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("\nScenario summary:")
    for result in summary["scenarios"]:
        print(f"{result['scenario']:22} {result['status']}")
    print("Reports:", output)
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())