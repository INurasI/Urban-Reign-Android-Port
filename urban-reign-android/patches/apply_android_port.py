#!/usr/bin/env python3
"""Apply Android-specific patches to an ephemeral Urban Reign Recomp checkout."""
from pathlib import Path
import re
import shutil
import sys

if len(sys.argv) != 2:
    raise SystemExit("Usage: apply_android_port.py <upstream-checkout>")

root = Path(sys.argv[1]).resolve()

def fail(message):
    raise SystemExit(message)

# Fix an Android libc++ overload ambiguity found by the ARM64 probe.
p = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_pnach.cpp"
text = p.read_text()
old_time = "std::to_string(time.time_since_epoch().count())"
new_time = "std::to_string(static_cast<long long>(time.time_since_epoch().count()))"
if old_time in text:
    p.write_text(text.replace(old_time, new_time, 1))
elif new_time not in text:
    fail("Timestamp conversion changed upstream; review Android libc++ compatibility")

# All ARM builds route x86 SIMD header includes through sse2neon.
pattern = re.compile(
    r'(#include\s+)<(xmmintrin|emmintrin|smmintrin|pmmintrin|tmmintrin|nmmintrin|wmmintrin|immintrin)\.h>'
)
redirected = 0
for base in (root / "runtime" / "ps2xRuntime", root / "src", root / "generated"):
    for file in base.rglob("*"):
        if not file.is_file() or file.suffix not in {".h", ".hpp", ".cpp", ".c", ".inl"}:
            continue
        original = file.read_text(errors="surrogateescape")
        # This patch is applied both before the Linux x86 recompiler build
        # and after code generation for Android ARM64. Preserve each native
        # x86 intrinsic header on the host; use sse2neon only on Android.
        # Skip a file already rewritten by an earlier run, so its native
        # fallback branch is not wrapped again.
        if '"sse2neon.h"' in original:
            # ps2_memory.h already contains an SSE2NEON include branch upstream.
            # That branch must be Android-only: the host-side x86 recompiler must
            # use the machine's native SSE headers even if a build flag defines
            # USE_SSE2NEON globally.
            native_host = original.replace(
                "#elif defined(USE_SSE2NEON)",
                "#elif defined(USE_SSE2NEON) && defined(__ANDROID__)",
            )
            if native_host != original:
                file.write_text(native_host, errors="surrogateescape")
            continue

        def conditional_intrinsic(match):
            return (
                "#if defined(__ANDROID__)\n"
                '#include "sse2neon.h"\n'
                "#else\n"
                + match.group(1)
                + "<" + match.group(2) + ".h>\n"
                "#endif"
            )

        updated, count = pattern.subn(conditional_intrinsic, original)
        if count:
            file.write_text(updated, errors="surrogateescape")
            redirected += count
if redirected == 0:
    already_patched = any(
        '"sse2neon.h"' in file.read_text(errors="ignore")
        for base in (root / "runtime" / "ps2xRuntime", root / "src")
        for file in base.rglob("*.cpp")
    )
    if not already_patched:
        fail("No x86 intrinsic headers were redirected; upstream source may have changed")

# Android has raylib gamepad APIs but not the desktop GLFW gamepad symbols.
gamepads = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_host_gamepads.cpp"
text = gamepads.read_text()
include = '#include "ps2_host_gamepads.h"'
if '#include "raylib.h"' not in text:
    if include not in text:
        fail("Could not locate ps2_host_gamepads.h include")
    text = text.replace(include, include + '\n#if defined(__ANDROID__)\n#include "raylib.h"\n#endif', 1)
if "#include <mutex>" not in text:
    text = text.replace("#include <iostream>", "#include <iostream>\n#include <mutex>", 1)

touch_globals = '''#if defined(__ANDROID__)
        std::mutex g_touchMutex;
        std::array<unsigned char, 15> g_touchButtons{};
        std::array<float, 6> g_touchAxes{0.0f, 0.0f, 0.0f, 0.0f, -1.0f, -1.0f};
#endif

'''
if "g_touchMutex" not in text:
    anchor = '        extern "C"\n        {'
    if anchor not in text:
        fail("Could not locate GLFW gamepad declaration block")
    text = text.replace(anchor, touch_globals + anchor, 1)

start = text.find('        extern "C"\n        {')
if start >= 0:
    end = text.find("\n        }", start)
    if end < 0:
        fail("Could not locate end of GLFW gamepad declarations")
    end += len("\n        }")
    wrappers = '''        extern "C"
        {
            struct GlfwGamepadState
            {
                unsigned char buttons[15];
                float axes[6];
            };
#if defined(__ANDROID__)
            int glfwJoystickIsGamepad(int jid)
            {
                return IsGamepadAvailable(jid) || jid == 0;
            }

            const char *glfwGetGamepadName(int jid)
            {
                if (IsGamepadAvailable(jid))
                    return GetGamepadName(jid);
                return jid == 0 ? "Android Touch Controller" : nullptr;
            }

            int glfwGetGamepadState(int jid, GlfwGamepadState *state)
            {
                if (!state || jid < 0 || jid >= 16)
                    return 0;
                if (IsGamepadAvailable(jid))
                {
                    constexpr int raylibButtons[15] = {
                        GAMEPAD_BUTTON_RIGHT_FACE_DOWN, GAMEPAD_BUTTON_RIGHT_FACE_RIGHT,
                        GAMEPAD_BUTTON_RIGHT_FACE_LEFT, GAMEPAD_BUTTON_RIGHT_FACE_UP,
                        GAMEPAD_BUTTON_LEFT_TRIGGER_1, GAMEPAD_BUTTON_RIGHT_TRIGGER_1,
                        GAMEPAD_BUTTON_MIDDLE_LEFT, GAMEPAD_BUTTON_MIDDLE,
                        GAMEPAD_BUTTON_MIDDLE_RIGHT, GAMEPAD_BUTTON_LEFT_THUMB,
                        GAMEPAD_BUTTON_RIGHT_THUMB, GAMEPAD_BUTTON_LEFT_FACE_UP,
                        GAMEPAD_BUTTON_LEFT_FACE_RIGHT, GAMEPAD_BUTTON_LEFT_FACE_DOWN,
                        GAMEPAD_BUTTON_LEFT_FACE_LEFT
                    };
                    for (int i = 0; i < 15; ++i)
                        state->buttons[i] = IsGamepadButtonDown(jid, raylibButtons[i]) ? 1u : 0u;
                    for (int i = 0; i < 6; ++i)
                        state->axes[i] = GetGamepadAxisMovement(jid, i);
                    return 1;
                }
                if (jid != 0)
                    return 0;
                std::lock_guard<std::mutex> lock(g_touchMutex);
                std::copy(g_touchButtons.begin(), g_touchButtons.end(), state->buttons);
                std::copy(g_touchAxes.begin(), g_touchAxes.end(), state->axes);
                return 1;
            }
#else
            int glfwJoystickIsGamepad(int jid);
            int glfwGetGamepadState(int jid, GlfwGamepadState *state);
            const char *glfwGetGamepadName(int jid);
#endif
        }'''
    text = text[:start] + wrappers + text[end:]
elif "Android Touch Controller" not in text:
    fail("Could not locate GLFW gamepad wrappers and no Android wrappers exist")

setters = '''#if defined(__ANDROID__)
    void setTouchButton(int button, bool pressed)
    {
        if (button < 0 || button >= static_cast<int>(g_touchButtons.size()))
            return;
        std::lock_guard<std::mutex> lock(g_touchMutex);
        g_touchButtons[static_cast<size_t>(button)] = pressed ? 1u : 0u;
    }

    void setTouchAxis(int axis, float value)
    {
        if (axis < 0 || axis >= static_cast<int>(g_touchAxes.size()))
            return;
        std::lock_guard<std::mutex> lock(g_touchMutex);
        const float clamped = std::clamp(value, -1.0f, 1.0f);
        g_touchAxes[static_cast<size_t>(axis)] = clamped;
        // Also expose the analog stick as a D-pad so menus that only poll digital buttons work.
        if (axis == 0)
        {
            g_touchButtons[14] = clamped < -0.55f ? 1u : 0u;
            g_touchButtons[12] = clamped > 0.55f ? 1u : 0u;
        }
        else if (axis == 1)
        {
            g_touchButtons[11] = clamped < -0.55f ? 1u : 0u;
            g_touchButtons[13] = clamped > 0.55f ? 1u : 0u;
        }
    }
#endif

'''
if "void setTouchButton(int button" not in text:
    anchor = "    bool connected(int device)\n"
    if anchor not in text:
        fail("Could not locate public gamepad API implementation")
    text = text.replace(anchor, setters + anchor, 1)
gamepads.write_text(text)

header = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_host_gamepads.h"
text = header.read_text()
if "setTouchButton" not in text:
    anchor = "    void update();\n"
    declarations = '''    void update();
#if defined(__ANDROID__)
    void setTouchButton(int button, bool pressed);
    void setTouchAxis(int axis, float value);
#endif
'''
    if anchor not in text:
        fail("Could not locate ps2_host_gamepads.h update declaration")
    header.write_text(text.replace(anchor, declarations, 1))

# Add the native bridge to the Urban Reign host source list.
cmake = root / "src" / "CMakeLists.txt"
text = cmake.read_text()
source_old = "    urban/thread_policy.cpp\n)"
source_new = "    urban/thread_policy.cpp\n    urban/android_bridge.cpp\n)"
if source_new not in text:
    if source_old not in text:
        fail("Could not locate URBAN_HOST_SOURCES list in src/CMakeLists.txt")
    text = text.replace(source_old, source_new, 1)

old_target = "add_executable(urban ${URBAN_HOST_SOURCES})"
new_target = '''if(ANDROID)
    add_library(urban SHARED ${URBAN_HOST_SOURCES})
    target_link_options(urban PRIVATE "-Wl,--undefined=ANativeActivity_onCreate" "-Wl,--wrap=pthread_create")
else()
    add_executable(urban ${URBAN_HOST_SOURCES})
endif()'''
if "add_library(urban SHARED" in text:
    pass  # already configured by an earlier run (the workflow applies this script twice)
elif old_target in text:
    text = text.replace(old_target, new_target, 1)
else:
    fail("Could not configure urban as an Android shared library")

include_old = 'target_include_directories(urban PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}" "${URBAN_GENERATED_DIR}" "${CMAKE_SOURCE_DIR}/modsdk/include")'
include_new = '''target_include_directories(urban PRIVATE
    "${CMAKE_CURRENT_SOURCE_DIR}"
    "${URBAN_GENERATED_DIR}"
    "${CMAKE_SOURCE_DIR}/modsdk/include"
    "${CMAKE_SOURCE_DIR}/runtime/ps2xRuntime/src/lib"
)'''
if include_old in text:
    text = text.replace(include_old, include_new, 1)
elif '"${CMAKE_SOURCE_DIR}/runtime/ps2xRuntime/src/lib"' not in text:
    fail("Could not add runtime private-header include path to urban target")
cmake.write_text(text)

# Main waits for Java to provide the app-specific storage path and selected local ISO.
main = root / "src" / "main.cpp"
text = main.read_text()
include_anchor = '#include "urban/thread_policy.h"'
if '#include "urban/android_bridge.h"' not in text:
    if include_anchor not in text:
        fail("Could not locate main.cpp include list")
    text = text.replace(include_anchor, include_anchor + '\n#include "urban/android_bridge.h"', 1)

fn_anchor = '''    std::filesystem::path executableDirectory()
    {
#if defined(_WIN32)'''
fn_new = '''    std::filesystem::path executableDirectory()
    {
#if defined(__ANDROID__)
        return urban::android::appStorageDirectory();
#endif
#if defined(_WIN32)'''
if fn_anchor in text:
    text = text.replace(fn_anchor, fn_new, 1)
elif "return urban::android::appStorageDirectory();" not in text:
    fail("Could not add Android app storage path")

if "urban::android::waitForIsoSelection()" not in text:
    begin = text.find("    std::filesystem::path iso;\n")
    end_marker = '    if (iso.empty())\n    {\n        URBAN_ERROR(Boot, "no disc image found.'
    end = text.find(end_marker, begin)
    if begin < 0 or end < 0:
        fail("Could not locate ISO discovery block in main.cpp")
    block = text[begin:end]
    line = "    std::filesystem::path iso;\n"
    if not block.startswith(line):
        fail("Unexpected start of ISO discovery block")
    desktop_code = block[len(line):]
    replacement = (
        line
        + "#if defined(__ANDROID__)\n"
        + "    iso = urban::android::waitForIsoSelection();\n"
        + "#else\n"
        + desktop_code
        + "#endif\n"
    )
    text = text[:begin] + replacement + text[end:]
# The upstream GPU GS backend requires desktop OpenGL 4.6 compute shaders. Android
# exposes OpenGL ES on this target, so force the supported CPU GS backend instead of
# trying to compile desktop-only shaders on launch. Keep the desktop default unchanged.
gpu_android_patch = '''#if defined(__ANDROID__)
    GSThreadedBackend::SetGpuByDefault(false);
#else
    GSThreadedBackend::SetGpuByDefault(true);
#endif'''
gpu_default = '    GSThreadedBackend::SetGpuByDefault(true);'
if gpu_android_patch not in text:
    if gpu_default not in text:
        fail("Could not locate default GS GPU backend selection")
    text = text.replace(gpu_default, gpu_android_patch, 1)

main.write_text(text)


# Prevent raylib Android from waiting forever in InitWindow when EGL setup fails.
# raylib itself is fetched by CMake, so patch those files during configure.
runtime_cpp = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_runtime.cpp"
runtime_cpp_text = runtime_cpp.read_text()
runtime_window_anchor = '        InitWindow(HOST_WINDOW_WIDTH, HOST_WINDOW_HEIGHT, title);\n'
runtime_window_patch = runtime_window_anchor + '''#if defined(__ANDROID__)
        if (!IsWindowReady())
        {
            std::cerr << "[UrbanReign][EGL] Android window is not ready after InitWindow" << std::endl;
            return false;
        }
#endif
'''
if "[UrbanReign][EGL] Android window is not ready after InitWindow" not in runtime_cpp_text:
    if runtime_cpp_text.count(runtime_window_anchor) != 1:
        fail("Could not safely add Android InitWindow readiness check")
    runtime_cpp_text = runtime_cpp_text.replace(runtime_window_anchor, runtime_window_patch, 1)
    runtime_cpp.write_text(runtime_cpp_text)

runtime_cmake = root / "runtime" / "ps2xRuntime" / "CMakeLists.txt"
runtime_cmake_text = runtime_cmake.read_text()
raylib_patch_marker = "# Urban Reign Android EGL black-screen guard"
if raylib_patch_marker not in runtime_cmake_text:
    fetch_anchor = "    FetchContent_MakeAvailable(raylib)\n"
    if runtime_cmake_text.count(fetch_anchor) != 1:
        fail("Could not locate raylib FetchContent block")
    cmake_patch = r'''
    # Urban Reign Android EGL black-screen guard
    if(PS2X_IS_ANDROID)
        set(RAYLIB_ANDROID_C "@{raylib_SOURCE_DIR}/src/platforms/rcore_android.c")
        set(RAYLIB_CORE_C "@{raylib_SOURCE_DIR}/src/rcore.c")
        file(READ "@{RAYLIB_ANDROID_C}" RAYLIB_ANDROID_TEXT)
        if(NOT RAYLIB_ANDROID_TEXT MATCHES "URBAN_EGL_CONFIG_FALLBACK")
            set(EGL_OLD [=[
    // Get an appropriate EGL framebuffer configuration
    eglChooseConfig(platform.device, framebufferAttribs, &platform.config, 1, &numConfigs);
]=])
            set(EGL_NEW [=[
    // Get an appropriate EGL framebuffer configuration.
    // URBAN_EGL_CONFIG_FALLBACK retries RGB565 if RGB888 is not available.
    EGLBoolean configResult = eglChooseConfig(platform.device, framebufferAttribs, &platform.config, 1, &numConfigs);
    if (configResult != EGL_TRUE || numConfigs < 1)
    {
        const EGLint fallbackFramebufferAttribs[] = {
            EGL_RENDERABLE_TYPE, EGL_OPENGL_ES2_BIT,
            EGL_RED_SIZE, 5, EGL_GREEN_SIZE, 6, EGL_BLUE_SIZE, 5,
            EGL_DEPTH_SIZE, 16, EGL_NONE
        };
        numConfigs = 0;
        configResult = eglChooseConfig(platform.device, fallbackFramebufferAttribs, &platform.config, 1, &numConfigs);
        if (configResult != EGL_TRUE || numConfigs < 1)
        {
            TRACELOG(LOG_WARNING, "DISPLAY: No compatible EGL framebuffer config (error=0x%x)", eglGetError());
            return -1;
        }
        TRACELOG(LOG_WARNING, "DISPLAY: Using fallback RGB565 EGL framebuffer config");
    }
]=])
            string(FIND "@{RAYLIB_ANDROID_TEXT}" "@{EGL_OLD}" EGL_POS)
            if(EGL_POS LESS 0)
                message(FATAL_ERROR "raylib EGL config block changed; refusing unsafe patch")
            endif()
            string(REPLACE "@{EGL_OLD}" "@{EGL_NEW}" RAYLIB_ANDROID_TEXT "@{RAYLIB_ANDROID_TEXT}")

            set(INIT_OLD [=[
                    // Initialize graphics device (display device and OpenGL context)
                    InitGraphicsDevice();

                    // Initialize OpenGL context (states and resources)
                    // NOTE: CORE.Window.currentFbo.width and CORE.Window.currentFbo.height not used, just stored as globals in rlgl
                    rlglInit(CORE.Window.currentFbo.width, CORE.Window.currentFbo.height);
                    isGpuReady = true;
]=])
            set(INIT_NEW [=[
                    // Initialize graphics device (display device and OpenGL context).
                    if (InitGraphicsDevice() != 0)
                    {
                        TRACELOG(LOG_WARNING, "ANDROID: EGL initialization failed; leaving InitWindow wait");
                        CORE.Window.shouldClose = true;
                        ANativeActivity_finish(app->activity);
                        return;
                    }

                    // Initialize OpenGL context (states and resources).
                    rlglInit(CORE.Window.currentFbo.width, CORE.Window.currentFbo.height);
                    isGpuReady = true;
]=])
            string(FIND "@{RAYLIB_ANDROID_TEXT}" "@{INIT_OLD}" INIT_POS)
            if(INIT_POS LESS 0)
                message(FATAL_ERROR "raylib Android init block changed; refusing unsafe patch")
            endif()
            string(REPLACE "@{INIT_OLD}" "@{INIT_NEW}" RAYLIB_ANDROID_TEXT "@{RAYLIB_ANDROID_TEXT}")

            set(WAIT_OLD [=[
    while (!CORE.Window.ready)
    {
        // Process events until we reach TIMEOUT, which indicates no more events queued.
        while ((pollResult = ALooper_pollOnce(0, NULL, &pollEvents, (void**)&platform.source)) > ALOOPER_POLL_TIMEOUT)
        {
            // Process this event
            if (platform.source != NULL) platform.source->process(platform.app, platform.source);

            // NOTE: It's highly likely destroyRequested will never be non-zero at the start of the activity lifecycle.
            //if (platform.app->destroyRequested != 0) CORE.Window.shouldClose = true;
        }
    }

    return 0;
}
]=])
            set(WAIT_NEW [=[
    while (!CORE.Window.ready && !CORE.Window.shouldClose && platform.app->destroyRequested == 0)
    {
        while ((pollResult = ALooper_pollOnce(0, NULL, &pollEvents, (void**)&platform.source)) > ALOOPER_POLL_TIMEOUT)
        {
            if (platform.source != NULL) platform.source->process(platform.app, platform.source);
        }
    }
    if (!CORE.Window.ready)
    {
        TRACELOG(LOG_WARNING, "ANDROID: Window initialization cancelled; EGL never became ready");
        return -1;
    }
    return 0;
}
]=])
            string(FIND "@{RAYLIB_ANDROID_TEXT}" "@{WAIT_OLD}" WAIT_POS)
            if(WAIT_POS LESS 0)
                message(FATAL_ERROR "raylib Android wait block changed; refusing unsafe patch")
            endif()
            string(REPLACE "@{WAIT_OLD}" "@{WAIT_NEW}" RAYLIB_ANDROID_TEXT "@{RAYLIB_ANDROID_TEXT}")
            file(WRITE "@{RAYLIB_ANDROID_C}" "@{RAYLIB_ANDROID_TEXT}")
        endif()

        file(READ "@{RAYLIB_CORE_C}" RAYLIB_CORE_TEXT)
        if(NOT RAYLIB_CORE_TEXT MATCHES "URBAN_ANDROID_WINDOW_READY_GUARD")
            set(PLATFORM_OLD [=[
    // Initialize platform
    //--------------------------------------------------------------
    InitPlatform();
    //--------------------------------------------------------------

]=])
            set(PLATFORM_NEW [=[
    // Initialize platform
    //--------------------------------------------------------------
    InitPlatform();
    // URBAN_ANDROID_WINDOW_READY_GUARD: do not create GL resources without a valid EGL context.
    if (!CORE.Window.ready)
    {
        TRACELOG(LOG_WARNING, "ANDROID: InitPlatform returned without a ready EGL window");
        CORE.Window.shouldClose = true;
        return;
    }
    //--------------------------------------------------------------

]=])
            string(FIND "@{RAYLIB_CORE_TEXT}" "@{PLATFORM_OLD}" PLATFORM_POS)
            if(PLATFORM_POS LESS 0)
                message(FATAL_ERROR "raylib InitWindow block changed; refusing unsafe patch")
            endif()
            string(REPLACE "@{PLATFORM_OLD}" "@{PLATFORM_NEW}" RAYLIB_CORE_TEXT "@{RAYLIB_CORE_TEXT}")
            file(WRITE "@{RAYLIB_CORE_C}" "@{RAYLIB_CORE_TEXT}")
        endif()
    endif()
'''.replace("@{", "$" + "{")
    runtime_cmake_text = runtime_cmake_text.replace(fetch_anchor, fetch_anchor + cmake_patch, 1)
    runtime_cmake.write_text(runtime_cmake_text)

# Android SAF descriptors can be duplicated directly. Reopening /proc/self/fd/N
# with fopen() is provider-dependent; retain zero-copy access by dup()+fdopen().
image_cpp = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_cd_image.cpp"
image_text = image_cpp.read_text()
include_anchor = "#include <vector>"
include_block = """#include <vector>
#if defined(__ANDROID__)
#include <cerrno>
#include <cstdlib>
#include <sys/stat.h>
#include <unistd.h>
#endif"""
if include_block not in image_text:
    if include_anchor not in image_text:
        fail("Could not locate ps2_cd_image.cpp include block")
    image_text = image_text.replace(include_anchor, include_block, 1)

open_old = '''#if defined(_WIN32)
    m_file = ::_wfopen(imagePath.c_str(), L"rb");
#else
    m_file = std::fopen(imagePath.string().c_str(), "rb");
#endif'''
open_new = '''#if defined(__ANDROID__)
    const std::string imageName = imagePath.string();
    constexpr char fdPrefix[] = "/proc/self/fd/";
    if (imageName.rfind(fdPrefix, 0) == 0)
    {
        const char *number = imageName.c_str() + sizeof(fdPrefix) - 1;
        char *end = nullptr;
        errno = 0;
        const long requestedFd = std::strtol(number, &end, 10);
        if (errno == 0 && end != number && *end == '\\0' &&
            requestedFd >= 0 &&
            requestedFd <= static_cast<long>(std::numeric_limits<int>::max()))
        {
            const int duplicateFd = ::dup(static_cast<int>(requestedFd));
            if (duplicateFd >= 0)
            {
                m_file = ::fdopen(duplicateFd, "rb");
                if (!m_file)
                    ::close(duplicateFd);
            }
        }
    }
    else
    {
        m_file = std::fopen(imageName.c_str(), "rb");
    }
#elif defined(_WIN32)
    m_file = ::_wfopen(imagePath.c_str(), L"rb");
#else
    m_file = std::fopen(imagePath.string().c_str(), "rb");
#endif'''
if open_old in image_text:
    image_text = image_text.replace(open_old, open_new, 1)
elif 'm_file = ::fdopen(duplicateFd, "rb");' not in image_text:
    fail("Could not locate PS2CdImage file-open block")

size_old = '''    std::error_code error;
    m_size = std::filesystem::file_size(imagePath, error);
    m_path = imagePath;'''
size_new = '''#if defined(__ANDROID__)
    struct stat descriptorInfo{};
    if (::fstat(::fileno(m_file), &descriptorInfo) != 0 || descriptorInfo.st_size <= 0)
    {
        std::cerr << "[CDIMAGE] cannot get ISO size from Android file descriptor" << std::endl;
        std::fclose(m_file);
        m_file = nullptr;
        return false;
    }
    if (::fseeko(m_file, 0, SEEK_SET) != 0)
    {
        std::cerr << "[CDIMAGE] selected Android file is not seekable; select a local ISO file" << std::endl;
        std::fclose(m_file);
        m_file = nullptr;
        return false;
    }
    m_size = static_cast<uint64_t>(descriptorInfo.st_size);
#else
    std::error_code error;
    m_size = std::filesystem::file_size(imagePath, error);
#endif
    m_path = imagePath;'''
if size_old in image_text:
    image_text = image_text.replace(size_old, size_new, 1)
elif "cannot get ISO size from Android file descriptor" not in image_text:
    fail("Could not locate PS2CdImage file-size block")
image_cpp.write_text(image_text)

# Drop the JNI bridge into the upstream checkout; it remains only in the APK build workspace.
dest = root / "src" / "urban"
dest.mkdir(parents=True, exist_ok=True)
resources = Path(__file__).resolve().parents[1] / "native"
shutil.copyfile(resources / "android_bridge.cpp", dest / "android_bridge.cpp")
shutil.copyfile(resources / "android_bridge.h", dest / "android_bridge.h")

# Diagnostics: write a line to urban_native.log as soon as the native game thread reaches main(),
# so the in-app diagnostic log shows whether the thread starts at all. Never fatal.
_main_text = main.read_text()
_stage_marker = 'urban::android::logStage("main() entered");'
if _stage_marker not in _main_text:
    _match = re.search(r"\bint\s+main\s*\([^)]*\)\s*\{", _main_text)
    if _match:
        _main_text = (
            _main_text[: _match.end()]
            + "\n#if defined(__ANDROID__)\n    " + _stage_marker + "\n#endif"
            + _main_text[_match.end():]
        )
        main.write_text(_main_text)
        print("Added main() entry log line")
    else:
        print("WARNING: could not find int main(...) to add the diagnostic log line")


# ---------------------------------------------------------------------------------------------------------
# ROOT-CAUSE FIX 1: urban.exe sets these runtime defaults only inside `#if defined(_WIN32)` in main.cpp.
# Without them Android uses the HLE memory-card service, which upstream's docs (PROGRESS.md 2026-10-05) say
# makes the game never get past memory-card init (=> never draws a frame => black screen), and PADMAN does not
# accept the controller. Set the same values on Android (an environment variable set by hand still wins).
# ---------------------------------------------------------------------------------------------------------
_main_text = main.read_text()
_env_marker = "URBAN_ANDROID_RUNTIME_DEFAULTS"
if _env_marker not in _main_text:
    _anchor = "    try\n    {\n        PS2Runtime runtime;"
    if _main_text.count(_anchor) != 1:
        fail("Could not find the PS2Runtime construction block in main.cpp (Android runtime defaults)")
    _env_block = (
        "#if defined(__ANDROID__)\n"
        "    // " + _env_marker + ": the Windows build sets these in its _WIN32 block above.\n"
        "    setenv(\"PS2X_IOP_HLE_MCSERV\", \"0\", 0); // the game's own MCMAN/MCSERV on the emulated SIO2 card\n"
        "    setenv(\"PS2X_SIO2_RECV1\", \"2\", 0);     // PADMAN accepts the pad\n"
        "    setenv(\"PS2X_EE_LOOP_CYCLES\", \"16\", 0); // frame/vblank timing the game expects\n"
        "    setenv(\"PS2X_MTVU\", \"0\", 0);             // Android default: avoid async VU/GIF overruns on slower phones\n"
        "#endif\n\n"
    )
    _main_text = _main_text.replace(_anchor, _env_block + _anchor, 1)
    main.write_text(_main_text)
    print("Added Android runtime defaults (MCSERV/SIO2/EE loop/synchronous VU-GIF) to main.cpp")

# The guest-kernel hang dump (URBAN_HANG_DUMP=<seconds>) exists only for Windows upstream. dumpGuestKernel() itself
# is portable (EE threads, semaphores, PCs); enable that part on Android so the log shows where the game waits.
_hang = root / "src" / "urban" / "hang_dump.cpp"
_hang_text = _hang.read_text()
if "URBAN_ANDROID_HANG_DUMP" not in _hang_text:
    _hang_old = "#else\n    void startFromEnvironment() {}\n#endif"
    if _hang_text.count(_hang_old) == 1:
        _hang_new = """#elif defined(__ANDROID__)
    // URBAN_ANDROID_HANG_DUMP: guest EE kernel snapshot only (no Windows native stacks).
    void startFromEnvironment()
    {
        const char *delay = std::getenv("URBAN_HANG_DUMP");
        if (!delay || !*delay)
            return;
        const int delaySeconds = std::atoi(delay);
        const char *repeat = std::getenv("URBAN_HANG_DUMP_REPEAT");
        const int repeatSeconds = repeat ? std::atoi(repeat) : 0;
        std::thread([delaySeconds, repeatSeconds]() {
            std::this_thread::sleep_for(std::chrono::seconds(delaySeconds));
            do
            {
                dumpGuestKernel();
                if (repeatSeconds > 0)
                    std::this_thread::sleep_for(std::chrono::seconds(repeatSeconds));
            } while (repeatSeconds > 0);
        }).detach();
    }
#else
    void startFromEnvironment() {}
#endif"""
        _hang.write_text(_hang_text.replace(_hang_old, _hang_new, 1))
        print("Enabled guest-kernel hang dump on Android")
    else:
        print("WARNING: hang_dump.cpp layout changed; Android hang dump not enabled")

print("Applied Android host, document-picker, ARM64 intrinsic and virtual touch-gamepad patches.")

# Android port build trigger: include the corrected Java diagnostic logging in the APK build.


# PS2CdImage folder-backed virtual ISO patch. This is idempotent because this script
# runs both before and after code generation in the Android workflow.
disc_header = root / "runtime" / "ps2xRuntime" / "include" / "runtime" / "ps2_cd_image.h"
disc_source = root / "runtime" / "ps2xRuntime" / "src" / "lib" / "ps2_cd_image.cpp"
if not disc_header.is_file() or not disc_source.is_file():
    fail("Could not find upstream PS2CdImage header/source for extracted-folder support")

header_text = disc_header.read_text()
header_marker = "URBAN_FOLDER_DISC_FIELDS"
if header_marker not in header_text:
    if "#include <map>" not in header_text:
        header_text = header_text.replace("#include <mutex>", "#include <mutex>\n#include <map>\n#include <vector>", 1)
    extent_decl = """// File payload mapped into a synthetic ISO9660 sector range for an extracted game folder.
struct PS2CdImageFileExtent
{
    std::filesystem::path path;
    uint64_t sizeBytes = 0;
    uint64_t allocatedBytes = 0;
};

"""
    if "struct PS2CdImageFileExtent" not in header_text:
        if "class PS2CdImage\n{" not in header_text:
            fail("PS2CdImage class declaration changed; cannot add folder-disc extent type")
        header_text = header_text.replace("class PS2CdImage\n{", extent_decl + "class PS2CdImage\n{", 1)
    method_anchor = "    bool readUnlocked(uint64_t offset, void *destination, size_t size) const;"
    if "readFolderUnlocked" not in header_text:
        if method_anchor not in header_text:
            fail("PS2CdImage private read method changed; cannot add folder reader")
        header_text = header_text.replace(method_anchor, method_anchor + "\n    bool readFolderUnlocked(uint64_t offset, void *destination, size_t size) const;", 1)
    member_anchor = "    std::FILE *m_file = nullptr;"
    fields = """    std::FILE *m_file = nullptr;
    // URBAN_FOLDER_DISC_FIELDS
    bool m_folderMode = false;
    std::vector<uint8_t> m_virtualPrefix;
    std::map<uint64_t, PS2CdImageFileExtent> m_virtualFiles;"""
    if member_anchor not in header_text:
        fail("PS2CdImage file member changed; cannot add folder-disc state")
    header_text = header_text.replace(member_anchor, fields, 1)
    disc_header.write_text(header_text)

source_text = disc_source.read_text()
if "buildSyntheticFolderImage" not in source_text:
    source_text = source_text.replace(
        "#include <limits>\n#include <vector>",
        "#include <limits>\n#include <map>\n#include <memory>\n#include <set>\n#include <iterator>\n#include <vector>",
        1
    )
    support_path = Path(__file__).resolve().parent / "ps2_folder_disc_support.inc"
    if not support_path.is_file():
        fail("Missing patches/ps2_folder_disc_support.inc")
    support = support_path.read_text()
    instance_anchor = "PS2CdImage &PS2CdImage::instance()"
    if instance_anchor not in source_text:
        fail("PS2CdImage instance implementation moved; cannot insert virtual folder reader")
    source_text = source_text.replace(instance_anchor, support + "\n\n" + instance_anchor, 1)

    open_start = source_text.find("bool PS2CdImage::open(const std::filesystem::path &imagePath)")
    open_end = source_text.find("\nbool PS2CdImage::parseDirectory(", open_start)
    if open_start < 0 or open_end < 0:
        fail("PS2CdImage::open/parseDirectory boundaries changed; cannot add folder mounting")
    folder_open = r'''bool PS2CdImage::open(const std::filesystem::path &imagePath)
{
    std::lock_guard<std::mutex> lock(m_mutex);
    m_openAttempted = true;
    if (m_file)
    {
        std::fclose(m_file);
        m_file = nullptr;
    }
    m_entries.clear();
    m_folderMode = false;
    m_virtualPrefix.clear();
    m_virtualFiles.clear();
    m_size = 0;
    m_path.clear();

    std::error_code folderError;
    const bool folderInput = std::filesystem::is_directory(imagePath, folderError) && !folderError;
    if (folderInput)
    {
        m_path = imagePath;
        m_folderMode = true;
        if (!buildSyntheticFolderImage(imagePath, m_virtualPrefix, m_virtualFiles, m_size))
        {
            std::cerr << "[CDIMAGE] folder does not contain a supported extracted Urban Reign disc: "
                      << imagePath.string() << std::endl;
            m_folderMode = false;
            m_virtualPrefix.clear();
            m_virtualFiles.clear();
            m_size = 0;
            return false;
        }
    }
    else
    {
#if defined(__ANDROID__)
        const std::string imageName = imagePath.string();
        constexpr char fdPrefix[] = "/proc/self/fd/";
        if (imageName.rfind(fdPrefix, 0) == 0)
        {
            const char *number = imageName.c_str() + sizeof(fdPrefix) - 1;
            char *end = nullptr;
            errno = 0;
            const long requestedFd = std::strtol(number, &end, 10);
            if (errno == 0 && end != number && *end == '\0' &&
                requestedFd >= 0 &&
                requestedFd <= static_cast<long>(std::numeric_limits<int>::max()))
            {
                const int duplicateFd = ::dup(static_cast<int>(requestedFd));
                if (duplicateFd >= 0)
                {
                    m_file = ::fdopen(duplicateFd, "rb");
                    if (!m_file)
                        ::close(duplicateFd);
                }
            }
        }
        else
        {
            m_file = std::fopen(imageName.c_str(), "rb");
        }
#elif defined(_WIN32)
        m_file = ::_wfopen(imagePath.c_str(), L"rb");
#else
        m_file = std::fopen(imagePath.string().c_str(), "rb");
#endif
        if (!m_file)
        {
            std::cerr << "[CDIMAGE] cannot open " << imagePath.string() << std::endl;
            return false;
        }

#if defined(__ANDROID__)
        struct stat descriptorInfo{};
        if (::fstat(::fileno(m_file), &descriptorInfo) != 0 || descriptorInfo.st_size <= 0)
        {
            std::cerr << "[CDIMAGE] cannot get ISO size from Android file descriptor" << std::endl;
            std::fclose(m_file);
            m_file = nullptr;
            return false;
        }
        if (::fseeko(m_file, 0, SEEK_SET) != 0)
        {
            std::cerr << "[CDIMAGE] selected Android file is not seekable; select a local ISO file" << std::endl;
            std::fclose(m_file);
            m_file = nullptr;
            return false;
        }
        m_size = static_cast<uint64_t>(descriptorInfo.st_size);
#else
        std::error_code sizeError;
        m_size = std::filesystem::file_size(imagePath, sizeError);
        if (sizeError)
        {
            std::fclose(m_file);
            m_file = nullptr;
            m_size = 0;
            return false;
        }
#endif
        m_path = imagePath;
    }

    std::vector<uint8_t> sector(kSectorSize);
    for (uint32_t lba = 16;; ++lba)
    {
        if (!readUnlocked(static_cast<uint64_t>(lba) * kSectorSize, sector.data(), kSectorSize) ||
            std::memcmp(sector.data() + 1, "CD001", 5) != 0 || sector[0] == 255)
        {
            std::cerr << "[CDIMAGE] " << imagePath.string() << " is not an ISO9660 image" << std::endl;
            if (m_file)
            {
                std::fclose(m_file);
                m_file = nullptr;
            }
            m_folderMode = false;
            m_virtualPrefix.clear();
            m_virtualFiles.clear();
            m_entries.clear();
            m_size = 0;
            m_path.clear();
            return false;
        }
        if (sector[0] == 1)
            break;
        if (lba == UINT32_MAX)
            return false;
    }
    const uint8_t *root = sector.data() + 156;
    if (!parseDirectory(le32(root + 2), le32(root + 10), std::string(), 0))
    {
        if (m_file)
        {
            std::fclose(m_file);
            m_file = nullptr;
        }
        m_folderMode = false;
        m_virtualPrefix.clear();
        m_virtualFiles.clear();
        m_entries.clear();
        m_size = 0;
        m_path.clear();
        return false;
    }
    std::cout << "[CDIMAGE] mounted " << imagePath.string() << " ("
              << m_entries.size() << " entries" << (folderInput ? ", extracted-folder view" : "") << ")" << std::endl;
    return true;
}'''
    source_text = source_text[:open_start] + folder_open + source_text[open_end:]

    read_anchor = """bool PS2CdImage::readUnlocked(uint64_t offset, void *destination, size_t size) const
{
    if (!m_file || !seekFile(m_file, offset))
"""
    read_patch = """bool PS2CdImage::readUnlocked(uint64_t offset, void *destination, size_t size) const
{
    if (m_folderMode)
        return readFolderUnlocked(offset, destination, size);
    if (!m_file || !seekFile(m_file, offset))
"""
    if read_anchor not in source_text:
        fail("PS2CdImage::readUnlocked body changed; cannot route virtual folder reads")
    source_text = source_text.replace(read_anchor, read_patch, 1)

    source_text = source_text.replace(
        "    m_entries.clear();\n    m_openAttempted = false;\n}",
        "    m_entries.clear();\n    m_folderMode = false;\n    m_virtualPrefix.clear();\n    m_virtualFiles.clear();\n    m_size = 0;\n    m_path.clear();\n    m_openAttempted = false;\n}",
        1
    )
    source_text = source_text.replace(
        "    return m_file != nullptr;\n}",
        "    return m_file != nullptr || m_folderMode;\n}",
        1
    )
    source_text = source_text.replace(
        "    if (!m_file)\n    {\n        return false;\n    }\n    const auto it = m_entries.find(key);",
        "    if (!m_file && !m_folderMode)\n    {\n        return false;\n    }\n    const auto it = m_entries.find(key);",
        1
    )
    ensure_old = """        if (m_file)
        {
            return true;
        }"""
    ensure_new = """        if (m_file || m_folderMode)
        {
            return true;
        }"""
    if ensure_old in source_text:
        source_text = source_text.replace(ensure_old, ensure_new, 1)
    elif ensure_new not in source_text:
        fail("PS2CdImage::ensureOpenFromIoPaths changed; cannot preserve folder mount")
    disc_source.write_text(source_text)
elif "readFolderUnlocked" not in source_text or "URBAN_FOLDER_DISC_FIELDS" not in disc_header.read_text():
    fail("Partial folder-disc patch detected; refusing to silently leave a broken native source")


# ---------------------------------------------------------------------------------------------------------
# ROOT-CAUSE FIX 3: sceCdRead (Kernel/Stubs/Helpers/Support.h) opened the disc by path with std::ifstream /
# file_size. On Android the disc is "/proc/self/fd/N" or an extracted folder (synthetic ISO inside PS2CdImage),
# which cannot be opened/sized that way => "[sceCdRead] unresolved request" forever => game never leaves its
# loading loop => black screen. Route Android reads through PS2CdImage (see android_cd_read_patch.py).
# ---------------------------------------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent))
import android_cd_read_patch
print("Android CD read patch:", android_cd_read_patch.apply(root))
