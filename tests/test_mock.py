import json
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from hilaas.mock_agent import QUEUE, TOKEN, execute
from hilaas.mock_client import execute as submit_mock, verify_evidence


class MockAgentTests(unittest.TestCase):
    def test_client_rejects_failed_phase_or_mismatched_evidence(self):
        receipt = {"job_id": "00000000-0000-4000-8000-000000000001", "run_id": "github-123", "nonce": "a" * 32}
        proof = dict(receipt, kind="mock", status="passed", agent=QUEUE, hardware=False, flashed=False)
        results = {"job_state": "complete", "setup_status": 0, "test_status": 0, "cleanup_status": 0}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifacts.tar.gz"
            payload = json.dumps(proof).encode()
            with tarfile.open(path, "w:gz") as archive:
                member = tarfile.TarInfo("artifacts/mock-result.json")
                member.size = len(payload)
                archive.addfile(member, io.BytesIO(payload))
            self.assertEqual(verify_evidence(path, results, receipt), proof)
            with self.assertRaisesRegex(ValueError, "test failed"):
                verify_evidence(path, dict(results, test_status=1), receipt)
            with self.assertRaisesRegex(ValueError, "mismatched"):
                verify_evidence(path, results, dict(receipt, nonce="b" * 32))

    def test_client_does_not_repeat_uncertain_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = Mock()
            api.get_json.side_effect = [
                {"owners": ["hilaas-laptop-mock-agent", "hilaas-laptop-mock-submitter"]},
                {"state": "waiting", "queues": [QUEUE]}, [],
            ]
            api.request.side_effect = TimeoutError("Submission uncertain")
            with self.assertRaises(TimeoutError):
                submit_mock(api, "run-1", root / "output", root / "ledger")
            with self.assertRaisesRegex(ValueError, "already attempted"):
                submit_mock(api, "run-1", root / "output", root / "ledger")
            api.request.assert_called_once()

    def test_client_rejects_unexpected_queue_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            api = Mock()
            api.get_json.return_value = {"owners": ["hilaas-laptop-mock-agent", "hilaas-laptop-mock-submitter", "another-client"]}
            with self.assertRaisesRegex(ValueError, "unexpected owners"):
                submit_mock(api, "run-2", root / "output", root / "ledger")
            api.request.assert_not_called()

    def job(self):
        return {"job_id": "00000000-0000-4000-8000-000000000001", "job_queue": QUEUE,
            "name": "github-123", "tags": ["a" * 32], "test_data": {"test_cmds": TOKEN}}

    def test_returns_correlated_mock_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "testflinger.json"
            job.write_text(json.dumps(self.job()))
            result = execute(job, root / "artifacts")
            self.assertEqual(result, json.loads((root / "artifacts/mock-result.json").read_text()))
            self.assertEqual(result["run_id"], "github-123")
            self.assertEqual(result["nonce"], "a" * 32)
            self.assertFalse(result["hardware"])
            self.assertFalse(result["flashed"])

    def test_rejects_commands_wrong_queue_and_hardware_phases(self):
        for mutation in ("command", "queue", "provision", "nonce"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                payload = self.job()
                if mutation == "command":
                    payload["test_data"]["test_cmds"] = "touch /tmp/untrusted"
                elif mutation == "queue":
                    payload["job_queue"] = "hilaas-esp32c6-pi1"
                elif mutation == "provision":
                    payload["provision_data"] = {"url": "https://example.invalid/image"}
                else:
                    payload["tags"] = ["invalid"]
                job = root / "testflinger.json"
                job.write_text(json.dumps(payload))
                with self.assertRaises(ValueError):
                    execute(job, root / "artifacts")
                self.assertFalse((root / "artifacts").exists())