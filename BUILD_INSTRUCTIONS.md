# Urban Reign Android — FIXED-v4 (boot ELF қосылған)

## Не өзгерді

Осы zip ішінде **тексерілген** `SLUS_212.09` бар:

- SHA-256: `60399090fc69ffdb4c39afd30977ab50890b0e33ed6bd9374e4d9497f4246081`
- USA Urban Reign boot ELF (PS2 MIPS)
- Workflow оны **автоматты** қолданады (`GITHUB_WORKSPACE/SLUS_212.09`)

`boot_elf_url` енді **міндетті емес**.

## ⚠️ Маңызды

- Репоны **тек private** ұстаңыз. `SLUS_212.09` — ойынның бөлігі; public repo = copyright бұзу.
- ISO zip ішінде жоқ. Телефонда өз USA ISO-ңызды таңдайсыз.

## GitHub қадамдары

1. Жаңа **private** repository ашыңыз.
2. Zip ішіндегі `Android-port-qoy-Urban-Reign-main/` мазмұнын root-қа салыңыз:
   - `SLUS_212.09` (root-та болуы керек)
   - `.github/workflows/`
   - `urban-reign-android/`
   - қалған файлдар
3. Push жасаңыз.
4. **Actions → Urban Reign Android APK → Run workflow**
   - Input-тарды бос қалдыруға болады (ELF root-тан алынады)
5. 1–6 сағат күтіңіз. Artifacts ішінен APK жүктеңіз.

## Телефонда

1. APK орнатыңыз.
2. **Uncompressed USA ISO** (`SLUS-21209`) ішкі жадыға қойыңыз.
3. Choose ISO → Start game.
4. Қара экран болса:
   ```bash
   adb logcat -s UrbanReignDiag:* *:E
   adb shell run-as kz.nurasql77.urbanreign cat files/urban_native.log
   ```

## Ескерту

- Build сәтті болса да, нақты телефонда бірінші кадр әлі **ресми расталмаған**.
- CPU software GS; FFmpeg/cutscene өшірулі.
- Extracted папка қолдау көрсетілмейді — тек `.iso` файлы.
