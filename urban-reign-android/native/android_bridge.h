#pragma once

#include <filesystem>

namespace urban::android
{
    // Waits until MainActivity reports a writable app-specific files directory.
    // Gives up after a few seconds (returns current_path()) instead of blocking the
    // NativeActivity thread forever, which would freeze the whole Android UI.
    std::filesystem::path appStorageDirectory();

    // Waits for MainActivity to pass the selected ISO (/proc/self/fd/N path).
    // Returns an empty path if Java never answers (so main() can fail cleanly
    // instead of leaving the Android UI thread blocked => black screen + ANR).
    std::filesystem::path waitForIsoSelection();

    // Writes one stage line to urban_native.log (+ logcat). Safe to call from any thread.
    void logStage(const char *message);
}
