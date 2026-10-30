import json
import tempfile
import unittest
from pathlib import Path

from hilaas.bundle import FLASH_FILES, MARKER, SDK_VERSION, TARGET, digest, package, validate


class BundleFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.nonce = "a" * 32
        image = bytearray(24)
        image[0] = 0xE9
        image[12] = 13
        self.image = bytes(image) + MARKER.encode() + self.nonce.encode()
        self.manifest = {"schema": 1, "target": TARGET, "sdk": SDK_VERSION, "commit": "b" * 40, "nonce": self.nonce, "files": {}}
        for filename, offset in FLASH_FILES.items():
            data = b"partition" if filename == "partition-table.bin" else self.image
            (self.directory / filename).write_bytes(data)
            self.manifest["files"][filename] = {"offset": offset, "size": len(data), "sha256": digest(data)}
        self.save()

    def save(self):
        (self.directory / "manifest.json").write_text(json.dumps(self.manifest))


class BundleTests(BundleFixture, unittest.TestCase):
    def test_valid_bundle(self):
        manifest, captured = validate(self.directory)
        self.assertEqual(manifest, self.manifest)
        self.assertEqual(set(captured), set(FLASH_FILES))

    def test_corruption(self):
        (self.directory / "hilaas_c6.bin").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "Integrity"):
            validate(self.directory)

    def test_unknown_board(self):
        self.manifest["target"] = "nucleo-unspecified"
        self.save()
        with self.assertRaisesRegex(ValueError, "target"):
            validate(self.directory)

    def test_symlink(self):
        path = self.directory / "hilaas_c6.bin"
        path.unlink()
        path.symlink_to(self.directory / "bootloader.bin")
        with self.assertRaisesRegex(ValueError, "regular file"):
            validate(self.directory)

    def test_wrong_chip_even_with_matching_hash(self):
        data = bytearray(self.image)
        data[12] = 9
        (self.directory / "hilaas_c6.bin").write_bytes(data)
        self.manifest["files"]["hilaas_c6.bin"]["sha256"] = digest(data)
        self.save()
        with self.assertRaisesRegex(ValueError, "ESP32-C6"):
            validate(self.directory)

    def test_wrong_nonce(self):
        self.manifest["nonce"] = "c" * 32
        self.save()
        with self.assertRaisesRegex(ValueError, "proof"):
            validate(self.directory)

    def test_path_traversal_inventory(self):
        self.manifest["files"]["../extra"] = {}
        self.save()
        with self.assertRaisesRegex(ValueError, "inventory"):
            validate(self.directory)

    def test_duplicate_json_keys(self):
        (self.directory / "manifest.json").write_text('{"schema":1,"schema":1}')
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate(self.directory)

    def test_package(self):
        build = self.directory / "idf"
        (build / "bootloader").mkdir(parents=True)
        (build / "partition_table").mkdir()
        mapping = {"0x0": "bootloader/bootloader.bin", "0x8000": "partition_table/partition-table.bin", "0x10000": "hilaas_c6.bin"}
        (build / "flasher_args.json").write_text(json.dumps({"flash_files": mapping, "extra_esptool_args": {"chip": "esp32c6"}}))
        for relative in mapping.values():
            (build / relative).write_bytes((self.directory / Path(relative).name).read_bytes())
        destination = self.directory / "bundle"
        package(build, destination, "b" * 40, self.nonce, SDK_VERSION)
        self.assertEqual(validate(destination)[0], self.manifest)
