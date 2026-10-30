import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from hilaas import preliminary


ROOT = Path(__file__).resolve().parents[1]


class PreliminaryTests(unittest.TestCase):
    def test_spread_checkpoint_never_allocates_a_submitter(self):
        definition = yaml.safe_load((ROOT / "spread.yaml").read_text())
        allocation = definition["backends"]["preliminary"]["allocate"]
        self.assertIn("python3 -m hilaas.preliminary", allocation)
        self.assertIn(preliminary.STOP_MESSAGE, allocation)
        self.assertNotIn("hilaas.worker", allocation)
        self.assertNotIn("ADDRESS", allocation)
        task = yaml.safe_load((ROOT / "spread/preliminary/task.yaml").read_text())
        self.assertEqual(task["backends"], ["preliminary"])
        self.assertIn("FATAL", task["execute"])

    def test_build_both_profiles_and_validate_outputs_without_network_api(self):
        for target in preliminary.selected("zephyr-all"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                project, output = root / "workshop", root / "artifacts"
                def process(command, **kwargs):
                    if command[3] == "run":
                        values = dict(item.split("=", 1) for item in command if item.startswith("HILAAS_"))
                        built = project / "build/zephyr/zephyr"
                        built.mkdir(parents=True)
                        header = bytearray(64)
                        header[:7] = b"\x7fELF\x01\x01\x01"
                        header[18:20] = b"\x28\x00"
                        for name in preliminary.FILES:
                            (built / name).write_bytes(bytes(header) + " ".join(values.values()).encode())
                    return Mock(returncode=0)
                with patch.object(preliminary.subprocess, "run", side_effect=process) as run:
                    manifest = preliminary.build(ROOT, output, project, target, "a" * 40, "run-1")
                self.assertEqual([call.args[0][3] for call in run.call_args_list], ["launch", "run", "stop"])
                self.assertEqual(manifest["board"], preliminary.profile(ROOT, target)["board"])
                bundle = output / "targets" / target / "bundle"
                preliminary.validate(bundle, target, manifest["board"], "a" * 40, "run-1")
                (output / "preliminary.json").write_text(json.dumps({"run_id": "run-1", "commit": "a" * 40,
                    "targets": [target], "status": "build_complete", "testflinger_submitted": False, "flashed": False}))
                text, passed = preliminary.report(ROOT, output, target, "a" * 40, "run-1", "failure")
                self.assertTrue(passed)
                self.assertIn("EXPECTED STOP", text)
                (bundle / "zephyr.bin").write_bytes(b"corrupt")
                self.assertFalse(preliminary.report(ROOT, output, target, "a" * 40, "run-1", "failure")[1])

    def test_incomplete_build_is_not_deliberate_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(preliminary.report(ROOT, Path(directory), "zephyr-all", "a" * 40, "run-1", "failure")[1])

    def test_hardware_enable_is_rejected_before_any_command(self):
        with patch.dict(os.environ, HILAAS_COMMIT="a" * 40, HILAAS_RUN_ID="run-1", HILAAS_TARGET="microbit",
                        HILAAS_MODE="preliminary", HILAAS_ENABLE_HARDWARE="yes"), patch("sys.argv", ["preliminary"]):
            with patch.object(preliminary.subprocess, "check_output") as command:
                with self.assertRaisesRegex(ValueError, "disable hardware"):
                    preliminary.main()
                command.assert_not_called()