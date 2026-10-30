"""Single-submit mock pipeline; deliberately independent of firmware builds."""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import tarfile
import time
import uuid
from urllib.parse import urlsplit

from hilaas.bundle import read_json, require, unique_keys
from hilaas.client import save
from hilaas.mock_agent import QUEUE, TOKEN


SUBMITTER = "hilaas-laptop-mock-submitter"
AGENT_CLIENT = "hilaas-laptop-mock-agent"


class MockAPI:
    def __init__(self, server, token_file):
        import requests

        address = urlsplit(server)
        require(address.scheme in ("http", "https") and address.hostname and not address.username
                and not address.password and not address.query and not address.fragment
                and address.path in ("", "/"), "Invalid private server URL")
        credentials = read_json(Path(token_file))
        require(credentials.get("client_id") == SUBMITTER, "Use the dedicated mock submitter")
        self.server = server.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False
        response = self.session.post(self.server + "/v1/oauth2/refresh",
                                     json={"refresh_token": credentials["refresh_token"]},
                                     timeout=(10, 30), allow_redirects=False)
        require(response.status_code == 200, f"Mock authentication: HTTP {response.status_code}")
        self.session.headers["Authorization"] = "Bearer " + response.json()["access_token"]

    def request(self, method, path, **kwargs):
        response = self.session.request(method, self.server + path, timeout=(10, 30), allow_redirects=False, **kwargs)
        require(200 <= response.status_code < 300, f"Mock {method} {path}: HTTP {response.status_code}")
        return response

    def get_json(self, path):
        response = self.request("GET", path)
        return None if response.status_code == 204 else response.json()


def verify_evidence(path, results, receipt):
    require(results.get("job_state") == "complete", "Mock job did not complete")
    for phase in ("setup", "test", "cleanup"):
        status = results.get(phase + "_status")
        require(type(status) is int and status == 0, f"Mock {phase} failed or missing")
    proof = None
    total = 0
    with tarfile.open(path) as archive:
        for index, member in enumerate(archive):
            total += member.size
            require(index < 32 and total <= 2**20, "Oversized mock evidence")
            if member.name == "artifacts/mock-result.json":
                require(proof is None and member.isfile() and member.size <= 65536, "Unsafe mock evidence")
                with archive.extractfile(member) as stream:
                    proof = json.loads(stream.read(), object_pairs_hook=unique_keys)
    expected = {"kind": "mock", "status": "passed", "agent": QUEUE,
                "job_id": receipt["job_id"], "run_id": receipt["run_id"], "nonce": receipt["nonce"],
                "hardware": False, "flashed": False}
    require(proof == expected, "Missing or mismatched mock evidence")
    return proof


def execute(api, run_id, output, ledger_dir, timeout=180, clock=time.monotonic, wait=time.sleep):
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid mock run ID")
    output, ledger_dir = Path(output), Path(ledger_dir)
    output.mkdir(parents=True, exist_ok=True)
    ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = ledger_dir / (run_id + "-mock.json")
    require(not ledger.exists(), "Mock submission already attempted; inspect the existing receipt")
    restriction = api.get_json("/v1/restricted-queues/" + QUEUE)
    require(restriction and set(restriction.get("owners", [])) == {SUBMITTER, AGENT_CLIENT}, "Mock queue has unexpected owners")
    agent = api.get_json("/v1/agents/data/" + QUEUE)
    require(agent and agent.get("state") == "waiting" and not agent.get("job")
            and agent.get("queues") == [QUEUE], "Expected an idle dedicated laptop mock")
    jobs = api.get_json("/v1/queues/" + QUEUE + "/jobs") or []
    require(not any(job.get("job_state") not in ("complete", "cancelled") for job in jobs), "Mock queue has outstanding work")
    receipt = {"kind": "mock", "job_id": str(uuid.uuid4()), "run_id": run_id,
               "nonce": secrets.token_hex(16), "stage": "submission_intent", "status": "pending"}
    with ledger.open("x") as stream:
        os.chmod(ledger, 0o600)
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    job = dict(job_id=receipt["job_id"], job_queue=QUEUE, name=run_id,
               tags=[receipt["nonce"]], test_data={"test_cmds": TOKEN})
    save(output / "receipt.json", receipt)
    try:
        response = api.request("POST", "/v1/job", json=job).json()
        require(response.get("job_id") == receipt["job_id"], "Server changed mock job ID")
        print("Submitted mock job " + receipt["job_id"], flush=True)
        receipt["stage"] = "waiting"
        deadline = clock() + timeout
        while True:
            results = api.get_json("/v1/result/" + receipt["job_id"])
            save(output / "phase-results.json", results)
            if results.get("job_state") in ("complete", "cancelled"):
                break
            if clock() >= deadline:
                raise TimeoutError("Mock still pending; no automatic resubmission or cancellation")
            wait(2)
        receipt["stage"] = "evidence"
        path = output / "artifacts.tar.gz"
        with api.request("GET", "/v1/result/" + receipt["job_id"] + "/artifact", stream=True) as response:
            size = 0
            with path.open("wb") as stream:
                for chunk in response.iter_content(65536):
                    size += len(chunk)
                    require(size <= 2**20, "Mock evidence archive too large")
                    stream.write(chunk)
        proof = verify_evidence(path, results, receipt)
        save(output / "mock-result.json", proof)
        receipt.update(stage="complete", status="pass")
        return receipt
    except Exception as error:
        receipt.update(status="fail", error=type(error).__name__)
        raise
    finally:
        save(output / "receipt.json", receipt)
        save(ledger, receipt)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    api = MockAPI(args.server, args.token_file)
    result = execute(api, args.run_id, args.output, args.ledger)
    print(json.dumps(result))


if __name__ == "__main__":
    main()