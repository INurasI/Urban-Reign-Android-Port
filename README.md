# Urban Reign Android Port (FIXED-v4)

ARM64 Android port scaffolding for [Urban Reign Recomp](https://github.com/thiagordsfx/urban-reign-recomp) **v1.3.0**.

**This package includes a verified `SLUS_212.09` boot ELF** (SHA-256 matches upstream USA).  
Keep the repository **private**. Do not publish the ELF.

## Quick build

1. Push this tree to a **private** GitHub repo (ELF must stay at repo root as `SLUS_212.09`).
2. Actions → **Urban Reign Android APK** → Run workflow (no URL inputs required).
3. Download the APK artifact.
4. On device, select your own local USA ISO (not shipped here).

See [BUILD_INSTRUCTIONS.md](BUILD_INSTRUCTIONS.md).

## Workflows

| Workflow | Purpose |
|---|---|
| `urban-reign-android-apk.yml` | Full APK (uses root `SLUS_212.09` if present) |
| `urban-reign-prepare-game-code.yml` | Generate C++ artifact only |
| `urban-reign-android-probe.yml` | Host compile check — **not playable** |

## Graphics / limits

- CPU software GS (no desktop OpenGL 4.6 on Android)
- FFmpeg cutscenes disabled
- Runtime needs seekable local ISO; extracted folders not supported

## FIXED-v4

- Verified boot ELF embedded for private CI (`60399090…46081`)
- Empty/probe `register_functions.cpp` still refused by APK workflow
- Clear private-repo build steps

GPL-3.0 port code. No full ISO. Legal ownership of the disc is your responsibility.
