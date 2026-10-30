import json
import unittest

from hilaas.bundle import digest
from hilaas.device import preflight
from test_bundle import BundleFixture


class DeviceTests(BundleFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = {"enabled": True, "device_mac": "01:23:45:67:89:ab", "queue": "hilaas-c6", "partition_sha256": digest(b"partition")}
        manifest_hash = digest((self.directory / "manifest.json").read_bytes())
        self.job = {"job_queue": "hilaas-c6", "test_data": {"test_cmds": f"HILAAS_C6 {self.config['device_mac']} {self.nonce} {manifest_hash}"}}
        self.job_path = self.directory / "job.json"

    def check(self):
        self.job_path.write_text(json.dumps(self.job))
        return preflight(self.directory, self.job_path, self.config)

    def test_valid_confirmation(self):
        self.assertEqual(self.check()[0], self.manifest)

    def test_disabled_device(self):
        self.config["enabled"] = False
        with self.assertRaisesRegex(ValueError, "not enabled"):
            self.check()

    def test_arbitrary_command_rejected(self):
        self.job["test_data"]["test_cmds"] = "echo arbitrary"
        with self.assertRaisesRegex(ValueError, "confirmation"):
            self.check()

    def test_other_queue_rejected(self):
        self.job["job_queue"] = "another-device"
        with self.assertRaisesRegex(ValueError, "queue"):
            self.check()

    def test_provision_cannot_flash_again(self):
        self.job["provision_data"] = {"url": "another-image"}
        with self.assertRaisesRegex(ValueError, "owns flashing"):
            self.check()
