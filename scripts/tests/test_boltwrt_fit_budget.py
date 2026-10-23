"""Capacity regressions: valid primary GPT fixtures and sparse internal-data FITs."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

spec = importlib.util.spec_from_file_location('boltwrt_verify', Path(__file__).parents[1] / 'boltwrt-verify.py')
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
MIB = 1024 * 1024
PREFIX = 'boltwrt-4.0.0-mediatek-filogic-cmcc_rax3000m-'


def gpt(path, recovery_last=90111, production_last=393215, recovery_name='recovery'):
    """Independent GPT byte fixture matching ptgen's 32/128MiB deployed layout."""
    data = bytearray(34 * 512)
    data[510:512] = b'\x55\xaa'
    data[450] = 0xee
    for index, (name, first, last) in enumerate(((recovery_name, 24576, recovery_last),
                                                ('production', 131072, production_last))):
        offset = 1024 + index * 128
        data[offset:offset + 16] = bytes([1]) * 16
        data[offset + 16:offset + 32] = bytes([index + 2]) * 16
        struct.pack_into('<QQ', data, offset + 32, first, last)
        encoded = name.encode('utf-16le')
        data[offset + 56:offset + 56 + len(encoded)] = encoded
    struct.pack_into('<8sIIIIQQQQ16sQIII', data, 512, b'EFI PART', 0x10000, 92, 0, 0,
                     1, 393248, 34, 393215, bytes([9]) * 16, 2, 128, 128,
                     zlib.crc32(data[1024:]))
    struct.pack_into('<I', data, 528, zlib.crc32(data[512:604]))
    path.write_bytes(data)


def fit(path, total, padded=None):
    """FDT /images/kernel-1/data with a sparse payload; never executable code."""
    def node(name):
        value = name.encode() + b'\0'
        return struct.pack('>I', 1) + value + bytes((-len(value)) % 4)
    with path.open('wb') as stream:
        stream.write(struct.pack('>10I', 0xd00dfeed, total, 56, total - 8, 40,
                                 17, 16, 0, 8, total - 64))
        stream.write(bytes(16))
        stream.write(node('') + node('images') + node('kernel-1'))
        stream.write(struct.pack('>III', 3, total - 128, 0))
        stream.seek(total - 24)
        stream.write(struct.pack('>4I', 2, 2, 2, 9) + b'data\0\0\0\0')
        stream.truncate(padded or total)


class FitBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.gpt = self.root / (PREFIX + 'emmc-gpt.bin')
        self.production = self.root / (PREFIX + 'squashfs-sysupgrade.itb')
        self.recovery = self.root / (PREFIX + 'initramfs-recovery.itb')
        gpt(self.gpt)
        fit(self.production, 64 * MIB)
        fit(self.recovery, 12 * MIB)

    def test_small_recovery_keeps_deployed_layout_and_reserve(self):
        result = verify.check_rax_fit_budget(self.root)
        self.assertEqual(result['emmc_partitions']['recovery']['bytes'], 32 * MIB)
        self.assertEqual(result['emmc_partitions']['production']['bytes'], 128 * MIB)
        self.assertEqual(result['nand_payload_lower_bound_bytes'], 78 * MIB)
        self.assertIn('bad blocks', result['scope'])

    def test_current_build_sized_recovery_is_rejected_for_both_media(self):
        # Observed actual artifact sizes, not values computed by the checker.
        fit(self.production, 67633420)
        fit(self.recovery, 50659328)
        with self.assertRaises(ValueError) as error:
            verify.check_rax_fit_budget(self.root)
        self.assertIn('eMMC recovery', str(error.exception))
        self.assertIn('NAND production+recovery+two 1MiB env', str(error.exception))
        self.assertIn('120389900', str(error.exception))

    def test_file_padding_cannot_evade_recovery_capacity(self):
        fit(self.recovery, 12 * MIB, padded=32 * MIB + 1)
        with self.assertRaisesRegex(ValueError, 'eMMC recovery'):
            verify.check_rax_fit_budget(self.root)

    def test_two_env_volumes_count_towards_strict_conservative_margin(self):
        fit(self.production, 82 * MIB)
        fit(self.recovery, 20 * MIB)
        with self.assertRaisesRegex(ValueError, 'NAND coexistence.*104 MiB'):
            verify.check_rax_fit_budget(self.root)

    def test_production_uses_actual_gpt_capacity(self):
        gpt(self.gpt, production_last=131072 + 48 * 2048 - 1)
        with self.assertRaisesRegex(ValueError, 'eMMC production'):
            verify.check_rax_fit_budget(self.root)

    def test_gpt_header_corruption(self):
        data = bytearray(self.gpt.read_bytes())
        data[568] ^= 1
        self.gpt.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'header CRC'):
            verify.check_rax_gpt(self.gpt)

    def test_gpt_table_corruption(self):
        data = bytearray(self.gpt.read_bytes())
        data[1024 + 56] ^= 1
        self.gpt.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'table CRC'):
            verify.check_rax_gpt(self.gpt)

    def test_crc_valid_overlap_and_out_of_range_are_still_rejected(self):
        for kwargs, message in (({'recovery_last': 131073}, 'overlapping'),
                                ({'production_last': 393216}, 'outside usable range')):
            with self.subTest(kwargs=kwargs):
                gpt(self.gpt, **kwargs)
                with self.assertRaisesRegex(ValueError, message):
                    verify.check_rax_gpt(self.gpt)

    def test_truncated_table_and_missing_recovery(self):
        self.gpt.write_bytes(self.gpt.read_bytes()[:-1])
        with self.assertRaisesRegex(ValueError, 'table bounds'):
            verify.check_rax_gpt(self.gpt)
        gpt(self.gpt, recovery_name='other')
        with self.assertRaisesRegex(ValueError, 'missing recovery/production'):
            verify.check_rax_gpt(self.gpt)

    def test_truncated_fit_is_rejected_before_capacity_checks(self):
        self.recovery.write_bytes(self.recovery.read_bytes()[:4096])
        with self.assertRaisesRegex(ValueError, 'invalid/truncated FIT size'):
            verify.check_rax_fit_budget(self.root)


if __name__ == '__main__':
    unittest.main(verbosity=2)
