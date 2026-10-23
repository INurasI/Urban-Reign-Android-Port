#include "urban/android_bridge.h"
#include "urban/game_disc.h"

#if defined(__ANDROID__)
#include "ps2_host_gamepads.h"

#include <android/log.h>
#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <jni.h>
#include <pthread.h>
#include <signal.h>
#include <sys/syscall.h>
#include <unistd.h>
#include <unwind.h>

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <mutex>
#include <string>

namespace
{
    std::mutex g_storageMutex;
    std::condition_variable g_storageChanged;
    std::string g_storagePath;
    bool g_storageReady = false;

    std::mutex g_isoMutex;
    std::condition_variable g_isoChanged;
    std::string g_isoPath;
    bool g_isoReady = false;

    // How long the native game thread may wait for Java before giving up. Java sets both
    // values BEFORE NativeActivity starts, so in normal operation this never triggers.
    constexpr auto kJavaHandshakeTimeout = std::chrono::seconds(8);

    void logNative(const char *message)
    {
        std::fprintf(stderr, "[UrbanReign][native] %s\n", message);
        std::fflush(stderr);
        __android_log_print(ANDROID_LOG_INFO, "UrbanReignDiag", "%s", message);
    }

    std::string javaString(JNIEnv *env, jstring value)
    {
        if (!value)
            return {};
        const char *utf = env->GetStringUTFChars(value, nullptr);
        if (!utf)
            return {};
        std::string result(utf);
        env->ReleaseStringUTFChars(value, utf);
        return result;
    }

    // ---------------------------------------------------------------------------------
    // Thread dumper: shows WHERE every native thread is stuck (written to urban_native.log).
    // Used by the Java watchdog when the screen stays black / the UI thread stalls.
    // ---------------------------------------------------------------------------------
    constexpr int kMaxFrames = 40;
    std::atomic<int> g_traceState{0}; // 0 idle, 1 requested, 2 filled
    void *g_traceFrames[kMaxFrames];
    int g_traceCount = 0;
    std::mutex g_dumpMutex;
    bool g_traceHandlerInstalled = false;

    struct TraceCollector
    {
        int count;
    };

    _Unwind_Reason_Code collectFrame(struct _Unwind_Context *context, void *arg)
    {
        auto *collector = static_cast<TraceCollector *>(arg);
        const uintptr_t pc = _Unwind_GetIP(context);
        if (pc == 0 || collector->count >= kMaxFrames)
            return _URC_END_OF_STACK;
        g_traceFrames[collector->count++] = reinterpret_cast<void *>(pc);
        return _URC_NO_REASON;
    }

    void traceSignalHandler(int, siginfo_t *, void *)
    {
        if (g_traceState.load(std::memory_order_acquire) != 1)
            return;
        TraceCollector collector{0};
        _Unwind_Backtrace(collectFrame, &collector);
        g_traceCount = collector.count;
        g_traceState.store(2, std::memory_order_release);
    }

    std::string readProcText(const std::string &path)
    {
        std::FILE *file = std::fopen(path.c_str(), "r");
        if (!file)
            return {};
        char buffer[512];
        const size_t n = std::fread(buffer, 1, sizeof(buffer) - 1, file);
        std::fclose(file);
        buffer[n] = '\0';
        std::string text(buffer);
        while (!text.empty() && (text.back() == '\n' || text.back() == '\r'))
            text.pop_back();
        return text;
    }

    bool startsWithAny(const std::string &text, const char *const *prefixes, size_t count)
    {
        for (size_t i = 0; i < count; ++i)
        {
            if (text.rfind(prefixes[i], 0) == 0)
                return true;
        }
        return false;
    }

    void printFrames()
    {
        for (int i = 0; i < g_traceCount; ++i)
        {
            const uintptr_t pc = reinterpret_cast<uintptr_t>(g_traceFrames[i]);
            Dl_info info{};
            if (dladdr(g_traceFrames[i], &info) && info.dli_fname)
            {
                const char *slash = std::strrchr(info.dli_fname, '/');
                const char *lib = slash ? slash + 1 : info.dli_fname;
                if (info.dli_sname && info.dli_saddr)
                {
                    std::fprintf(stderr, "[UrbanReign][threads]     #%02d %s+0x%lx (%s+0x%lx)\n", i, info.dli_sname,
                                 static_cast<unsigned long>(pc - reinterpret_cast<uintptr_t>(info.dli_saddr)), lib,
                                 static_cast<unsigned long>(pc - reinterpret_cast<uintptr_t>(info.dli_fbase)));
                }
                else
                {
                    std::fprintf(stderr, "[UrbanReign][threads]     #%02d %s+0x%lx\n", i, lib,
                                 static_cast<unsigned long>(pc - reinterpret_cast<uintptr_t>(info.dli_fbase)));
                }
            }
            else
            {
                std::fprintf(stderr, "[UrbanReign][threads]     #%02d 0x%lx\n", i, static_cast<unsigned long>(pc));
            }
        }
        std::fflush(stderr);
    }

    void dumpAllNativeThreads()
    {
        std::lock_guard<std::mutex> guard(g_dumpMutex);
        const int signalNumber = SIGRTMIN + 2;
        if (!g_traceHandlerInstalled)
        {
            struct sigaction action;
            std::memset(&action, 0, sizeof(action));
            action.sa_sigaction = traceSignalHandler;
            action.sa_flags = SA_SIGINFO | SA_RESTART;
            sigemptyset(&action.sa_mask);
            sigaction(signalNumber, &action, nullptr);
            g_traceHandlerInstalled = true;
        }

        static const char *const kSkip[] = {
            "Signal Catcher", "FinalizerDaemon", "FinalizerWatchd", "ReferenceQueue", "HeapTaskDaemon",
            "Jit thread pool", "Binder:", "RenderThread", "hwuiTask", "ADB-JDWP", "Profile Saver",
            "perfetto_hprof", "Runtime worker", "queued-work-loo", "mali-", "CrAsync", "urban-watchdog",
        };

        const pid_t pid = getpid();
        const pid_t self = static_cast<pid_t>(syscall(SYS_gettid));
        std::fprintf(stderr, "[UrbanReign][threads] ===== native thread dump (pid=%d) =====\n", static_cast<int>(pid));

        DIR *dir = opendir("/proc/self/task");
        if (!dir)
        {
            std::fprintf(stderr, "[UrbanReign][threads] cannot open /proc/self/task\n");
            std::fflush(stderr);
            return;
        }
        int skipped = 0;
        while (dirent *entry = readdir(dir))
        {
            const int tid = std::atoi(entry->d_name);
            if (tid <= 0 || tid == self)
                continue;
            const std::string base = std::string("/proc/self/task/") + entry->d_name;
            const std::string comm = readProcText(base + "/comm");
            if (startsWithAny(comm, kSkip, sizeof(kSkip) / sizeof(kSkip[0])))
            {
                ++skipped;
                continue;
            }
            const std::string stat = readProcText(base + "/stat");
            char state = '?';
            const size_t close = stat.rfind(')');
            if (close != std::string::npos && close + 2 < stat.size())
                state = stat[close + 2];
            const std::string wchan = readProcText(base + "/wchan");
            std::fprintf(stderr, "[UrbanReign][threads] tid=%d%s name='%s' state=%c wchan=%s\n", tid,
                         tid == pid ? " (UI/main thread)" : "", comm.c_str(), state, wchan.c_str());

            // The Java UI thread is dumped from Java; unwinding through ART frames is not worth the risk.
            if (tid == pid)
                continue;

            g_traceCount = 0;
            g_traceState.store(1, std::memory_order_release);
            if (syscall(SYS_tgkill, pid, tid, signalNumber) != 0)
            {
                g_traceState.store(0, std::memory_order_release);
                std::fprintf(stderr, "[UrbanReign][threads]     (could not signal thread)\n");
                continue;
            }
            bool filled = false;
            for (int i = 0; i < 40; ++i)
            {
                if (g_traceState.load(std::memory_order_acquire) == 2)
                {
                    filled = true;
                    break;
                }
                usleep(5000);
            }
            if (filled)
                printFrames();
            else
                std::fprintf(stderr, "[UrbanReign][threads]     (no stack: thread did not answer within 200 ms)\n");
            g_traceState.store(0, std::memory_order_release);
        }
        closedir(dir);
        std::fprintf(stderr, "[UrbanReign][threads] ===== end of dump (%d system threads skipped) =====\n", skipped);
        std::fflush(stderr);
    }
}

// ---------------------------------------------------------------------------------------------------------
// ROOT-CAUSE FIX 2: guest (recompiled PS2) call chains run on the HOST stack of the EE game thread
// (std::thread inside PS2Runtime::run()). The Windows build links with /STACK:16777216, which is also the default
// size of every thread it creates. Android/bionic gives every std::thread only 1 MiB, so deep guest call chains
// overflow it. The CMake patch links liburban.so with -Wl,--wrap=pthread_create, which routes the (statically linked)
// libc++ std::thread, the NativeActivity glue thread and every other thread of this library through this function.
// ---------------------------------------------------------------------------------------------------------
// Weak: targets that are not linked with --wrap (e.g. the CI probe library) still link; there this function is unused.
extern "C" __attribute__((weak)) int __real_pthread_create(pthread_t *, const pthread_attr_t *, void *(*)(void *), void *);

extern "C" int __wrap_pthread_create(pthread_t *thread, const pthread_attr_t *attr, void *(*start)(void *), void *arg)
{
    if (!__real_pthread_create)
        return EAGAIN; // cannot happen when linked with --wrap=pthread_create
    constexpr size_t kWantedStack = 16u * 1024u * 1024u;
    pthread_attr_t local;
    if (attr)
        local = *attr;
    else
        pthread_attr_init(&local);

    size_t current = 0;
    const bool needsBigger = pthread_attr_getstacksize(&local, &current) != 0 || current < kWantedStack;
    int result = -1;
    if (needsBigger && pthread_attr_setstacksize(&local, kWantedStack) == 0)
        result = __real_pthread_create(thread, &local, start, arg);
    if (!attr)
        pthread_attr_destroy(&local);
    if (result != 0) // not needed, or the big stack could not be reserved: behave exactly like the original call
        result = __real_pthread_create(thread, attr, start, arg);
    return result;
}

namespace urban::android
{
    void logStage(const char *message)
    {
        logNative(message ? message : "(null)");
    }

    std::filesystem::path appStorageDirectory()
    {
        logNative("Native startup: waiting for app storage path from Java");
        std::unique_lock<std::mutex> lock(g_storageMutex);
        if (!g_storageChanged.wait_for(lock, kJavaHandshakeTimeout, [] { return g_storageReady; }))
        {
            logNative("Native startup: TIMEOUT waiting for app storage path; using current directory");
            return std::filesystem::current_path();
        }
        logNative("Native startup: app storage path received");
        return std::filesystem::path(g_storagePath);
    }

    std::filesystem::path waitForIsoSelection()
    {
        logNative("Native startup: waiting for selected ISO path from Java");
        std::unique_lock<std::mutex> lock(g_isoMutex);
        if (!g_isoChanged.wait_for(lock, kJavaHandshakeTimeout, [] { return g_isoReady; }))
        {
            logNative("Native startup: TIMEOUT waiting for ISO path; continuing with empty path");
            return std::filesystem::path();
        }
        logNative(g_isoPath.empty() ? "Native startup: ISO path is empty" : "Native startup: ISO path received");
        return std::filesystem::path(g_isoPath);
    }
}

extern "C" JNIEXPORT void JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeSetAppStoragePath(
    JNIEnv *env, jobject, jstring path)
{
    const std::string value = javaString(env, path);
    if (!value.empty())
    {
        const std::string logPath = value + "/urban_native.log";
        std::freopen(logPath.c_str(), "a", stderr);
        std::freopen(logPath.c_str(), "a", stdout);
        std::setvbuf(stderr, nullptr, _IONBF, 0);
        std::setvbuf(stdout, nullptr, _IONBF, 0);
    }
    {
        std::lock_guard<std::mutex> lock(g_storageMutex);
        g_storagePath = value;
        g_storageReady = true;
    }
    logNative("Java supplied app storage path; native stdout/stderr are being saved to urban_native.log");
    g_storageChanged.notify_all();
}

extern "C" JNIEXPORT void JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeSetIsoPath(
    JNIEnv *env, jobject, jstring path)
{
    const std::string value = javaString(env, path);
    {
        std::lock_guard<std::mutex> lock(g_isoMutex);
        g_isoPath = value;
        g_isoReady = true;
    }
    logNative(value.empty() ? "Java cleared the ISO path" : "Java supplied the ISO path");
    g_isoChanged.notify_all();
}

extern "C" JNIEXPORT void JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeDumpThreads(JNIEnv *, jobject)
{
    dumpAllNativeThreads();
}

extern "C" JNIEXPORT void JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeTouchButton(
    JNIEnv *, jobject, jint button, jboolean pressed)
{
    ps2_host_gamepads::setTouchButton(static_cast<int>(button), pressed == JNI_TRUE);
}

extern "C" JNIEXPORT void JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeTouchAxis(
    JNIEnv *, jobject, jint axis, jfloat value)
{
    ps2_host_gamepads::setTouchAxis(static_cast<int>(axis), static_cast<float>(value));
}

extern "C" JNIEXPORT jstring JNICALL
Java_kz_nurasql77_urbanreign_MainActivity_nativeValidateIsoPath(
    JNIEnv *env, jobject, jstring isoPath, jstring cacheDirectory)
{
    const std::string image = javaString(env, isoPath);
    const std::string cache = javaString(env, cacheDirectory);
    logNative("ISO validation started");
    if (image.empty() || cache.empty())
    {
        logNative("ISO validation failed: missing ISO path or cache directory");
        return env->NewStringUTF("Missing ISO path or app cache directory.");
    }

    try
    {
        const urban::DiscCheckResult result =
            urban::verifyDiscAndPrepare(std::filesystem::path(image), std::filesystem::path(cache));
        logNative(result.ok ? "ISO validation succeeded" : "ISO validation failed");
        return env->NewStringUTF(result.ok ? "" : result.error.c_str());
    }
    catch (const std::exception &error)
    {
        std::fprintf(stderr, "[UrbanReign][native] ISO validation exception: %s\n", error.what());
        std::fflush(stderr);
        return env->NewStringUTF(error.what());
    }
    catch (...)
    {
        logNative("ISO validation failed: unknown native exception");
        return env->NewStringUTF("Unexpected error while validating the disc image.");
    }
}

extern "C" JNIEXPORT jstring JNICALL
Java_kz_nurasql77_urbanreign_LauncherActivity_nativeValidateIsoPath(
    JNIEnv *env, jobject object, jstring isoPath, jstring cacheDirectory)
{
    return Java_kz_nurasql77_urbanreign_MainActivity_nativeValidateIsoPath(
        env, object, isoPath, cacheDirectory);
}

#else // !__ANDROID__

namespace urban::android
{
    std::filesystem::path appStorageDirectory() { return std::filesystem::current_path(); }
    std::filesystem::path waitForIsoSelection() { return {}; }
    void logStage(const char *) {}
}

#endif
