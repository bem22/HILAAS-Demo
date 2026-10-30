"""Explicitly invoked by the Pi agent, not by the repository's unit-test suite."""

import fcntl
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import serial

from hilaas.bundle import FLASH_FILES, TARGET, digest, read_json, require
from hilaas.client import firmware_tests, serial_proof
from hilaas.device import preflight


@pytest.fixture
def assigned_device():
    config = read_json(Path(os.environ["HILAAS_DEVICE_CONFIG"]))
    directory = Path(os.environ["HILAAS_BUNDLE"])
    manifest, captured, manifest_bytes = preflight(directory, Path(os.environ["HILAAS_JOB"]), config)
    tags = read_json(Path(os.environ["HILAAS_JOB"])).get("tags", [])
    if tags:
        config = dict(config, suite=tags[0])
    port = Path(config["serial_port"]).resolve(strict=True)
    device_stat = port.stat()
    require(stat.S_ISCHR(device_stat.st_mode), "Configured port is not a character device")
    lock_dir = Path.home() / ".local/state/hilaas-device-locks"
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = lock_dir / f"{os.major(device_stat.st_rdev)}-{os.minor(device_stat.st_rdev)}.lock"
    output = Path(os.environ["HILAAS_EVIDENCE"])
    output.mkdir(parents=True, exist_ok=True)
    (output / "proof.json").unlink(missing_ok=True)
    (output / "manifest.json").write_bytes(manifest_bytes)
    with lock_path.open("a") as lock, tempfile.TemporaryDirectory(prefix="hilaas-c6-") as temporary:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        private = Path(temporary)
        for filename, data in captured.items():
            (private / filename).write_bytes(data)
        yield config, manifest, manifest_bytes, str(port), private, output


def test_flash_and_serial_proof(assigned_device):
    config, manifest, manifest_bytes, port, private, output = assigned_device
    base = [sys.executable, "-m", "esptool", "--chip", "esp32c6", "--port", port,
            "--baud", "115200", "--before", "usb_reset", "--after", "hard_reset", "--no-stub"]

    def tool(arguments):
        result = subprocess.run(base + arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=180, check=False)
        with (output / "flash.log").open("ab") as stream:
            stream.write(result.stdout)
        require(result.returncode == 0, "esptool failed; inspect flash.log")
        return result.stdout.decode(errors="replace")

    identity = tool(["read_mac"])
    matches = re.findall(r"^(?:BASE )?MAC:\s*((?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2})\s*$",
                         identity, re.MULTILINE)
    require(matches and {value.lower() for value in matches} == {config["device_mac"]}, "Physical device identity mismatch")
    files = []
    for filename, offset in FLASH_FILES.items():
        files.extend([hex(offset), str(private / filename)])
    tool(["write_flash", "--flash_mode", "dio", "--flash_freq", "40m", "--flash_size", "4MB", *files])
    tool(["verify_flash", "--flash_mode", "dio", "--flash_freq", "40m", "--flash_size", "4MB", *files])
    deadline = time.monotonic() + 45
    text = ""
    sequences = None
    with serial.Serial(port, baudrate=115200, timeout=1, exclusive=True) as connection, (output / "serial.log").open("w") as log:
        connection.dtr = False
        connection.rts = False
        while time.monotonic() < deadline:
            line = connection.readline(1024).decode(errors="replace")
            text += line
            require(len(text) <= 2**20, "Serial output limit exceeded")
            log.write(line)
            log.flush()
            try:
                sequences = serial_proof(text, manifest["nonce"], config["device_mac"])
                if config.get("suite"):
                    firmware_tests(text, manifest["nonce"], config["suite"])
                break
            except ValueError:
                continue
    require(sequences is not None, "Timed out waiting for fresh firmware proof")
    proof = {"target": TARGET, "nonce": manifest["nonce"], "device_mac": config["device_mac"],
             "manifest_sha256": digest(manifest_bytes), "flash_count": 1, "sequences": sequences}
    if config.get("suite"):
        proof["tests"] = firmware_tests(text, manifest["nonce"], config["suite"])
    (output / "proof.json").write_text(json.dumps(proof, indent=2) + "\n")
