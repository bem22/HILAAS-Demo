import io
import json
import os
import shutil
import tarfile
import unittest
from unittest.mock import Mock, patch

from hilaas.bundle import FLASH_FILES, TARGET, digest
from hilaas import progress
from hilaas.client import execute, firmware_tests, serial_proof, validate_config, verify_evidence
from hilaas.showcase import report
from test_bundle import BundleFixture


class ClientTests(BundleFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = {"server": "http://testflinger.local", "allow_trusted_http": True,
                       "queue": "hilaas-c6", "agent": "pi1-c6", "submitter": "hilaas-demo",
                       "device_mac": "01:23:45:67:89:ab", "partition_sha256": digest(b"partition")}
        self.api = Mock()
        self.api.get_json.side_effect = [{"owners": ["hilaas-demo"]},
                                        {"state": "waiting", "queues": ["hilaas-c6"], "job": {}}, []]
        self.ledger = self.directory / "ledger"
        self.output = self.directory / "output"

    def run_job(self):
        return execute(self.directory, self.config, self.api, "run-1", "b" * 40, self.ledger, self.output)

    def test_ambiguous_submission_blocks_repeat(self):
        self.api.request.side_effect = TimeoutError("simulated")
        with self.assertRaises(TimeoutError):
            self.run_job()
        self.assertEqual(self.api.request.call_count, 1)
        receipt = json.loads((self.output / "receipt.json").read_text())
        self.assertEqual(receipt["stage"], "submission_intent")
        with self.assertRaisesRegex(RuntimeError, "already attempted"):
            self.run_job()
        self.assertEqual(self.api.request.call_count, 1)

    def test_wrong_partition_never_submits(self):
        self.config["partition_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "Partition"):
            self.run_job()
        self.api.request.assert_not_called()

    def test_http_requires_opt_in(self):
        del self.config["allow_trusted_http"]
        with self.assertRaisesRegex(ValueError, "HTTP"):
            validate_config(self.config)

    def test_unrestricted_queue_never_submits(self):
        self.api.get_json.side_effect = [{"owners": []}]
        with self.assertRaisesRegex(ValueError, "restricted"):
            self.run_job()
        self.api.request.assert_not_called()

    def test_changed_job_id_stops_upload(self):
        self.api.request.return_value.json.return_value = {"job_id": "different"}
        with self.assertRaisesRegex(ValueError, "changed"):
            self.run_job()
        self.assertEqual(self.api.request.call_count, 1)

    def test_serial_proof(self):
        lines = "\n".join(f"HILAAS_C6_V1 mac={self.config['device_mac']} nonce={self.nonce} seq={sequence}" for sequence in (7, 8, 9))
        self.assertEqual(serial_proof(lines, self.nonce, self.config["device_mac"]), [7, 8, 9])
        with self.assertRaises(ValueError):
            serial_proof(lines, "c" * 32, self.config["device_mac"])

    def test_firmware_test_requires_fresh_passing_values(self):
        line = f"HILAAS_TEST_V1 nonce={self.nonce} name=crc32 status=pass actual=cbf43926 expected=cbf43926"
        self.assertEqual(firmware_tests(line, self.nonce, "crc32")["crc32"]["status"], "pass")
        for text, nonce, suite in ((line, "c" * 32, "crc32"), (line, self.nonce, "uint32-wrap"),
                                   (line.replace("status=pass", "status=fail"), self.nonce, "crc32"),
                                   (line.replace("cbf43926", "00000000"), self.nonce, "crc32")):
            with self.subTest(text=text, nonce=nonce, suite=suite), self.assertRaises(ValueError):
                firmware_tests(text, nonce, suite)

    def test_suite_is_bound_to_job_confirmation(self):
        self.config["suite"] = "crc32"
        self.api.request.side_effect = TimeoutError("simulated")
        with self.assertRaises(TimeoutError):
            self.run_job()
        payload = self.api.request.call_args.kwargs["json"]
        self.assertEqual(payload["tags"], ["crc32"])
        self.assertTrue(payload["test_data"]["test_cmds"].endswith(" crc32"))

    def test_demo_submits_two_suites_and_writes_report(self):
        from hilaas.demo import run_suites
        self.api.get_json.side_effect = None
        self.api.get_json.return_value = {"state": "waiting", "job": {}}
        with patch("hilaas.demo.execute", return_value={"job_id": "test-job", "proof": {}}) as submit:
            summary = run_suites(self.directory, self.config, self.api, "demo", "b" * 40,
                                 self.ledger, self.output)
        self.assertEqual(summary["status"], "pass")
        self.assertEqual([call.args[1]["suite"] for call in submit.call_args_list], ["crc32", "uint32-wrap"])
        self.assertEqual([call.args[3] for call in submit.call_args_list], ["demo-crc32", "demo-uint32-wrap"])
        self.assertTrue((self.output / "junit.xml").is_file())
        with self.assertRaisesRegex(ValueError, "already attempted"):
            run_suites(self.directory, self.config, self.api, "demo", "b" * 40, self.ledger, self.output)

    def test_demo_stops_on_ambiguous_submission(self):
        from hilaas.demo import run_suites
        with patch("hilaas.demo.execute", side_effect=TimeoutError("ambiguous")) as submit:
            with self.assertRaises(TimeoutError):
                run_suites(self.directory, self.config, self.api, "demo", "b" * 40, self.ledger, self.output)
        self.assertEqual(submit.call_count, 1)
        summary = json.loads((self.output / "summary.json").read_text())
        self.assertEqual([result["status"] for result in summary["tests"]], ["fail", "not-run"])

    def evidence(self):
        manifest_bytes = (self.directory / "manifest.json").read_bytes()
        proof = {"target": TARGET, "nonce": self.nonce, "device_mac": self.config["device_mac"],
                 "manifest_sha256": digest(manifest_bytes), "flash_count": 1, "sequences": [0, 1, 2]}
        files = {"manifest.json": manifest_bytes, "proof.json": json.dumps(proof).encode(),
                 "serial.log": "\n".join(f"HILAAS_C6_V1 mac={self.config['device_mac']} nonce={self.nonce} seq={sequence}" for sequence in range(3)).encode(),
                 "junit.xml": b"<testsuite/>", "flash.log": b"flash and verification completed"}
        archive_path = self.directory / "evidence.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo("artifacts/" + name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return archive_path, manifest_bytes, proof

    def test_evidence(self):
        archive, manifest, proof = self.evidence()
        results = {"job_state": "complete", "setup_status": 0, "test_status": 0, "cleanup_status": 0}
        self.assertEqual(verify_evidence(archive, results, manifest, self.config), proof)
        results["cleanup_status"] = 1
        with self.assertRaisesRegex(ValueError, "cleanup"):
            verify_evidence(archive, results, manifest, self.config)

    def test_successful_job(self):
        archive, _, _ = self.evidence()
        self.api.get_json.side_effect = [{"owners": ["hilaas-demo"]},
                                        {"state": "waiting", "queues": ["hilaas-c6"], "job": {}}, [],
                                        {"job_state": "complete", "setup_status": 0, "test_status": 0, "cleanup_status": 0}]
        def request(method, endpoint, **kwargs):
            response = Mock()
            if endpoint == "/v1/job":
                response.json.return_value = {"job_id": kwargs["json"]["job_id"]}
            if method == "GET":
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.iter_content.return_value = [archive.read_bytes()]
            return response
        self.api.request.side_effect = request
        with patch.dict(os.environ, HILAAS_PROGRESS_DIR=str(self.directory / "progress"), HILAAS_RUN_ID="run-1", HILAAS_TARGET=TARGET):
            self.assertEqual(self.run_job()["status"], "pass")
        self.assertEqual([call.args[0] for call in self.api.request.call_args_list], ["POST", "POST", "GET"])
        recorded = progress.events(self.directory / "progress", "run-1", TARGET)
        self.assertEqual([event["stage"] for event in recorded if event["status"] == "passed"], [
            "testflinger.preflight", "testflinger.submit", "testflinger.attachments", "testflinger.wait", "testflinger.evidence"])
        root = self.directory / "report"
        bundle = root / "targets" / TARGET / "bundle"
        bundle.mkdir(parents=True)
        for name in (*FLASH_FILES, "manifest.json"):
            shutil.copyfile(self.directory / name, bundle / name)
        hardware = bundle.parent / "hardware"
        shutil.copytree(self.output, hardware)
        config_path = self.directory / "client.json"
        config_path.write_text(json.dumps(self.config))
        text, passed = report(root, "run-1", "b" * 40, "success", TARGET, "hardware", config_path)
        self.assertTrue(passed)
        self.assertIn("Verified flash count", text)
        self.assertIn("three increasing", text)
        self.assertNotIn("Native self-tests", text)
        phases = json.loads((hardware / "phase-results.json").read_text())
        phases["cleanup_status"] = 1
        (hardware / "phase-results.json").write_text(json.dumps(phases))
        text, passed = report(root, "run-1", "b" * 40, "success", TARGET, "hardware", config_path)
        self.assertFalse(passed)

    def test_generic_report_distinguishes_build_only_from_hardware(self):
        output = self.directory / "report"
        bundle = output / "targets" / TARGET / "bundle"
        bundle.mkdir(parents=True)
        for source in self.directory.iterdir():
            if source.is_file():
                shutil.copyfile(source, bundle / source.name)
        text, passed = report(output, "run-1", "b" * 40, "success", TARGET, "build")
        self.assertTrue(passed)
        self.assertIn("hardware execution NOT RUN", text)
        text, passed = report(output, "run-1", "b" * 40, "success", TARGET, "hardware")
        self.assertFalse(passed)
        text, passed = report(output, "run-1", "b" * 40, "success", "microbit", "build")
        self.assertFalse(passed)
