"""The immutable boundary between firmware builds and hardware execution."""

import hashlib
import json
import re
import shutil
from pathlib import Path


TARGET = "esp32c6-devkitc-1"
SDK_VERSION = "ESP-IDF v5.5.3"
MARKER = "HILAAS_C6_V1"
FLASH_FILES = {
    "bootloader.bin": 0x0,
    "partition-table.bin": 0x8000,
    "hilaas_c6.bin": 0x10000,
}
MAX_FILE_SIZE = 4 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    require(not path.is_symlink() and path.is_file(), "JSON must be a regular file")
    require(path.stat().st_size <= 65536, "JSON is too large")
    return json.loads(path.read_bytes(), object_pairs_hook=unique_keys)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def validate(directory):
    directory = Path(directory)
    require(not directory.is_symlink(), "Bundle cannot be a symlink")
    manifest = read_json(directory / "manifest.json")
    require(manifest.get("schema") == 1, "Unsupported manifest schema")
    require(manifest.get("target") == TARGET, "Unsupported or unconfigured target")
    require(manifest.get("sdk") == SDK_VERSION, "Unexpected SDK version")
    require(re.fullmatch(r"[0-9a-f]{40}", manifest.get("commit", "")), "Invalid source commit")
    require(re.fullmatch(r"[0-9a-f]{32}", manifest.get("nonce", "")), "Invalid nonce")
    entries = manifest.get("files")
    require(isinstance(entries, dict) and set(entries) == set(FLASH_FILES), "Unexpected firmware inventory")
    captured = {}
    for filename, offset in FLASH_FILES.items():
        path = directory / filename
        require(not path.is_symlink() and path.is_file(), f"Not a regular file: {filename}")
        size = path.stat().st_size
        require(0 < size <= MAX_FILE_SIZE, f"Invalid size: {filename}")
        data = path.read_bytes()
        entry = entries[filename]
        require(entry == {"offset": offset, "size": len(data), "sha256": digest(data)}, f"Integrity mismatch: {filename}")
        if filename != "partition-table.bin":
            require(len(data) >= 24 and data[0] == 0xE9, "Invalid ESP image header")
            require(int.from_bytes(data[12:14], "little") == 13, "Image is not ESP32-C6")
        captured[filename] = data
    require(len(captured["bootloader.bin"]) <= 0x8000, "Bootloader overlaps partition table")
    require(len(captured["partition-table.bin"]) <= 0x1000, "Partition table exceeds allocation")
    require(len(captured["hilaas_c6.bin"]) <= 0x100000, "Application exceeds factory partition")
    application = captured["hilaas_c6.bin"]
    require(manifest["nonce"].encode() in application and MARKER.encode() in application, "Missing firmware proof")
    return manifest, captured


def package(build, destination, commit, nonce, sdk):
    build, destination = Path(build), Path(destination)
    require(not destination.exists(), "Bundle destination already exists")
    metadata = read_json(build / "flasher_args.json")
    flash_files = metadata.get("flash_files", {})
    expected = {0: "bootloader/bootloader.bin", 0x8000: "partition_table/partition-table.bin", 0x10000: "hilaas_c6.bin"}
    require({int(offset, 0): name for offset, name in flash_files.items()} == expected, "Unexpected build flash layout")
    require(metadata.get("extra_esptool_args", {}).get("chip") == "esp32c6", "Wrong build chip")
    destination.mkdir(parents=True)
    try:
        manifest = {"schema": 1, "target": TARGET, "sdk": sdk, "commit": commit, "nonce": nonce, "files": {}}
        for filename, offset in FLASH_FILES.items():
            source = build / expected[offset]
            require(not source.is_symlink() and source.is_file(), "Invalid build artifact")
            require(source.stat().st_size <= MAX_FILE_SIZE, "Build artifact too large")
            data = source.read_bytes()
            (destination / filename).write_bytes(data)
            manifest["files"][filename] = {"offset": offset, "size": len(data), "sha256": digest(data)}
        (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        validate(destination)
    except BaseException:
        shutil.rmtree(destination)
        raise
