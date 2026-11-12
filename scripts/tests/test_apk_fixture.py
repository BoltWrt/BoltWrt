import importlib.util
from pathlib import Path
import struct
import unittest
import zlib

source = Path(__file__).resolve().parents[2] / "tests/runtime/apk_fixture.py"
spec = importlib.util.spec_from_file_location("apk_fixture", source)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def block(kind, body):
    content = struct.pack("<I", kind << 30 | (4 + len(body))) + body
    return content + b"\0" * (-len(content) % 8)


class SignedMetadataFixtureTests(unittest.TestCase):
    def package(self, metadata=None, payload=b"translation contents"):
        metadata = b"\0" * 8 + fixture.PACKAGE if metadata is None else metadata
        return b"ADB.pckg" + block(0, metadata) + block(1, b"unchanged signature") + block(2, payload)

    def test_mutation_changes_only_signed_metadata_and_keeps_other_blocks(self):
        original = self.package()
        changed, proof = fixture.tampered_metadata(original)
        differences = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
        self.assertEqual(differences, [proof["signed_metadata_offset"]])
        self.assertEqual(len(original), len(changed))
        self.assertIn(b"muci-i18n-passwall2-zh-cn", changed)
        self.assertEqual(changed[48:], original[48:])

    def test_deflate_and_uncompressed_inputs_have_the_same_mutation(self):
        original = self.package()
        encoder = zlib.compressobj(wbits=-15)
        compressed = b"ADBd" + encoder.compress(original) + encoder.flush()
        plain, _ = fixture.tampered_metadata(original)
        decoded, _ = fixture.tampered_metadata(compressed)
        self.assertEqual(plain, decoded)

    def test_name_in_data_only_cannot_be_used_as_signed_metadata(self):
        with self.assertRaisesRegex(ValueError, "absent from signed metadata"):
            fixture.tampered_metadata(self.package(metadata=b"other metadata", payload=fixture.PACKAGE))

    def test_truncated_package_cannot_be_used_as_a_signature_control(self):
        with self.assertRaisesRegex(ValueError, "truncated|complete"):
            fixture.tampered_metadata(self.package()[:-5])


if __name__ == "__main__":
    unittest.main()
