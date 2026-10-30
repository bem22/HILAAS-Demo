"""Fixed, data-only Testflinger test phase; never touches hardware."""

import json
import re
from pathlib import Path
from uuid import UUID

from hilaas.bundle import read_json, require


QUEUE = "hilaas-laptop-mock"
TOKEN = "HILAAS_MOCK_V1"


def execute(job_path, evidence_path):
    job = read_json(Path(job_path))
    require(job.get("job_queue") == QUEUE, "Wrong mock queue")
    require(not any(job.get(phase + "_data") for phase in
                    ("setup", "provision", "firmware_update", "allocate", "reserve")),
            "The mock accepts only a test phase")
    data = job.get("test_data", {})
    require(isinstance(data, dict) and set(data) == {"test_cmds"}, "Invalid mock payload")
    require(data["test_cmds"] == TOKEN, "Submitted commands are forbidden")
    run_id = job.get("name")
    tags = job.get("tags", [])
    require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid run ID")
    require(isinstance(tags, list) and len(tags) == 1 and isinstance(tags[0], str)
            and re.fullmatch(r"[0-9a-f]{32}", tags[0]), "Invalid nonce")
    job_id = str(UUID(job["job_id"]))
    result = {"kind": "mock", "status": "passed", "agent": QUEUE,
              "job_id": job_id, "run_id": run_id, "nonce": tags[0],
              "hardware": False, "flashed": False}
    evidence = Path(evidence_path)
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "mock-result.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    execute(Path.cwd() / "testflinger.json", Path.cwd() / "artifacts")