"""Fixed Pi agent smoke check: inspect a serial node without opening it."""

import argparse
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
from uuid import UUID

from hilaas.bundle import read_json, require


TOKEN = "HILAAS_PI_SMOKE_V1"
SUBMITTER = "hilaas-pi-smoke-submitter"
AGENTS = {
    "hilaas-pi1-c6": "hilaas-esp32c6-pi1",
    "hilaas-pi1-microbit": "hilaas-microbit-pi1",
    "hilaas-pi1-nucleo": "hilaas-nucleo-pi1",
    "hilaas-pi2-esp32": "hilaas-esp32-pi2",
    "hilaas-pi2-c3": "hilaas-esp32c3-pi2",
}


def validate_job(job_path, agent_id):
    require(agent_id in AGENTS, "Unknown smoke agent")
    job = read_json(Path(job_path))
    require(job.get("job_queue") == AGENTS[agent_id], "Wrong smoke queue")
    require(not any(job.get(phase + "_data") for phase in
                    ("setup", "provision", "firmware_update", "allocate", "reserve")),
            "Smoke jobs accept only a test phase")
    require(job.get("test_data") == {"test_cmds": TOKEN}, "Submitted commands and attachments are forbidden")
    run_id = job.get("name")
    require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid run ID")
    tags = job.get("tags")
    require(isinstance(tags, list) and len(tags) == 2
            and isinstance(tags[0], str) and re.fullmatch(r"[0-9a-f]{32}", tags[0])
            and isinstance(tags[1], str) and re.fullmatch(r"[0-9a-f]{40}", tags[1]),
            "Invalid smoke nonce or commit")
    return {"job_id": str(UUID(job["job_id"])), "run_id": run_id,
            "nonce": tags[0], "commit": tags[1]}


def execute(job_path, evidence_path, agent_id, device_path="/dev/hilaas-mcu"):
    identity = validate_job(job_path, agent_id)
    source = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(
        ["/usr/bin/git", "-c", "safe.directory=" + str(source), "-C", str(source), "rev-parse", "HEAD"],
        text=True, timeout=10,
    ).strip()
    require(commit == identity["commit"], "Smoke source commit mismatch")
    require(platform.machine() == "aarch64", "Expected an ARM64 Pi agent")
    require(stat.S_ISCHR(os.stat(device_path).st_mode), "Assigned serial interface is not a character device")
    require(os.access(device_path, os.R_OK | os.W_OK), "Agent lacks serial interface permissions")
    result = {
        "kind": "pi-registration-smoke", "status": "passed", "agent": agent_id,
        "queue": AGENTS[agent_id], **identity, "architecture": "aarch64",
        "serial_node_present": True, "serial_access_allowed": True,
        "device_opened": False, "flashed": False, "firmware_executed": False,
    }
    evidence = Path(evidence_path)
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "pi-smoke-result.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    job = Path.cwd() / "testflinger.json"
    if args.validate_only:
        validate_job(job, args.agent)
    else:
        execute(job, Path.cwd() / "artifacts", args.agent)


if __name__ == "__main__":
    main()