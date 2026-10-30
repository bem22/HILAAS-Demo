import os
import unittest
from unittest.mock import patch

from hilaas.cli import main


class CliTests(unittest.TestCase):
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
