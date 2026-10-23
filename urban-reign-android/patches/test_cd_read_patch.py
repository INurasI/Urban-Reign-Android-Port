#!/usr/bin/env python3
"""Static regression test for android_cd_read_patch.py (no compiler needed)."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import android_cd_read_patch as m

FAKE = m.SIZE_OLD + "\n    }\n" + "...\n" + m.READ_OLD + "\n"

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    f = root / "runtime/ps2xRuntime/src/lib/Kernel/Stubs/Helpers/Support.h"
    f.parent.mkdir(parents=True)
    f.write_text(FAKE)
    assert m.apply(root) == "patched"
    out = f.read_text()
    assert out.count(m.MARKER) == 2, "both the size and the read hook must be present"
    assert "PS2CdImage::instance().read(offset, dst, byteCount)" in out
    assert "PS2CdImage::instance().sizeBytes()" in out
    assert m.apply(root) == "already", "patch must be idempotent (workflow runs it twice)"
    assert f.read_text() == out
    f.write_text("// upstream changed\n")
    try:
        m.apply(root)
        raise AssertionError("must fail loudly when upstream code changed")
    except SystemExit:
        pass
print("test_cd_read_patch: OK")
