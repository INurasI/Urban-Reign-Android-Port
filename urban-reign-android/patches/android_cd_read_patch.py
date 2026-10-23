#!/usr/bin/env python3
"""Android fix: make the sceCdRead stub read through PS2CdImage.

Upstream's Kernel/Stubs/Helpers/Support.h reads raw disc sectors with
std::filesystem::file_size(cdImage) and std::ifstream(cdImage). On Android the
"image" is either "/proc/self/fd/N" (SAF descriptor) or an extracted folder that
PS2CdImage exposes as a synthetic ISO9660 view. Neither can be opened/sized by
path, so every sceCdRead(lbn, ...) after sceCdSearchFile() failed with
"[sceCdRead] unresolved request" and the game retried it forever (black screen).
PS2CdImage already knows how to read both, so route Android reads through it.
"""
from pathlib import Path

MARKER = "URBAN_ANDROID_CD_IMAGE_READ"

SIZE_OLD = """        if (!g_cdImageSizeValid || g_cdImageSizePath != imagePath)
        {
            std::error_code ec;
            g_cdImageSizeBytes ="""

SIZE_NEW = """#if defined(__ANDROID__)
        // URBAN_ANDROID_CD_IMAGE_READ: size comes from PS2CdImage (fd-backed ISO or folder view),
        // because file_size() on /proc/self/fd/N or on a directory fails.
        if (PS2CdImage::instance().ensureOpenFromIoPaths())
        {
            const uint64_t imageBytes = PS2CdImage::instance().sizeBytes();
            if (imageBytes > 0)
            {
                totalSectorsOut = imageBytes / static_cast<uint64_t>(kCdSectorSize);
                return true;
            }
        }
#endif
""" + SIZE_OLD

READ_OLD = """            const uint64_t offset = static_cast<uint64_t>(lbn) * kCdSectorSize;
            return readHostRange(cdImage, offset, dst, byteCount);"""

READ_NEW = """            const uint64_t offset = static_cast<uint64_t>(lbn) * kCdSectorSize;
#if defined(__ANDROID__)
            // URBAN_ANDROID_CD_IMAGE_READ: read through PS2CdImage (dup()'d fd or synthetic folder ISO)
            // instead of re-opening the path with std::ifstream, which fails on Android.
            if (dst && byteCount > 0 && PS2CdImage::instance().ensureOpenFromIoPaths())
            {
                std::memset(dst, 0, byteCount);
                if (PS2CdImage::instance().read(offset, dst, byteCount))
                {
                    g_lastCdError = 0;
                    return true;
                }
                g_lastCdError = -1;
                return false;
            }
#endif
            return readHostRange(cdImage, offset, dst, byteCount);"""


def apply(root: Path) -> str:
    """Returns 'patched' or 'already'. Raises SystemExit if upstream changed."""
    support = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "Kernel" / "Stubs" / "Helpers" / "Support.h"
    if not support.is_file():
        raise SystemExit("Support.h not found; upstream layout changed (Android CD read patch)")
    text = support.read_text(errors="surrogateescape")
    if MARKER in text:
        return "already"
    if text.count(SIZE_OLD) != 1 or text.count(READ_OLD) != 1:
        raise SystemExit("Support.h CD read code changed upstream; review Android CD read patch")
    text = text.replace(SIZE_OLD, SIZE_NEW, 1).replace(READ_OLD, READ_NEW, 1)
    support.write_text(text, errors="surrogateescape")
    return "patched"


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("Usage: android_cd_read_patch.py <upstream-checkout>")
    print("Android CD read patch:", apply(Path(sys.argv[1]).resolve()))
