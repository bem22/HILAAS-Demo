"""Submit one non-flashing Pi smoke job and verify its returned evidence."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import tarfile
import time
from urllib.parse import urlsplit
import uuid

from hilaas.bundle import read_json, require, unique_keys
from hilaas.client import save
from hilaas.pi_smoke import AGENTS, SUBMITTER, TOKEN


class SmokeAPI:
    def __init__(self, server, token_file):
        import jwt
        import requests

        address = urlsplit(server)
        require(address.scheme in ("http", "https") and address.hostname
                and not address.username and not address.password
                and not address.query and not address.fragment and address.path in ("", "/"),
                "Invalid smoke server URL")
        require(address.scheme == "https" or address.hostname in ("127.0.0.1", "localhost"),
                "HTTP smoke access must use a local private tunnel")
        token_file = Path(token_file)
        require(token_file.stat().st_mode & 0o077 == 0, "Smoke token must be private")
        credentials = read_json(token_file)
        require(credentials.get("client_id") == SUBMITTER, "Use the dedicated smoke contributor")
        self.server = server.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        response = self.session.post(self.server + "/v1/oauth2/refresh",
                                     json={"refresh_token": credentials["refresh_token"]},
                                     timeout=(10, 30), allow_redirects=False)
        require(response.status_code == 200, f"Smoke authentication: HTTP {response.status_code}")
        token = response.json()["access_token"]
        permissions = jwt.decode(token, options={"verify_signature": False}).get("permissions", {})
        require(permissions.get("client_id") == SUBMITTER and permissions.get("role") == "contributor"
                and set(permissions.get("allowed_queues", [])) == set(AGENTS.values()),
                "Unexpected smoke contributor permissions")
        self.session.headers["Authorization"] = "Bearer " + token

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.server + path, timeout=(10, 30),
                                        allow_redirects=False, **kwargs)
        require(200 <= response.status_code < 300, f"Smoke {method} {path}: HTTP {response.status_code}")
        return response

    def get_json(self, path):
        response = self.request("GET", path)
        return None if response.status_code == 204 else response.json()


def verify_evidence(path, results, receipt):
    require(results.get("job_state") == "complete", "Smoke job did not complete")
    for phase in ("setup", "test", "cleanup"):
        status = results.get(phase + "_status")
        require(type(status) is int and status == 0, f"Smoke {phase} failed or missing")
    require(results.get("provision_status", 0) == 0, "Smoke provision failed")
    proof = None
    total = 0
    with tarfile.open(path) as archive:
        for index, member in enumerate(archive):
            total += member.size
            require(index < 32 and total <= 2**20, "Oversized smoke evidence")
            if member.name == "artifacts/pi-smoke-result.json":
                require(proof is None and member.isfile() and member.size <= 65536, "Unsafe smoke evidence")
                with archive.extractfile(member) as stream:
                    proof = json.loads(stream.read(), object_pairs_hook=unique_keys)
    expected = {
        "kind": "pi-registration-smoke", "status": "passed", "agent": receipt["agent"],
        "queue": AGENTS[receipt["agent"]], "job_id": receipt["job_id"],
        "run_id": receipt["run_id"], "nonce": receipt["nonce"], "commit": receipt["commit"],
        "architecture": "aarch64", "serial_node_present": True, "serial_access_allowed": True,
        "device_opened": False, "flashed": False, "firmware_executed": False,
    }
    require(proof == expected, "Missing or mismatched smoke evidence")
    return proof


def execute(api, agent_id, run_id, commit, output, timeout=180, clock=time.monotonic, wait=time.sleep):
    require(agent_id in AGENTS, "Unknown smoke agent")
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid smoke run ID")
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid smoke commit")
    output = Path(output)
    require(not output.exists(), "Smoke submission already attempted; inspect the existing receipt")
    queue = AGENTS[agent_id]
    restriction = api.get_json("/v1/restricted-queues/" + queue)
    require(restriction and set(restriction.get("owners", [])) == {"testflinger-agent", SUBMITTER},
            "Smoke queue has unexpected owners")
    agent = api.get_json("/v1/agents/data/" + agent_id)
    require(agent and agent.get("state") == "waiting" and not agent.get("job")
            and agent.get("queues") == [queue], "Expected an idle dedicated smoke agent")
    jobs = api.get_json("/v1/queues/" + queue + "/jobs") or []
    require(not any(job.get("job_state") not in ("complete", "cancelled") for job in jobs),
            "Smoke queue has outstanding work")
    receipt = {"agent": agent_id, "job_id": str(uuid.uuid4()), "run_id": run_id,
               "nonce": secrets.token_hex(16), "commit": commit,
               "stage": "submission_intent", "status": "pending"}
    output.mkdir(parents=True, mode=0o700)
    with (output / "receipt.json").open("x") as stream:
        os.chmod(output / "receipt.json", 0o600)
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(output, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    print(f"Recorded {agent_id} job {receipt['job_id']} before submission", flush=True)
    try:
        job = {"job_id": receipt["job_id"], "job_queue": queue, "name": run_id,
               "tags": [receipt["nonce"], commit], "test_data": {"test_cmds": TOKEN}}
        response = api.request("POST", "/v1/job", json=job).json()
        require(response.get("job_id") == receipt["job_id"], "Server changed the smoke job ID")
        receipt["stage"] = "waiting"
        save(output / "receipt.json", receipt)
        deadline = clock() + timeout
        while True:
            results = api.get_json("/v1/result/" + receipt["job_id"]) or {}
            save(output / "phase-results.json", results)
            if results.get("job_state") in ("complete", "cancelled"):
                break
            if clock() >= deadline:
                raise TimeoutError("Smoke still pending; no resubmission or cancellation")
            wait(2)
        receipt["stage"] = "evidence"
        archive_path = output / "artifacts.tar.gz"
        with api.request("GET", "/v1/result/" + receipt["job_id"] + "/artifact", stream=True) as response:
            size = 0
            with archive_path.open("xb") as stream:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    require(size <= 2**20, "Smoke archive too large")
                    stream.write(chunk)
        proof = verify_evidence(archive_path, results, receipt)
        save(output / "pi-smoke-result.json", proof)
        receipt["stage"] = "agent_idle"
        while True:
            agent = api.get_json("/v1/agents/data/" + agent_id)
            if agent and agent.get("state") == "waiting" and not agent.get("job"):
                break
            if clock() >= deadline:
                raise TimeoutError("Smoke passed but agent has not returned to waiting")
            wait(2)
        receipt.update(stage="complete", status="pass")
        return receipt
    except Exception as error:
        receipt.update(status="fail", error=type(error).__name__)
        raise
    finally:
        save(output / "receipt.json", receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--agent", choices=AGENTS, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    api = SmokeAPI(args.server, args.token_file)
    result = execute(api, args.agent, args.run_id, args.commit, args.output)
    print(json.dumps(result))


if __name__ == "__main__":
    main()