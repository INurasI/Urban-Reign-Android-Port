#!/usr/bin/env python3
"""Fetch only Urban Reign's boot executable, using HTTP byte ranges when given an ISO URL.

This tool refuses servers that ignore Range and return HTTP 200, so it never intentionally
downloads an entire multi-gigabyte ISO. A locally extracted SLUS_212.09 can also be fetched
from a small direct URL. The result is always checked against the upstream USA SHA-256.
"""
import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlparse, urlunparse
from urllib.request import Request, urlopen

EXPECTED_SHA256 = "60399090fc69ffdb4c39afd30977ab50890b0e33ed6bd9374e4d9497f4246081"
BOOT_NAME = "SLUS_212.09"
SECTOR = 2048
CHUNK = 1024 * 1024
MIN_BOOT = 1024 * 1024
MAX_BOOT = 64 * 1024 * 1024
MAX_ROOT_DIRECTORY = 16 * 1024 * 1024


def normalize_shared_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme != "https":
        raise ValueError("Only HTTPS sources are accepted.")
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query)
    file_id = None
    if host in {"drive.google.com", "www.drive.google.com"}:
        match = re.match(r"^/file/d/([A-Za-z0-9_-]+)", parsed.path)
        if match:
            file_id = match.group(1)
        elif parsed.path.rstrip("/") == "/open":
            file_id = query.get("id", [None])[0]
        elif parsed.path.rstrip("/") == "/uc":
            file_id = query.get("id", [None])[0]
    if file_id:
        return "https://drive.google.com/uc?export=download&confirm=t&id=" + quote(file_id, safe="")
    return url.strip()


def request_range(url: str, start: int, end: int, expected_total: int | None = None):
    if start < 0 or end < start:
        raise ValueError("Invalid byte range.")
    request = Request(
        url,
        headers={
            "Range": f"bytes={start}-{end}",
            "Accept-Encoding": "identity",
            "User-Agent": "UrbanReign-Android-build/1.0",
        },
    )
    # Deliberately never read the response body unless the server confirms HTTP 206.
    # If a server ignores Range, close immediately instead of receiving the full ISO.
    with urlopen(request, timeout=45) as response:
        if response.status != 206:
            raise ValueError(
                "The host did not honor HTTP Range (expected 206 Partial Content). "
                "No full ISO download was attempted."
            )
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
        if not match:
            raise ValueError("The host returned no valid Content-Range header.")
        actual_start, actual_end, total = map(int, match.groups())
        if actual_start != start or actual_end != end:
            raise ValueError("The host returned a different byte range than requested.")
        if total <= end:
            raise ValueError("The remote file length is inconsistent with Content-Range.")
        if expected_total is not None and total != expected_total:
            raise ValueError("The remote file length changed between byte-range requests.")
        media_type = response.headers.get("Content-Type", "").lower()
        if "text/html" in media_type:
            raise ValueError("The URL returned an HTML page, not a downloadable binary.")
        data = response.read(end - start + 2)
        if len(data) != end - start + 1:
            raise ValueError("The host returned a truncated or oversized byte range.")
        return data, total


def remote_slice(url: str, start: int, length: int, total: int) -> bytes:
    if length < 0 or start < 0 or start + length > total:
        raise ValueError("The requested ISO extent is outside the remote file.")
    result = bytearray()
    stop = start + length
    while start < stop:
        end = min(stop, start + CHUNK) - 1
        part, seen_total = request_range(url, start, end, total)
        if seen_total != total:
            raise ValueError("The remote file length changed while reading the ISO.")
        result.extend(part)
        start = end + 1
    return bytes(result)


def verify_boot(data: bytes) -> bytes:
    if not MIN_BOOT <= len(data) <= MAX_BOOT:
        raise ValueError("SLUS_212.09 has an implausible size; expected 1–64 MiB.")
    digest = hashlib.sha256(data).hexdigest()
    if digest != EXPECTED_SHA256:
        raise ValueError(
            f"SLUS_212.09 SHA-256 mismatch ({digest}). Use the USA executable from your own disc."
        )
    print(f"Verified {BOOT_NAME}: {len(data):,} bytes; SHA-256 matches upstream.")
    return data


def fetch_direct_elf(url: str) -> bytes:
    url = normalize_shared_url(url)
    first, total = request_range(url, 0, 0)
    if not MIN_BOOT <= total <= MAX_BOOT:
        raise ValueError(
            "The direct executable URL is not 1–64 MiB. If this is the full ISO, use iso_url; "
            "the tool will inspect it through byte ranges only."
        )
    # Read at most 1 MiB per request, and require a correct 206 response every time.
    data = remote_slice(url, 0, total, total)
    return verify_boot(data)


def find_boot_extent(directory: bytes):
    offset = 0
    while offset < len(directory):
        record_length = directory[offset]
        if record_length == 0:
            offset = ((offset // SECTOR) + 1) * SECTOR
            continue
        if record_length < 34 or offset + record_length > len(directory):
            offset += max(1, record_length)
            continue
        name_len = directory[offset + 32]
        if 33 + name_len <= record_length:
            raw_name = directory[offset + 33:offset + 33 + name_len]
            name = raw_name.decode("ascii", "replace").split(";")[0].upper()
            if name == BOOT_NAME:
                lba = int.from_bytes(directory[offset + 2:offset + 6], "little")
                size = int.from_bytes(directory[offset + 10:offset + 14], "little")
                return lba * SECTOR, size
        offset += record_length
    raise ValueError("SLUS_212.09 was not found in the ISO9660 root directory.")


def fetch_from_iso(url: str) -> bytes:
    url = normalize_shared_url(url)
    pvd_offset = 16 * SECTOR
    pvd, total = request_range(url, pvd_offset, pvd_offset + SECTOR - 1)
    if pvd[0] != 1 or pvd[1:6] != b"CD001":
        raise ValueError("The remote file is not a readable ISO9660 image at sector 16.")
    root_lba = int.from_bytes(pvd[158:162], "little")
    root_size = int.from_bytes(pvd[166:170], "little")
    if root_lba <= 0 or root_size <= 0 or root_size > MAX_ROOT_DIRECTORY:
        raise ValueError("The ISO9660 root directory has an invalid extent or size.")
    root_offset = root_lba * SECTOR
    directory = remote_slice(url, root_offset, root_size, total)
    boot_offset, boot_size = find_boot_extent(directory)
    if not MIN_BOOT <= boot_size <= MAX_BOOT:
        raise ValueError("The SLUS_212.09 extent size is outside the expected 1–64 MiB range.")
    if boot_offset + boot_size > total:
        raise ValueError("The executable extent runs past the end of the ISO.")
    print(
        f"Remote ISO size: {total:,} bytes. Fetching only the executable extent "
        f"({boot_size:,} bytes), not the whole ISO."
    )
    data = remote_slice(url, boot_offset, boot_size, total)
    return verify_boot(data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--elf-url", help="HTTPS URL to a small SLUS_212.09 executable")
    parser.add_argument("--iso-url", help="HTTPS URL to an ISO supporting byte-range requests")
    parser.add_argument("--output", required=True, help="output path for the verified executable")
    args = parser.parse_args()
    if bool(args.elf_url) == bool(args.iso_url):
        parser.error("provide exactly one of --elf-url or --iso-url")
    try:
        data = fetch_direct_elf(args.elf_url) if args.elf_url else fetch_from_iso(args.iso_url)
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(data)
    except (ValueError, URLError, HTTPError, TimeoutError, OSError) as exc:
        # Avoid echoing signed/share URLs into Actions logs.
        print(
            f"Boot executable retrieval failed ({type(exc).__name__}). "
            f"{exc if isinstance(exc, ValueError) else 'The HTTPS host could not serve the requested byte range.'}",
            file=sys.stderr,
        )
        print(
            "Fallback: extract SLUS_212.09 locally with urban-reign-android/tools/extract-boot-elf.html "
            "and provide a small HTTPS download link.",
            file=sys.stderr,
        )
        return 1
    print(f"Wrote verified boot executable to {output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
