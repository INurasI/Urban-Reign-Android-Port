# Urban Reign — Android port build

This repository builds an ARM64 Android APK from the GPL-3.0 source of Urban Reign Recomp v1.3.0. The project is still experimental; compiling an APK does not yet prove the game is playable on a physical phone.

## Avoid uploading the 4 GB ISO to Actions

The code generator needs only the verified boot executable `SLUS_212.09` extracted from the user's own Urban Reign (USA) PS2 disc. The complete ISO is **not** downloaded to GitHub Actions, committed to the repository, or embedded in the APK.

1. Run Actions → **Prepare Urban Reign generated game code** and paste your existing HTTPS ISO share link into `iso_url`. The build fetches the ISO's PVD, root directory and `SLUS_212.09` executable extent using HTTP byte ranges, and refuses hosts that ignore range requests. It does not intentionally download or store the full ISO. A Google Drive share link in the usual `drive.google.com/file/d/…/view` form is converted to a range-download URL.
2. If the host does not support byte ranges, use the local helper [`urban-reign-android/tools/extract-boot-elf.html`](tools/extract-boot-elf.html) to extract `SLUS_212.09` on your device, then provide a small HTTPS executable link in `boot_elf_url` (or configure `URBAN_BOOT_ELF_URL`). The build checks the exact SHA-256 before generating code and uploads only a generated-code archive.
3. APK creation is currently **manual-only and deliberately does not start automatically**. When you're ready to build later, run **Urban Reign Android APK** from the Actions tab and enter the successful preparation run's numeric ID in `generated_code_run_id`. If the artifact has expired, manually repeat the preparation workflow.

The Android APK workflow also accepts `boot_elf_url` directly when `generated_code_run_id` is blank. Neither workflow accepts the complete ISO as a build input. The prepare and probe workflows are also manual-only while the port is being debugged.

## Game disc on Android

The APK does **not** include the game disc. On first launch, choose either your own uncompressed Urban Reign USA ISO or **Extracted game folder** in the launcher's source menu.

For the extracted-folder option, select the folder that directly contains `SYSTEM.CNF` and `SLUS_212.09` (not its parent). For now, place it in internal shared storage at a normal path such as `Download/Games/UrbanReign`; the native reader needs a real filesystem path, so virtual/cloud-only document providers and folders on unsupported storage volumes may not work. The folder is exposed to the runtime as a synthetic ISO9660 view and does not require building an ISO first.

For an ISO, the Activity keeps an open file descriptor and passes its `/proc/self/fd/` path to native code, so it does not make a second DVD-sized copy in app storage. Keep the ISO at the same location and do not remove its storage permission after selecting it. Some cloud providers expose non-seekable streams; if direct opening fails, download the ISO to device storage first and select that local file.

The folder reader and Android picker are experimental. The synthetic ISO now includes the mandatory ISO9660 Type-L and Type-M path tables, which were missing previously. This fixes an invalid-volume-descriptor issue, but still needs testing against the actual extracted game folder on an ARM64 Android device.

The current native runtime does not fully decode PS2 IPU/Sofdec opening videos; a black screen during that video may be expected. Try tapping the on-screen **START** control to skip it and reach the profile menu. On Android, VU/GIF processing defaults to synchronous mode (`PS2X_MTVU=0`) to avoid the worker timeout seen in diagnostics; this may trade some performance for stability. `PS2X_MTVU=1` can be tested later if needed.

## Validate changes without building an APK

To check the current C++/Java changes without packaging an APK, open Actions and run **Urban Reign Android ARM64 host portability probe** manually. It runs the ISO-folder regression checks, compiles the ARM64 runtime/host probe, and compiles the Android Activity/resources; it does **not** run `assembleDebug` or upload an APK. Do not start either APK workflow until this compile-only probe passes and the device-side black-screen issue is understood.

## Current implementation

- `patches/apply_android_port.py` applies Android host, ARM64 intrinsic and touch-input compatibility patches to a temporary upstream checkout.
- `patches/test_folder_disc_support.py` adds static regression checks for ISO9660 path-table ordering and volume-descriptor pointers; run it alongside the manual compile-only probe.
- `patches/generate_from_elf.py` validates the boot ELF and generates the game-specific C++ without requiring the ISO.
- `native/android_bridge.cpp` connects the Android Activity to native ISO selection and virtual touch-gamepad input.
- `android-app/` contains the NativeActivity manifest, Java activity and adjustable touch overlay.
- `.github/workflows/urban-reign-prepare-game-code.yml` generates and archives reusable game code from the small boot ELF.
- `.github/workflows/urban-reign-android-apk.yml` builds the ARM64 APK from that artifact, or generates code from the boot ELF in the same run.
- `.github/workflows/urban-reign-android-probe.yml` verifies native ARM64 and Java compilation without claiming a playable game build.

## Remaining validation

The APK needs a complete native build with the verified generated code and then testing on a real ARM64 Android device. The Android build does not use the Windows PCSX2 GS DLL; MPEG video decoding is disabled in this initial configuration, so cutscene-video support remains unvalidated.
