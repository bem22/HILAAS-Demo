import json
import importlib.util
import os
import runpy
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def hardware_harness(self):
        if any(importlib.util.find_spec(module) is None for module in ("serial", "pytest")):
            self.skipTest("pytest and pyserial are required for mocked USB adapter tests")
        return runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent/test_c6.py"))

    def test_missing_usb_port_blocks_device_access(self):
        harness = self.hardware_harness()
        self.check()
        config = dict(self.config, serial_port=str(self.directory / "disconnected-port"))
        config_path = self.directory / "device.json"
        config_path.write_text(json.dumps(config))
        with patch.dict(os.environ, {"HILAAS_DEVICE_CONFIG": str(config_path),
                                    "HILAAS_BUNDLE": str(self.directory), "HILAAS_JOB": str(self.job_path)}), \
                patch("subprocess.run") as tool, patch("serial.Serial") as port:
            with self.assertRaises(FileNotFoundError):
                next(harness["assigned_device"].__wrapped__())
            tool.assert_not_called()
            port.assert_not_called()

    def test_wrong_physical_mcu_never_flashes(self):
        harness = self.hardware_harness()
        output = self.directory / "evidence"
        output.mkdir()
        assigned = (self.config, self.manifest, b"", "/dev/hilaas-demo-not-a-device", self.directory, output)
        response = subprocess.CompletedProcess([], 0, stdout=b"MAC: 02:23:45:67:89:ab\n")
        with patch("subprocess.run", return_value=response) as tool, patch("serial.Serial") as port:
            with self.assertRaisesRegex(ValueError, "Physical device identity mismatch"):
                harness["test_flash_and_serial_proof"](assigned)
            tool.assert_called_once()
            self.assertIn("read_mac", tool.call_args.args[0])
            port.assert_not_called()
        self.assertFalse((output / "proof.json").exists())

    def test_usb_failure_during_flash_is_not_retried(self):
        harness = self.hardware_harness()
        output = self.directory / "evidence"
        output.mkdir()
        assigned = (self.config, self.manifest, b"", "/dev/hilaas-demo-not-a-device", self.directory, output)
        responses = [subprocess.CompletedProcess([], 0, stdout=b"MAC: 01:23:45:67:89:ab\n"),
                     subprocess.CompletedProcess([], 1, stdout=b"Simulated USB disconnect during write\n")]
        with patch("subprocess.run", side_effect=responses) as tool, patch("serial.Serial") as port:
            with self.assertRaisesRegex(ValueError, "esptool failed"):
                harness["test_flash_and_serial_proof"](assigned)
            self.assertEqual(tool.call_count, 2)
            self.assertIn("write_flash", tool.call_args_list[1].args[0])
            port.assert_not_called()
        self.assertIn("Simulated USB disconnect", (output / "flash.log").read_text())
        self.assertFalse((output / "proof.json").exists())
