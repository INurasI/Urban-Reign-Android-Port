#!/usr/bin/env python3
"""Generate Urban Reign recompiled C++ from the verified SLUS_212.09 ELF.

Unlike upstream's generate.py, this path does not need the full PS2 ISO. The
boot executable is checked against upstream's SHA-256 before being passed to
ps2_recomp. This file and the generated output are build-time tools only.
"""
import argparse
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import sys

BOOT_ELF = "SLUS_212.09"
BOOT_SHA256 = "60399090fc69ffdb4c39afd30977ab50890b0e33ed6bd9374e4d9497f4246081"


def sync_tree(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    wanted = {p.name for p in src.iterdir() if p.is_file()}
    changed = removed = 0
    for p in src.iterdir():
        if not p.is_file():
            continue
        target = dst / p.name
        data = p.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            continue
        target.write_bytes(data)
        changed += 1
    for p in dst.iterdir():
        if p.is_file() and p.name not in wanted:
            p.unlink()
            removed += 1
    print(f"generated sources: {len(wanted)} total, {changed} written, {removed} removed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="path to the checked-out Urban Reign Recomp tree")
    parser.add_argument("--elf", required=True, help="path to the extracted SLUS_212.09 boot executable")
    args = parser.parse_args()

    root = Path(args.source).resolve()
    elf_path = Path(args.elf).resolve()
    if not elf_path.is_file():
        raise SystemExit(f"Boot executable not found: {elf_path}")
    data = elf_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != BOOT_SHA256:
        raise SystemExit(f"{BOOT_ELF} hash mismatch ({digest}); expected {BOOT_SHA256}")
    if not (root / "recomp" / "urban.toml").is_file():
        raise SystemExit(f"Not an Urban Reign Recomp source tree: {root}")

    build = root / "build"
    build.mkdir(parents=True, exist_ok=True)
    checked_elf = build / BOOT_ELF
    checked_elf.write_bytes(data)

    staging = build / "generated_staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    toml = (root / "recomp" / "urban.toml").read_text(encoding="utf-8")
    toml = re.sub(
        r"(?m)^input\s*=.*$",
        f'input = "{checked_elf.as_posix()}"',
        toml,
    )
    toml = re.sub(
        r"(?m)^output\s*=.*$",
        f'output = "{staging.as_posix()}"',
        toml,
    )

    def absolute_ghidra(match: re.Match[str]) -> str:
        path = (root / "recomp" / match.group(1)).resolve()
        if path.exists():
            return f'ghidra_output = "{path.as_posix()}"'
        return "# ghidra map missing: " + match.group(1)

    toml = re.sub(r'(?m)^ghidra_output\s*=\s*"([^"]+)"', absolute_ghidra, toml)
    staged_toml = build / "urban.elf.staging.toml"
    staged_toml.write_text(toml, encoding="utf-8")

    recompiler = root / "tools" / "ps2_recomp.exe"
    if not recompiler.is_file():
        raise SystemExit(f"Recompiler not found: {recompiler}; build runtime/ps2xRecomp and place it there first.")
    log_path = build / "recomp.log"
    command = [str(recompiler), str(staged_toml)]
    print("+", " ".join(command), flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(command, cwd=build, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode != 0:
        raise SystemExit(f"ps2_recomp failed ({result.returncode}); see {log_path}")
    sync_tree(staging, root / "generated")
    print(f"Verified boot ELF SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
