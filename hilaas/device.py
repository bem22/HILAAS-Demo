"""Host-controlled ESP32-C6 preflight; never execute submitted test commands."""

import json
import re

from hilaas.bundle import digest, read_json, require, unique_keys, validate


def preflight(directory, job_path, config):
    require(config.get("enabled") is True, "Device is not enabled by the host administrator")
    require(re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", config.get("device_mac", "")), "Host device identity is required")
    require(re.fullmatch(r"[0-9a-f]{64}", config.get("partition_sha256", "")), "Host partition hash is required")
    job = read_json(job_path)
    require(job.get("job_queue") == config.get("queue") and bool(config.get("queue")), "Wrong device queue")
    require(not job.get("provision_data"), "This adapter owns flashing; provision_data must be empty")
    manifest, captured = validate(directory)
    manifest_bytes = (directory / "manifest.json").read_bytes()
    require(json.loads(manifest_bytes, object_pairs_hook=unique_keys) == manifest, "Manifest changed during preflight")
    require(digest(captured["partition-table.bin"]) == config["partition_sha256"], "Partition table is not approved by the host")
    command = f"HILAAS_C6 {config['device_mac']} {manifest['nonce']} {digest(manifest_bytes)}"
    require(job.get("test_data", {}).get("test_cmds") == command, "Invalid hardware confirmation")
    return manifest, captured, manifest_bytes
