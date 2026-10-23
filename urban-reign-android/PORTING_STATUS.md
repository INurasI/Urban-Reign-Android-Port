# Status — FIXED-v4

## Boot ELF

Repo root includes verified USA `SLUS_212.09`:

`SHA-256 = 60399090fc69ffdb4c39afd30977ab50890b0e33ed6bd9374e4d9497f4246081`

The APK workflow copies it automatically when no `boot_elf_url` / `generated_code_run_id` is given.

**Keep the repository private.**

## CI expectations

1. `generate_from_elf.py` validates the hash, runs `ps2_recomp`, writes `source/generated/`.
2. Guard rejects tiny/empty `register_functions.cpp`.
3. Native `liburban.so` + Gradle APK for `arm64-v8a`.

## Device

Startup/graphics on real phones still need validation after a successful full APK artifact.
