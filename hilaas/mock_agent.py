"""Restricted native-binary and legacy data-only test phases; no hardware."""

import json
import re
import subprocess
from pathlib import Path
from uuid import UUID

from hilaas.bundle import read_json, require
from hilaas.native import BINARY, MANIFEST, TOKEN as NATIVE_TOKEN, validate, verify_output


QUEUE = "hilaas-laptop-mock"
TOKEN = "HILAAS_MOCK_V1"


def execute(job_path, evidence_path):
    job = read_json(Path(job_path))
    require(job.get("job_queue") == QUEUE, "Wrong mock queue")
    require(not any(job.get(phase + "_data") for phase in
                    ("setup", "provision", "firmware_update", "allocate", "reserve")),
            "The mock accepts only a test phase")
    data = job.get("test_data", {})
    native = isinstance(data, dict) and data.get("test_cmds") == NATIVE_TOKEN
    fields = {"test_cmds", "attachments"} if native else {"test_cmds"}
    require(isinstance(data, dict) and set(data) == fields, "Invalid mock payload")
    require(data["test_cmds"] in (TOKEN, NATIVE_TOKEN), "Submitted commands are forbidden")
    run_id = job.get("name")
    tags = job.get("tags", [])
    require(isinstance(run_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid run ID")
    require(isinstance(tags, list) and len(tags) == (3 if native else 1) and isinstance(tags[0], str)
            and re.fullmatch(r"[0-9a-f]{32}", tags[0]), "Invalid nonce")
    job_id = str(UUID(job["job_id"]))
    result = {"kind": "mock", "status": "passed", "agent": QUEUE,
              "job_id": job_id, "run_id": run_id, "nonce": tags[0],
              "hardware": False, "flashed": False}
    evidence = Path(evidence_path)
    evidence.mkdir(parents=True, exist_ok=True)
    if native:
        require(data["attachments"] == [{"agent": BINARY}, {"agent": MANIFEST}], "Unexpected native attachments")
        directory = Path(job_path).parent / "attachments/test"
        manifest, _ = validate(directory)
        require(manifest["run_id"] == run_id and tags == [manifest["nonce"], manifest["binary_sha256"], manifest["commit"]], "Native job metadata mismatch")
        binary = (directory / BINARY).resolve(strict=True)
        binary.chmod(0o500)
        command = ["/usr/bin/prlimit", "--fsize=65536:65536", "--cpu=10:10", "--core=0:0", "--",
               "/usr/bin/bwrap", "--unshare-all", "--die-with-parent", "--new-session",
                   "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
                   "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                   "--tmpfs", "/tmp", "--ro-bind", str(binary), "/tmp/" + BINARY,
                   "--chdir", "/tmp", "--clearenv", "/tmp/" + BINARY]
        with (evidence / "native-output.log").open("x") as log:
            completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=20, check=False)
        require(completed.returncode == 0, "Native executable failed; inspect native-output.log")
        require((evidence / "native-output.log").stat().st_size <= 65536, "Native output too large")
        verify_output((evidence / "native-output.log").read_text(), manifest)
        result.update(kind="native", commit=manifest["commit"], binary_sha256=manifest["binary_sha256"], exit_code=0, checks=5)
    with (evidence / "mock-result.json").open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(json.dumps(result))
    return result


if __name__ == "__main__":
    execute(Path.cwd() / "testflinger.json", Path.cwd() / "artifacts")