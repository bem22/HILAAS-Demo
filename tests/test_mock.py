import json
import io
import tarfile
import tempfile
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from hilaas.mock_agent import QUEUE, TOKEN, execute
from hilaas.mock_client import execute as submit_mock, verify_evidence
from hilaas.native import build as build_native, validate as validate_native, verify_output


class MockAgentTests(unittest.TestCase):
    def test_native_process_failure_never_reports_pass(self):
        import hashlib
        for failure in (subprocess.CompletedProcess([], 1), subprocess.TimeoutExpired("demo", 20)):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = root / "attachments/test"
                bundle.mkdir(parents=True)
                binary = bytearray(64)
                binary[:7] = b"\x7fELF\x02\x01\x01"
                binary[18:20] = b"\x3e\x00"
                manifest = {"kind": "native", "architecture": "x86_64", "commit": "a" * 40,
                            "run_id": "run-1", "nonce": "b" * 32, "build_system": "workshop",
                            "binary_sha256": hashlib.sha256(binary).hexdigest()}
                (bundle / "hilaas-selftest").write_bytes(binary)
                (bundle / "native-manifest.json").write_text(json.dumps(manifest))
                payload = self.job()
                payload.update(name="run-1", tags=[manifest["nonce"], manifest["binary_sha256"], manifest["commit"]],
                               test_data={"test_cmds": "HILAAS_NATIVE_V1", "attachments": [
                                   {"agent": "hilaas-selftest"}, {"agent": "native-manifest.json"}]})
                (root / "testflinger.json").write_text(json.dumps(payload))
                with patch("hilaas.mock_agent.subprocess.run") as process:
                    if isinstance(failure, Exception):
                        process.side_effect = failure
                    else:
                        process.return_value = failure
                    with self.assertRaises((ValueError, subprocess.TimeoutExpired)):
                        execute(root / "testflinger.json", root / "artifacts")
                    process.assert_called_once()
                    self.assertEqual(process.call_args.kwargs["timeout"], 20)
                self.assertFalse((root / "artifacts/mock-result.json").exists())

    @unittest.skipUnless(shutil.which("cc") and shutil.which("bwrap"), "Native compiler and Bubblewrap required")
    def test_real_native_binary_runs_in_sandbox(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "attachments/test"
            bundle.mkdir(parents=True)
            source = Path(__file__).resolve().parents[1]
            manifest = {"kind": "native", "architecture": "x86_64", "commit": "a" * 40,
                        "run_id": "run-1", "nonce": "b" * 32, "build_system": "workshop"}
            binary = bundle / "hilaas-selftest"
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            '-DHILAAS_BUILD_COMMIT="' + manifest["commit"] + '"',
                            '-DHILAAS_BUILD_RUN_ID="run-1"',
                            '-DHILAAS_BUILD_NONCE="' + manifest["nonce"] + '"',
                            str(source / "firmware/native/main.c"), str(source / "firmware/common/selftest.c"),
                            "-o", str(binary)], check=True)
            manifest["binary_sha256"] = hashlib.sha256(binary.read_bytes()).hexdigest()
            (bundle / "native-manifest.json").write_text(json.dumps(manifest))
            payload = self.job()
            payload.update(name="run-1", tags=[manifest["nonce"], manifest["binary_sha256"], manifest["commit"]],
                           test_data={"test_cmds": "HILAAS_NATIVE_V1", "attachments": [
                               {"agent": "hilaas-selftest"}, {"agent": "native-manifest.json"}]})
            (root / "testflinger.json").write_text(json.dumps(payload))
            proof = execute(root / "testflinger.json", root / "artifacts")
            self.assertEqual(proof["kind"], "native")
            self.assertEqual(proof["binary_sha256"], manifest["binary_sha256"])
            self.assertEqual(proof["exit_code"], 0)
            verify_output((root / "artifacts/native-output.log").read_text(), manifest)
            evidence = root / "evidence.tar.gz"
            with tarfile.open(evidence, "w:gz") as archive:
                for filename in ("mock-result.json", "native-output.log"):
                    archive.add(root / "artifacts" / filename, arcname="artifacts/" + filename)
            results = {"job_state": "complete", "setup_status": 0, "test_status": 0, "cleanup_status": 0}
            download = MagicMock()
            download.__enter__.return_value = download
            download.iter_content.return_value = [evidence.read_bytes()]
            api = Mock()
            api.get_json.side_effect = [
                {"owners": ["hilaas-laptop-mock-agent", "hilaas-laptop-mock-submitter"]},
                {"state": "waiting", "queues": [QUEUE]}, [], results]
            api.request.side_effect = [Mock(json=Mock(return_value={"job_id": payload["job_id"]})), Mock(), download]
            with patch("hilaas.mock_client.uuid.uuid4", return_value=payload["job_id"]):
                receipt = submit_mock(api, "run-1", root / "output", root / "ledger", bundle=bundle)
            self.assertEqual(receipt["status"], "pass")
            self.assertEqual(receipt["binary_sha256"], manifest["binary_sha256"])
            self.assertEqual(api.request.call_args_list[0].kwargs["json"]["tags"], payload["tags"])
            uploaded = api.request.call_args_list[1].kwargs["files"]["file"][1]
            with tarfile.open(fileobj=io.BytesIO(uploaded), mode="r:gz") as archive:
                self.assertEqual(archive.extractfile("test/hilaas-selftest").read(), binary.read_bytes())
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                verify_evidence(evidence, results, dict(receipt, commit="c" * 40))

    def test_missing_bundle_never_submits(self):
        with tempfile.TemporaryDirectory() as directory:
            api = Mock()
            root = Path(directory)
            with self.assertRaises((ValueError, FileNotFoundError)):
                submit_mock(api, "run-1", root / "output", root / "ledger", bundle=root / "missing")
            api.request.assert_not_called()
            api.get_json.assert_not_called()

    def test_failed_workshop_build_produces_no_bundle(self):
        import subprocess
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("hilaas.native.subprocess.run", side_effect=[
                    Mock(), subprocess.CalledProcessError(1, "workshop build"), Mock()]):
                with self.assertRaises(subprocess.CalledProcessError):
                    build_native(Path(__file__).resolve().parents[1], root / "output", root / "project", "a" * 40, "run-1")
            self.assertFalse((root / "output/bundle").exists())

    def test_native_output_must_match_build_and_pass(self):
        manifest = {"commit": "a" * 40, "run_id": "run-1", "nonce": "b" * 32}
        identity = f"HILAAS_BUILD_V1 commit={manifest['commit']} run_id=run-1 nonce={manifest['nonce']}"
        output = identity + "\nHILAAS_SELFTEST_V1 status=pass checks=5 failures=0\n"
        verify_output(output, manifest)
        for changed in (output.replace("run-1", "run-2"), output.replace("status=pass", "status=fail"), ""):
            with self.assertRaises(ValueError):
                verify_output(changed, manifest)

    def test_native_bundle_rejects_corrupt_binary(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = bytearray(64)
            binary[:7] = b"\x7fELF\x02\x01\x01"
            binary[18:20] = b"\x3e\x00"
            manifest = {"kind": "native", "architecture": "x86_64", "commit": "a" * 40,
                        "run_id": "run-1", "nonce": "b" * 32, "build_system": "workshop",
                        "binary_sha256": hashlib.sha256(binary).hexdigest()}
            (root / "native-manifest.json").write_text(json.dumps(manifest))
            (root / "hilaas-selftest").write_bytes(binary)
            self.assertEqual(validate_native(root)[0], manifest)
            (root / "hilaas-selftest").write_bytes(bytes(binary) + b"changed")
            with self.assertRaisesRegex(ValueError, "digest mismatch"):
                validate_native(root)

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