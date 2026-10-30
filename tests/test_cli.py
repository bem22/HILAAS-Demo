import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from hilaas.cli import ROOT, build_host, main


class CliTests(unittest.TestCase):
    def test_failed_host_build_never_publishes_bundle(self):
        with tempfile.TemporaryDirectory() as temporary, patch("subprocess.run") as process:
            output = Path(temporary) / "output"
            project = Path(temporary) / "project"
            process.side_effect = [None, subprocess.CalledProcessError(1, "build"), None]
            with self.assertRaises(subprocess.CalledProcessError):
                build_host(ROOT, output, project, "a" * 40, "unit-demo")
            self.assertFalse((output / "bundle").exists())
            self.assertEqual(process.call_args.args[0][-1], "stop")

    def test_unknown_targets_cannot_run(self):
        with patch("sys.argv", ["hilaas", "run", "--target", "microbit"]), patch("subprocess.run") as process:
            with self.assertRaisesRegex(ValueError, "unconfigured"):
                main()
            process.assert_not_called()

    def test_hardware_requires_explicit_opt_in(self):
        with patch.dict(os.environ, {"HILAAS_COMMIT": "a" * 40}, clear=True), patch("sys.argv", ["hilaas", "run", "--mode", "hardware"]), patch("subprocess.run") as process:
            with self.assertRaisesRegex(ValueError, "not enabled"):
                main()
            process.assert_not_called()
