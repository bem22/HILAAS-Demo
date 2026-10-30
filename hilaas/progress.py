"""Target-neutral stage events and live rendering for Spread-owned operations."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import wraps
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid


STAGES = {
    "workshop.launch": "Workshop launch",
    "workshop.build": "Workshop build and build-time tests",
    "workshop.stop": "Workshop shutdown",
    "bundle.validate": "Target bundle validation",
    "worker.allocate": "Spread worker allocation",
    "worker.tests": "Worker contract tests",
    "worker.discard": "Spread worker cleanup",
    "testflinger.preflight": "Testflinger queue and agent preflight",
    "testflinger.submit": "Testflinger job submission",
    "testflinger.attachments": "Testflinger attachment upload",
    "testflinger.wait": "Wait for target execution and cleanup",
    "testflinger.evidence": "Download and verify target evidence",
}
IDENTIFIER = r"[A-Za-z0-9_-]{1,100}"


def emit(event):
    directory = os.environ.get("HILAAS_PROGRESS_DIR")
    if not directory:
        return
    try:
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        destination = root / (event["operation"] + "-" + event["status"] + ".json")
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(event) + "\n")
        temporary.chmod(0o644)
        temporary.replace(destination)
    except OSError:
        print("Progress recording unavailable; operation is not retried.", file=sys.stderr, flush=True)


@contextmanager
def stage(name):
    if name not in STAGES:
        raise ValueError("Unknown progress stage")
    if not os.environ.get("HILAAS_PROGRESS_DIR"):
        yield
        return
    event = {"version": 1, "run_id": os.environ["HILAAS_RUN_ID"],
             "target": os.environ["HILAAS_TARGET"], "stage": name,
             "operation": uuid.uuid4().hex, "status": "running",
             "timestamp": datetime.now(timezone.utc).isoformat()}
    started = time.monotonic()
    emit(event)
    try:
        yield
    except BaseException:
        event["status"] = "failed"
        raise
    else:
        event["status"] = "passed"
    finally:
        event["duration"] = round(time.monotonic() - started, 3)
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        emit(event)


def tracked(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with stage(name):
                return function(*args, **kwargs)
        return call
    return decorate


def events(root, run_id, target):
    found = []
    for path in sorted([*root.glob("*.json"), *(root / "worker").glob("*.json")])[:1000]:
        try:
            if path.is_symlink() or path.stat().st_size > 4096:
                continue
            event = json.loads(path.read_text())
            if (event.get("version") != 1 or event.get("run_id") != run_id or event.get("target") != target
                    or event.get("stage") not in STAGES or event.get("status") not in ("running", "passed", "failed")
                    or not re.fullmatch(r"[0-9a-f]{32}", event.get("operation", ""))
                    or not re.fullmatch(r"[0-9T:.+Z-]{20,40}", event.get("timestamp", ""))):
                continue
            duration = event.get("duration", 0)
            if type(duration) not in (int, float) or not 0 <= duration <= 86400:
                continue
            found.append(event)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return sorted(found, key=lambda event: (event["timestamp"], event["status"] != "running"))


def render(event):
    title = f"[{event['target']}] {STAGES[event['stage']]}: {event['status']}"
    print("::group::" + title, flush=True)
    print(f"UTC: {event['timestamp']} | run: {event['run_id']}", flush=True)
    if "duration" in event:
        print(f"Duration: {event['duration']:.3f}s", flush=True)
    print("::endgroup::", flush=True)


def table(root, run_id, target):
    operations = {}
    for event in events(root, run_id, target):
        operations[event["operation"]] = event
    lines = ["## Observed Stages", "", "| Target | Stage | Status | Duration (s) | UTC |",
             "| --- | --- | --- | --- | --- |"]
    for event in operations.values():
        status = "incomplete" if event["status"] == "running" else event["status"]
        lines.append(f"| {target} | {STAGES[event['stage']]} | {status} | {event.get('duration', '-')} | {event['timestamp']} |")
    if not operations:
        lines.append(f"| {target} | No stage events available | unobserved | - | - |")
    lines += ["", "Stage events describe observed orchestration, not device attestation. Missing events are not passes.",
              "The Testflinger wait duration includes queueing, target execution, and cleanup; it is not flash or device-runtime timing."]
    return "\n".join(lines) + "\n"


def run(command, root, target):
    run_id = os.environ["HILAAS_RUN_ID"]
    if not re.fullmatch(IDENTIFIER, run_id) or not re.fullmatch(IDENTIFIER, target):
        raise ValueError("Invalid run or target identity")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    (root / "worker").mkdir(mode=0o755)
    environment = dict(os.environ, HILAAS_PROGRESS_DIR=str(root), HILAAS_TARGET=target)
    seen = set()
    def forward():
        for event in events(root, run_id, target):
            key = (event["operation"], event["status"])
            if key not in seen:
                render(event)
                seen.add(key)
    with subprocess.Popen(command, env=environment) as process:
        while process.poll() is None:
            forward()
            time.sleep(0.2)
        forward()
        return process.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--target", required=True)
    runner.add_argument("command", nargs=argparse.REMAINDER)
    step = commands.add_parser("step")
    step.add_argument("stage", choices=STAGES)
    step.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A command is required")
    if args.action == "run":
        return run(command, args.output, args.target)
    with stage(args.stage):
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())