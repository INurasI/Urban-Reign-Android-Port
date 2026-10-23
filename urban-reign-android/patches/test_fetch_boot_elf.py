#!/usr/bin/env python3
"""Offline tests for strict ranged retrieval of the Urban Reign boot executable."""
import hashlib
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("fetch_boot_elf.py")
SPEC = importlib.util.spec_from_file_location("fetch_boot_elf", SCRIPT)
fetcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetcher)


class FakeResponse:
    def __init__(self, status, headers, data=b""):
        self.status = status
        self.headers = headers
        self.data = data
        self.read_called = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, amount=-1):
        self.read_called = True
        return self.data if amount < 0 else self.data[:amount]


class FetchBootElfTests(unittest.TestCase):
    def test_google_drive_share_url_is_converted_to_direct_range_url(self):
        normalized = fetcher.normalize_shared_url(
            "https://drive.google.com/file/d/abc_123/view?usp=drivesdk"
        )
        self.assertEqual(
            normalized,
            "https://drive.google.com/uc?export=download&confirm=t&id=abc_123",
        )

    def test_server_ignoring_range_is_rejected_without_reading_body(self):
        response = FakeResponse(200, {"Content-Length": "4294967296"})
        with patch.object(fetcher, "urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "did not honor HTTP Range"):
                fetcher.request_range("https://example.test/game.iso", 32768, 34815)
        self.assertFalse(response.read_called)

    def test_iso_extraction_requests_only_small_byte_ranges(self):
        total = 3 * 1024 * 1024
        iso = bytearray(total)
        pvd_offset = 16 * fetcher.SECTOR
        pvd = memoryview(iso)[pvd_offset:pvd_offset + fetcher.SECTOR]
        pvd[0] = 1
        pvd[1:6] = b"CD001"
        root_lba = 20
        root_size = fetcher.SECTOR
        pvd[158:162] = root_lba.to_bytes(4, "little")
        pvd[166:170] = root_size.to_bytes(4, "little")

        boot_lba = 30
        boot_size = fetcher.MIN_BOOT
        boot = bytes((i % 251 for i in range(boot_size)))
        boot_offset = boot_lba * fetcher.SECTOR
        iso[boot_offset:boot_offset + boot_size] = boot

        root_offset = root_lba * fetcher.SECTOR
        filename = b"SLUS_212.09;1"
        record_size = 33 + len(filename)
        record = memoryview(iso)[root_offset:root_offset + record_size]
        record[0] = record_size
        record[2:6] = boot_lba.to_bytes(4, "little")
        record[10:14] = boot_size.to_bytes(4, "little")
        record[32] = len(filename)
        record[33:33 + len(filename)] = filename

        requested = []

        def mock_urlopen(request, timeout=45):
            header = request.get_header("Range")
            self.assertIsNotNone(header)
            start, end = map(int, header.removeprefix("bytes=").split("-"))
            requested.append((start, end))
            payload = bytes(iso[start:end + 1])
            return FakeResponse(
                206,
                {
                    "Content-Range": f"bytes {start}-{end}/{total}",
                    "Content-Type": "application/octet-stream",
                },
                payload,
            )

        digest = hashlib.sha256(boot).hexdigest()
        with patch.object(fetcher, "EXPECTED_SHA256", digest), patch.object(
            fetcher, "urlopen", side_effect=mock_urlopen
        ):
            result = fetcher.fetch_from_iso("https://example.test/game.iso")

        self.assertEqual(result, boot)
        self.assertGreater(len(requested), 0)
        self.assertTrue(all(end - start + 1 <= fetcher.CHUNK for start, end in requested))
        self.assertLess(sum(end - start + 1 for start, end in requested), total)

    def test_full_iso_mistaken_for_direct_executable_is_rejected_after_one_byte_probe(self):
        with patch.object(
            fetcher, "request_range", return_value=(b"x", 4 * 1024 * 1024 * 1024)
        ) as range_mock:
            with self.assertRaisesRegex(ValueError, "not 1–64 MiB"):
                fetcher.fetch_direct_elf("https://example.test/game.iso")
        range_mock.assert_called_once_with(
            "https://example.test/game.iso", 0, 0
        )


if __name__ == "__main__":
    unittest.main()
