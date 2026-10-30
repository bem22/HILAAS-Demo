"""Single-submit Testflinger client adapter using testflinger-cli authentication."""

import io
import json
import os
import re
import tarfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from hilaas.bundle import TARGET, digest, read_json, require, unique_keys, validate
from hilaas.progress import stage


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def validate_config(config):
    server = urlsplit(config["server"])
    require(server.scheme in ("http", "https") and server.hostname, "Invalid Testflinger URL")
    require(not server.username and not server.password and not server.query and not server.fragment, "URL must not contain credentials or query parameters")
    require(server.path in ("", "/"), "Use the server root URL")
    require(server.scheme == "https" or config.get("allow_trusted_http") is True, "HTTP requires explicit trusted-network acknowledgement")
    for field in ("queue", "agent", "submitter"):
        require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", config[field]), f"Invalid {field}")
    require(re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", config["device_mac"]), "Invalid device MAC")
    require(re.fullmatch(r"[0-9a-f]{64}", config["partition_sha256"]), "Pinned partition-table hash required")
    return config


class API:
    def __init__(self, config):
        import requests
        from testflinger_cli.auth import TestflingerCliAuth

        self.config = validate_config(config)
        self.server = config["server"].rstrip("/")
        require(os.environ.get("XDG_CONFIG_HOME"), "Set a dedicated Testflinger client authentication directory")
        self.auth = TestflingerCliAuth(self.server)
        self.session = requests.Session()
        self.session.trust_env = False

    def request(self, method, endpoint, **kwargs):
        import jwt

        token = self.auth.authenticate()
        require(bool(token), "Testflinger client login required")
        permissions = jwt.decode(token, options={"verify_signature": False}).get("permissions", {})
        require(permissions.get("role") == "contributor", "Dedicated contributor credentials required")
        require(self.config["queue"] in (permissions.get("allowed_queues") or []), "Missing queue permission")
        response = self.session.request(method, self.server + endpoint,
                                        headers={"Authorization": "Bearer " + token},
                                        timeout=(10, 60), allow_redirects=False, **kwargs)
        require(200 <= response.status_code < 300, f"Testflinger {method}: HTTP {response.status_code}")
        return response

    def get_json(self, endpoint):
        response = self.request("GET", endpoint)
        return None if response.status_code == 204 else response.json()


def attachment_archive(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo("test/" + name)
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def serial_proof(text, nonce, mac):
    sequences = []
    pattern = re.compile(r"HILAAS_C6_V1 mac=([0-9a-f:]+) nonce=([0-9a-f]{32}) seq=([0-9]+)")
    for line in text.splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            continue
        actual_mac, actual_nonce, sequence = match.groups()
        value = int(sequence)
        if actual_mac != mac or actual_nonce != nonce or value > 0xFFFFFFFF:
            sequences = []
            continue
        if sequences and value <= sequences[-1]:
            sequences = []
        sequences.append(value)
        if len(sequences) == 3:
            return sequences
    raise ValueError("No three-line device/nonce/sequence proof")


def verify_evidence(path, results, manifest_bytes, config):
    require(results.get("job_state") == "complete", "Hardware job did not complete")
    for phase in ("setup", "test", "cleanup"):
        status = results.get(phase + "_status")
        require(type(status) is int and status == 0, f"Failed or missing {phase} status")
    require(results.get("provision_status", 0) == 0, "Provision failed")
    wanted = {"artifacts/" + name for name in ("proof.json", "manifest.json", "serial.log", "junit.xml", "flash.log")}
    captured = {}
    total_size = 0
    with tarfile.open(path) as archive:
        for index, member in enumerate(archive):
            total_size += member.size
            require(index < 64 and total_size <= 10 * 2**20, "Oversized evidence archive")
            if member.name not in wanted:
                continue
            require(member.name not in captured and member.isfile() and member.size <= 2**20, "Unsafe evidence member")
            with archive.extractfile(member) as stream:
                captured[member.name] = stream.read(2**20 + 1)
    require(set(captured) == wanted, "Missing hardware evidence")
    require(captured["artifacts/manifest.json"] == manifest_bytes, "Returned firmware manifest differs")
    manifest = json.loads(manifest_bytes, object_pairs_hook=unique_keys)
    proof = json.loads(captured["artifacts/proof.json"], object_pairs_hook=unique_keys)
    expected = {"target": TARGET, "nonce": manifest["nonce"], "device_mac": config["device_mac"],
                "manifest_sha256": digest(manifest_bytes), "flash_count": 1,
                "sequences": serial_proof(captured["artifacts/serial.log"].decode(), manifest["nonce"], config["device_mac"])}
    require(proof == expected, "Hardware proof does not match submission")
    return proof


def execute(directory, config, api, run_id, commit, ledger_dir, output, timeout=600, clock=time.monotonic, wait=time.sleep):
    config = validate_config(config)
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,100}", run_id), "Invalid run ID")
    directory, output, ledger_dir = Path(directory), Path(output), Path(ledger_dir)
    output.mkdir(parents=True, exist_ok=True)
    manifest, captured = validate(directory)
    require(manifest["commit"] == commit, "Bundle belongs to a different source commit")
    require(digest(captured["partition-table.bin"]) == config["partition_sha256"], "Partition layout is not approved")
    manifest_bytes = (directory / "manifest.json").read_bytes()
    require(json.loads(manifest_bytes, object_pairs_hook=unique_keys) == manifest, "Manifest changed during preflight")
    captured["manifest.json"] = manifest_bytes
    ledger_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    ledger = ledger_dir / f"{run_id}-{TARGET}.json"
    if ledger.exists():
        previous = read_json(ledger)
        save(output / "receipt.json", previous)
        raise RuntimeError(f"Submission already attempted: {previous['job_id']}; inspect it before a new run")
    with stage("testflinger.preflight"):
        restriction = api.get_json("/v1/restricted-queues/" + config["queue"])
        require(restriction and restriction.get("owners") == [config["submitter"]], "Queue must be restricted to the dedicated submitter")
        agent = api.get_json("/v1/agents/data/" + config["agent"])
        require(agent and agent.get("state") == "waiting" and not agent.get("job"), "Expected idle registered agent")
        require(agent.get("queues") == [config["queue"]], "Unexpected agent queue configuration")
        jobs = api.get_json("/v1/queues/" + config["queue"] + "/jobs") or []
        require(not any(job.get("job_state") not in ("complete", "cancelled") for job in jobs), "Queue has outstanding work")
    job_id = str(uuid.uuid4())
    receipt = {"job_id": job_id, "run_id": run_id, "target": TARGET,
               "manifest_sha256": digest(manifest_bytes), "stage": "submission_intent", "status": "pending"}
    with ledger.open("x") as stream:
        os.chmod(ledger, 0o600)
        json.dump(receipt, stream)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(ledger_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    save(output / "receipt.json", receipt)
    print(f"Recorded Testflinger job {job_id} before submission", flush=True)
    try:
        job = {"job_id": job_id, "job_queue": config["queue"], "test_data": {
            "test_cmds": f"HILAAS_C6 {config['device_mac']} {manifest['nonce']} {digest(manifest_bytes)}",
            "attachments": [{"agent": name} for name in captured]}}
        with stage("testflinger.submit"):
            response = api.request("POST", "/v1/job", json=job).json()
            require(response.get("job_id") == job_id, "Server changed the requested job ID")
        receipt["stage"] = "attachments"
        save(output / "receipt.json", receipt)
        with stage("testflinger.attachments"):
            api.request("POST", f"/v1/job/{job_id}/attachments",
                        files={"file": ("attachments.tar.gz", attachment_archive(captured), "application/gzip")})
        receipt["stage"] = "waiting"
        with stage("testflinger.wait"):
            deadline = clock() + timeout
            while True:
                results = api.get_json(f"/v1/result/{job_id}")
                save(output / "phase-results.json", results)
                if results.get("job_state") in ("complete", "cancelled"):
                    break
                if clock() >= deadline:
                    raise TimeoutError("Job still pending; no automatic resubmission or cancellation")
                wait(5)
        receipt["stage"] = "artifacts"
        archive_path = output / "artifacts.tar.gz"
        with stage("testflinger.evidence"):
            with api.request("GET", f"/v1/result/{job_id}/artifact", stream=True) as response:
                size = 0
                with archive_path.open("wb") as stream:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        require(size <= 10 * 2**20, "Evidence exceeds 10 MiB")
                        stream.write(chunk)
            proof = verify_evidence(archive_path, results, manifest_bytes, config)
        receipt.update(status="pass", stage="complete", proof=proof)
        return receipt
    except Exception as error:
        receipt.update(status="fail", error=type(error).__name__)
        raise
    finally:
        save(output / "receipt.json", receipt)
