package kz.nurasql77.urbanreign;

import android.app.AlertDialog;
import android.app.NativeActivity;
import android.content.Intent;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.system.Os;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.SeekBar;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

public final class MainActivity extends NativeActivity {
    private static final int PICK_ISO_REQUEST = 7421;
    private static final String PREFS = "urban_reign_touch";
    private static final long MIN_ISO_BYTES = 16L * 1024L * 1024L;
    private String appFilesPath = "";
    private ParcelFileDescriptor selectedIsoFd;
    private TouchOverlay touchOverlay;
    private final ArrayList<ControlView> controls = new ArrayList<>();
    private TouchStick stick;
    private TextView gear;
    private boolean editMode;
    private float controlScale = 1.0f;
    private float controlAlpha = 0.76f;

    /** Set when MainActivity cannot start; LauncherActivity shows it instead of a frozen black screen. */
    static volatile String pendingLauncherError;

    private volatile boolean watchdogStopped;
    private volatile long lastUiTick;
    private Thread watchdog;

    private void logStage(String message) {
        String line = "[Java][MainActivity] " + System.currentTimeMillis() + " " + message + "\n";
        try (FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "urban_native.log"), true)) {
            out.write(line.getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        } catch (Exception error) {
            android.util.Log.e("UrbanReignDiag", message, error);
        }
        android.util.Log.i("UrbanReignDiag", message);
    }

    private native void nativeSetAppStoragePath(String path);
    private native void nativeSetIsoPath(String path);
    private native void nativeDumpThreads();
    static native String nativeValidateIsoPath(String path, String cacheDirectory);
    private native void nativeTouchButton(int button, boolean pressed);
    private native void nativeTouchAxis(int axis, float value);

    @Override
    protected void onCreate(Bundle state) {
        logStage("MainActivity onCreate entered");
        applyRuntimeEnvironment();
        Throwable startupError = null;
        boolean libraryLoaded = false;
        // Prepare both native rendezvous values BEFORE NativeActivity starts its native thread.
        // Otherwise native startup can wait for Java while Java is still blocked in super.onCreate().
        try {
            logStage("Before System.loadLibrary(urban)");
            System.loadLibrary("urban");
            libraryLoaded = true;
            logStage("System.loadLibrary(urban) succeeded");
            appFilesPath = getFilesDir().getAbsolutePath();
            nativeSetAppStoragePath(appFilesPath);
            logStage("nativeSetAppStoragePath returned");

            String folderPath = getIntent().getStringExtra("disc_path");
            if (folderPath != null && !folderPath.isEmpty()) {
                File folder = new File(folderPath);
                if (!folder.isDirectory()) {
                    throw new IOException("The selected game folder is no longer accessible. Return to the launcher and select it again.");
                }
                // PS2CdImage presents this extracted directory as a virtual ISO9660 disc.
                nativeSetIsoPath(folder.getAbsolutePath());
                logStage("nativeSetIsoPath received extracted game folder");
            } else {
                String uriText = getIntent().getStringExtra("iso_uri");
                if (uriText == null || uriText.isEmpty()) {
                    throw new IOException("No ISO or extracted game folder was passed from the launcher. Return and choose the disc source.");
                }
                Uri uri = Uri.parse(uriText);
                selectedIsoFd = getContentResolver().openFileDescriptor(uri, "r");
                if (selectedIsoFd == null) {
                    throw new IOException("Android could not reopen the selected ISO. Return to the launcher and choose it again.");
                }
                if (selectedIsoFd.getStatSize() < MIN_ISO_BYTES) {
                    throw new IOException("The selected ISO is no longer accessible as a seekable local file. Download it to device storage and select it again.");
                }
                // Keep this descriptor open for the entire native game session; no ISO copy is made.
                nativeSetIsoPath("/proc/self/fd/" + selectedIsoFd.getFd());
                logStage("nativeSetIsoPath received ISO file descriptor");
            }
        } catch (Throwable error) {
            logStage("Startup preparation failed: " + android.util.Log.getStackTraceString(error));
            startupError = error;
            // BUGFIX: the native game thread blocks until Java answers. If we only show an error here,
            // that thread never reaches its event loop, NativeActivity blocks the Android UI thread in
            // onStart/onResume, and the user sees a black screen + "not responding". Always answer.
            if (libraryLoaded) {
                try { nativeSetIsoPath(""); } catch (Throwable ignored) {}
            }
        }

        try {
            logStage("Before NativeActivity super.onCreate");
            super.onCreate(state);
            logStage("NativeActivity super.onCreate returned");
        } catch (Throwable error) {
            logStage("NativeActivity super.onCreate threw: " + android.util.Log.getStackTraceString(error));
            if (selectedIsoFd != null) {
                try { selectedIsoFd.close(); } catch (IOException ignored) {}
                selectedIsoFd = null;
            }
            showStartupError("Native startup failed", error);
            return;
        }

        if (startupError != null) {
            logStage("Returning to launcher with startup error");
            String message = startupError.getMessage() == null ? startupError.toString() : startupError.getMessage();
            pendingLauncherError = "Could not open selected disc\n\n" + message;
            finish();
            return;
        }

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        hideSystemBars();
        android.content.SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        controlScale = prefs.getFloat("scale", 1.0f);
        controlAlpha = prefs.getFloat("alpha", 0.76f);
        installTouchControls();
        startWatchdog();
        Toast.makeText(
            this,
            "If you are at the black opening video, tap the on-screen START button to skip it.",
            Toast.LENGTH_LONG
        ).show();
        logStage("Touch controls installed; opening-video START hint shown; MainActivity onCreate completed");
    }

    // ---------------------------------------------------------------------------------------------
    // Diagnostics: if the screen stays black, this writes (to urban_native.log) where the Android UI
    // thread and every native game thread are stuck. Open "Show diagnostic log" in the launcher.
    // ---------------------------------------------------------------------------------------------
    private void startWatchdog() {
        watchdogStopped = false;
        lastUiTick = SystemClock.uptimeMillis();
        final Handler ui = new Handler(Looper.getMainLooper());
        ui.post(new Runnable() {
            @Override public void run() {
                lastUiTick = SystemClock.uptimeMillis();
                if (!watchdogStopped) ui.postDelayed(this, 500);
            }
        });
        final long startedAt = SystemClock.uptimeMillis();
        final long[] nativeDumpAt = {5000L, 12000L, 25000L, 50000L};
        watchdog = new Thread(() -> {
            int nextDump = 0;
            int stallReports = 0;
            while (!watchdogStopped) {
                try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
                long now = SystemClock.uptimeMillis();
                long stalled = now - lastUiTick;
                if (stalled > 2500 && stallReports < 3) {
                    stallReports++;
                    StringBuilder sb = new StringBuilder("UI THREAD STALLED for " + stalled + " ms. Main thread stack:\n");
                    for (StackTraceElement frame : Looper.getMainLooper().getThread().getStackTrace()) {
                        sb.append("    at ").append(frame).append('\n');
                    }
                    logStage(sb.toString());
                }
                if (nextDump < nativeDumpAt.length && now - startedAt >= nativeDumpAt[nextDump]) {
                    nextDump++;
                    logStage("Native thread dump #" + nextDump + " (" + (now - startedAt) + " ms after start); UI stalled=" + stalled + " ms");
                    try {
                        nativeDumpThreads();
                    } catch (Throwable error) {
                        logStage("nativeDumpThreads unavailable (old native library?): " + error);
                        nextDump = nativeDumpAt.length;
                    }
                }
                if (nextDump >= nativeDumpAt.length && stallReports >= 3) return;
            }
        }, "urban-watchdog");
        watchdog.setDaemon(true);
        watchdog.start();
    }

    @Override
    protected void onStart() {
        logStage("MainActivity onStart begin");
        super.onStart();
        logStage("MainActivity onStart returned");
    }

    @Override
    protected void onResume() {
        logStage("MainActivity onResume begin");
        super.onResume();
        logStage("MainActivity onResume returned");
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        logStage("MainActivity onWindowFocusChanged hasFocus=" + hasFocus);
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) hideSystemBars();
    }

    /**
     * ROOT-CAUSE FIX: the Windows urban.exe sets these runtime defaults in main(); the Android build never did.
     * Without PS2X_IOP_HLE_MCSERV=0 the HLE memory-card service is used, and (upstream docs) the game never gets
     * past memory-card init => no frame is ever drawn => black screen. Values already set by hand are kept.
     * URBAN_TRACE / URBAN_HANG_DUMP only add diagnostics to urban_native.log.
     */
    private void applyRuntimeEnvironment() {
        final String[][] defaults = {
            {"PS2X_IOP_HLE_MCSERV", "0"},
            {"PS2X_SIO2_RECV1", "2"},
            {"PS2X_EE_LOOP_CYCLES", "16"},
            // Android's weaker CPUs may not finish asynchronous VU/GIF work before the GS vblank timeout.
            // Keep the pipeline deterministic by default; PS2X_MTVU=1 remains an explicit opt-in.
            {"PS2X_MTVU", "0"},
            {"URBAN_TRACE", "BOOT,LOADER,FILE"},
            // Bounded first-400 GS SIGNAL/FINISH events, useful when EE waits for its render semaphore.
            {"PS2X_GS_SIGNAL_LOG", "1"},
            {"URBAN_HANG_DUMP", "12"},
            {"URBAN_HANG_DUMP_REPEAT", "30"},
        };
        for (String[] entry : defaults) {
            try {
                Os.setenv(entry[0], entry[1], false);
                // Verify the effective process value before loading the native library.
                // This is important for PS2X_MTVU: the runtime reads it once to decide
                // whether VU/GIF work runs asynchronously or on the game thread.
                String effective = Os.getenv(entry[0]);
                logStage("Runtime env " + entry[0] + "=" + effective);
                if (!entry[1].equals(effective)) {
                    logStage("Runtime env " + entry[0] + " retained an existing value; requested default was " + entry[1]);
                }
            } catch (Throwable error) {
                logStage("setenv/getenv " + entry[0] + " failed: " + error);
            }
        }
        logStage("Runtime environment defaults applied (MCSERV HLE off, SIO2 RECV1=2, EE loop cycles 16, synchronous VU/GIF)");
    }

    private void showStartupError(String title, Throwable error) {
        android.widget.LinearLayout layout = new android.widget.LinearLayout(this);
        layout.setOrientation(android.widget.LinearLayout.VERTICAL);
        layout.setPadding(dp(20), dp(24), dp(20), dp(20));
        layout.setBackgroundColor(Color.rgb(15, 18, 24));
        TextView heading = new TextView(this);
        heading.setText(title);
        heading.setTextColor(Color.rgb(255, 190, 75));
        heading.setTextSize(20);
        layout.addView(heading);
        TextView details = new TextView(this);
        details.setText(android.util.Log.getStackTraceString(error));
        details.setTextColor(Color.WHITE);
        details.setTextSize(13);
        layout.addView(details);
        try {
            setContentView(layout);
        } catch (Throwable ignored) {
            android.util.Log.e("UrbanReign", title, error);
        }
    }

    private void hideSystemBars() {
        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_FULLSCREEN
                | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }

    private void chooseExistingOrPick() {
        String savedUri = getSharedPreferences(PREFS, MODE_PRIVATE).getString("iso_uri", null);
        if (savedUri != null) {
            openSelectedIso(Uri.parse(savedUri), false);
        } else {
            launchPicker();
        }
    }

    private void launchPicker() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            startActivityForResult(Intent.createChooser(intent, "Select your Urban Reign (USA) ISO"), PICK_ISO_REQUEST);
        } catch (RuntimeException ex) {
            Toast.makeText(this, "Android's document picker could not be opened.", Toast.LENGTH_LONG).show();
            nativeSetIsoPath("");
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != PICK_ISO_REQUEST) return;
        if (resultCode != RESULT_OK || data == null || data.getData() == null) {
            Toast.makeText(this, "No disc image selected.", Toast.LENGTH_LONG).show();
            nativeSetIsoPath("");
            return;
        }

        Uri uri = data.getData();
        int grantFlags = data.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try {
            if (grantFlags != 0) getContentResolver().takePersistableUriPermission(uri, grantFlags);
        } catch (SecurityException ignored) {
            // Some document providers grant access for this session only; keep the open descriptor alive.
        }
        openSelectedIso(uri, true);
    }

    private void openSelectedIso(Uri uri, boolean remember) {
        ParcelFileDescriptor descriptor = null;
        try {
            descriptor = getContentResolver().openFileDescriptor(uri, "r");
            if (descriptor == null) throw new IOException("The selected file could not be opened.");
            long size = descriptor.getStatSize();
            if (size < MIN_ISO_BYTES) {
                throw new IOException("The file is too small or the provider does not expose a seekable local file. Download the ISO to device storage first.");
            }

            // Validate the ISO and exact Urban Reign USA boot executable before waking the native game thread
            // or remembering this URI. This prevents wrong-region/CHD/other files from making the app exit silently.
            String candidatePath = "/proc/self/fd/" + descriptor.getFd();
            String validationError = nativeValidateIsoPath(
                candidatePath, appFilesPath + File.separator + "game_data");
            if (validationError != null && !validationError.isEmpty()) {
                throw new IOException(validationError);
            }

            if (selectedIsoFd != null) {
                try { selectedIsoFd.close(); } catch (IOException ignored) {}
            }
            selectedIsoFd = descriptor;
            descriptor = null; // MainActivity owns it while the native runtime is using it.

            installTouchControls();
            // PS2CdImage uses fopen/fseeko; a procfs FD path avoids copying a DVD-sized ISO into app storage.
            nativeSetIsoPath("/proc/self/fd/" + selectedIsoFd.getFd());
            if (remember) {
                getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("iso_uri", uri.toString()).apply();
            }
            Toast.makeText(this, "Verified Urban Reign USA ISO opened without copying.", Toast.LENGTH_LONG).show();
        } catch (Exception ex) {
            if (descriptor != null) {
                try { descriptor.close(); } catch (IOException ignored) {}
            }
            // Do not keep a stale URI after validation/open failures: otherwise the next launch retries it forever.
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove("iso_uri").apply();
            String reason = ex.getMessage() == null ? "Unknown file access error" : ex.getMessage();
            new AlertDialog.Builder(this)
                .setTitle("Cannot use this ISO")
                .setMessage(reason + "\n\nChoose the uncompressed Urban Reign (USA) ISO containing the matching SLUS_212.09 executable.")
                .setPositiveButton("Choose ISO", (dialog, which) -> launchPicker())
                .setCancelable(false)
                .show();
        }
    }

    private void installTouchControls() {
        if (touchOverlay != null) return;
        touchOverlay = new TouchOverlay();
        FrameLayout.LayoutParams overlayParams = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT);
        addContentView(touchOverlay, overlayParams);
        touchOverlay.setFocusable(false);
        touchOverlay.setFocusableInTouchMode(false);
        // Keep the controls above ordinary Activity content while letting taps pass
        // through the empty overlay background to the native game surface.
        touchOverlay.setElevation(dp(24));
        touchOverlay.setTranslationZ(dp(24));
        touchOverlay.setClickable(false);
        touchOverlay.setBackgroundColor(Color.TRANSPARENT);

        // GLFW standard gamepad button indices used by the native compatibility shim.
        addButton("L2", -1, 4, -1.0f, 0.02f, 0.04f);
        addButton("L1", 4, -1, 0.0f, 0.13f, 0.04f);
        addButton("R1", 5, -1, 0.0f, 0.77f, 0.04f);
        addButton("R2", -1, 5, -1.0f, 0.88f, 0.04f);

        addButton("SELECT", 6, -1, 0.0f, 0.40f, 0.90f);
        addButton("START", 7, -1, 0.0f, 0.51f, 0.90f);

        addButton("△", 3, -1, 0.0f, 0.865f, 0.53f);
        addButton("□", 2, -1, 0.0f, 0.795f, 0.66f);
        addButton("○", 1, -1, 0.0f, 0.935f, 0.66f);
        addButton("×", 0, -1, 0.0f, 0.865f, 0.79f);

        stick = new TouchStick();
        int stickSize = dp(controlBaseSizeDp("stick") * controlScale);
        FrameLayout.LayoutParams stickParams = new FrameLayout.LayoutParams(stickSize, stickSize);
        stickParams.gravity = Gravity.TOP | Gravity.LEFT;
        touchOverlay.addView(stick, stickParams);
        controls.add(new ControlView("stick", stick));
        gear = new TextView(this);
        gear.setText("⚙");
        gear.setTextSize(20);
        gear.setGravity(Gravity.CENTER);
        gear.setTextColor(Color.WHITE);
        gear.setBackground(background());
        gear.setOnClickListener(v -> {
            editMode = !editMode;
            releaseAllControls();
            Toast.makeText(this, editMode ? "Touch edit mode: drag controls, tap the gear to finish." : "Touch controls locked.", Toast.LENGTH_SHORT).show();
            applyStyle();
        });
        gear.setOnLongClickListener(v -> {
            showTouchSettings();
            return true;
        });
        int gearSize = dp(controlBaseSizeDp("gear") * controlScale);
        FrameLayout.LayoutParams gearParams = new FrameLayout.LayoutParams(gearSize, gearSize);
        gearParams.gravity = Gravity.TOP | Gravity.LEFT;
        touchOverlay.addView(gear, gearParams);
        controls.add(new ControlView("gear", gear));
        touchOverlay.requestLayout();
        // addContentView() can attach/layout the parent before its children are added.
        // Post one explicit pass so control placement never depends only on the parent's
        // bounds changing (which may not happen again after the children are attached).
        touchOverlay.post(() -> {
            if (touchOverlay == null) return;
            touchOverlay.layoutControls();
            touchOverlay.invalidate();
            logStage("Touch overlay laid out: " + touchOverlay.getWidth() + "x" +
                touchOverlay.getHeight() + ", controls=" + controls.size());
        });
    }

    private int controlBaseSizeDp(String key) {
        if ("stick".equals(key)) return 170;
        if ("gear".equals(key)) return 42;
        if ("SELECT".equals(key) || "START".equals(key)) return 58;
        return 64;
    }

    private void addButton(String label, int buttonIndex, int axisIndex, float releasedAxis, float nx, float ny) {
        ControlView control = new ControlView(label, buttonIndex, axisIndex, releasedAxis);
        control.nx = nx;
        control.ny = ny;
        controls.add(control);
        int size = dp(controlBaseSizeDp(label) * controlScale);
        FrameLayout.LayoutParams params = new FrameLayout.LayoutParams(size, size);
        params.gravity = Gravity.TOP | Gravity.LEFT;
        touchOverlay.addView(control.view, params);
    }

    private GradientDrawable background() {
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(Color.argb(178, 12, 17, 26));
        bg.setStroke(dp(2), Color.rgb(232, 171, 61));
        bg.setCornerRadius(dp(17));
        return bg;
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private void applyStyle() {
        for (ControlView c : controls) {
            c.view.setAlpha(editMode ? Math.min(1.0f, controlAlpha + 0.20f) : controlAlpha);
            // The joystick draws its own circular background in onDraw(); only label
            // buttons and the gear should receive the rounded-rectangle drawable.
            if (c.view instanceof TextView) c.view.setBackground(background());
        }
        if (touchOverlay != null) {
            // Recompute child sizes as well as alpha so the size slider actually takes effect.
            touchOverlay.layoutControls();
            touchOverlay.requestLayout();
        }
    }

    private void showTouchSettings() {
        android.widget.LinearLayout panel = new android.widget.LinearLayout(this);
        panel.setOrientation(android.widget.LinearLayout.VERTICAL);
        panel.setPadding(dp(20), dp(12), dp(20), dp(4));

        TextView sizeLabel = new TextView(this);
        sizeLabel.setText("Control size");
        panel.addView(sizeLabel);
        SeekBar size = new SeekBar(this);
        size.setMax(100);
        size.setProgress(Math.round((controlScale - 0.65f) / 0.95f * 100));
        panel.addView(size);

        TextView opacityLabel = new TextView(this);
        opacityLabel.setText("Control opacity");
        panel.addView(opacityLabel);
        SeekBar opacity = new SeekBar(this);
        opacity.setMax(100);
        opacity.setProgress(Math.round((controlAlpha - 0.20f) / 0.75f * 100));
        panel.addView(opacity);

        size.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                controlScale = 0.65f + (progress / 100.0f) * 0.95f;
                applyStyle();
            }
            @Override public void onStartTrackingTouch(SeekBar bar) {}
            @Override public void onStopTrackingTouch(SeekBar bar) {}
        });
        opacity.setOnSeekBarChangeListener(new SeekBar.OnSeekBarChangeListener() {
            @Override public void onProgressChanged(SeekBar bar, int progress, boolean fromUser) {
                controlAlpha = 0.20f + (progress / 100.0f) * 0.75f;
                applyStyle();
            }
            @Override public void onStartTrackingTouch(SeekBar bar) {}
            @Override public void onStopTrackingTouch(SeekBar bar) {}
        });

        new AlertDialog.Builder(this)
            .setTitle("Touch control settings")
            .setView(panel)
            .setPositiveButton("Save", (dialog, which) -> {
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .putFloat("scale", controlScale)
                    .putFloat("alpha", controlAlpha)
                    .apply();
                applyStyle();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void releaseAllControls() {
        for (ControlView c : controls) c.release();
        if (stick != null) stick.resetAxes();
    }

    private void savePosition(String key, float nx, float ny) {
        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
            .putFloat("x_" + key, nx)
            .putFloat("y_" + key, ny)
            .apply();
    }

    private float savedX(String key, float fallback) {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getFloat("x_" + key, fallback);
    }

    private float savedY(String key, float fallback) {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getFloat("y_" + key, fallback);
    }

    private void positionView(View view, String key, float defaultX, float defaultY, int baseDp) {
        if (touchOverlay == null || touchOverlay.getWidth() == 0 || touchOverlay.getHeight() == 0) return;
        int size = dp(baseDp * controlScale);
        int w = touchOverlay.getWidth();
        int h = touchOverlay.getHeight();
        float nx = Math.max(0f, Math.min(1f, savedX(key, defaultX)));
        float ny = Math.max(0f, Math.min(1f, savedY(key, defaultY)));
        FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) view.getLayoutParams();
        if (lp == null) lp = new FrameLayout.LayoutParams(size, size);
        int left = Math.max(0, Math.min(Math.max(0, w - size), Math.round(nx * Math.max(0, w - size))));
        int top = Math.max(0, Math.min(Math.max(0, h - size), Math.round(ny * Math.max(0, h - size))));
        boolean changed = lp.width != size || lp.height != size || lp.leftMargin != left || lp.topMargin != top || lp.gravity != (Gravity.TOP | Gravity.LEFT);
        if (changed) {
            lp.width = size;
            lp.height = size;
            lp.gravity = Gravity.TOP | Gravity.LEFT;
            lp.leftMargin = left;
            lp.topMargin = top;
            view.setLayoutParams(lp);
        }
    }

    private final class TouchOverlay extends FrameLayout {
        TouchOverlay() {
            super(MainActivity.this);
            setClipChildren(false);
            setClipToPadding(false);
            setFocusable(false);
            setFocusableInTouchMode(false);
        }

        @Override
        protected void onLayout(boolean changed, int left, int top, int right, int bottom) {
            super.onLayout(changed, left, top, right, bottom);
            // Recompute children on every layout, not only when this overlay's own bounds
            // change. addContentView() can finish the parent's layout before its children
            // are inserted, leaving default-sized children overlapping the full screen.
            layoutControls();
        }

        void layoutControls() {
            if (getWidth() <= 0 || getHeight() <= 0) return;
            for (ControlView c : controls) {
                int base = controlBaseSizeDp(c.key);
                if (editMode) {
                    // Editing can change the size slider; resize controls without resetting
                    // the user's current drag positions.
                    int size = dp(base * controlScale);
                    FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) c.view.getLayoutParams();
                    if (lp == null) {
                        lp = new FrameLayout.LayoutParams(size, size);
                        lp.gravity = Gravity.TOP | Gravity.LEFT;
                        c.view.setLayoutParams(lp);
                    } else if (lp.width != size || lp.height != size) {
                        lp.width = size;
                        lp.height = size;
                        c.view.setLayoutParams(lp);
                    }
                    continue;
                }
                float dx = c.nx;
                float dy = c.ny;
                if (c.key.equals("stick")) { dx = 0.03f; dy = 0.52f; }
                else if (c.key.equals("gear")) { dx = 0.48f; dy = 0.015f; }
                else if (c.key.equals("L2")) { dx = 0.02f; dy = 0.035f; }
                else if (c.key.equals("L1")) { dx = 0.13f; dy = 0.035f; }
                else if (c.key.equals("R1")) { dx = 0.77f; dy = 0.035f; }
                else if (c.key.equals("R2")) { dx = 0.88f; dy = 0.035f; }
                positionView(c.view, c.key, dx, dy, base);
            }
        }
    }

    private final class ControlView {
        final String key;
        final View view;
        final int buttonIndex;
        final int axisIndex;
        final float releasedAxis;
        float nx;
        float ny;
        float downX;
        float downY;
        int originalLeft;
        int originalTop;
        boolean pressed;

        ControlView(String key, View view) {
            this.key = key;
            this.view = view;
            this.buttonIndex = -1;
            this.axisIndex = -1;
            this.releasedAxis = -1f;
            setPositionDefaults();
            installDragHandler();
        }

        ControlView(String key, int buttonIndex, int axisIndex, float releasedAxis) {
            this.key = key;
            TextView b = new TextView(MainActivity.this);
            b.setText(key);
            b.setTextSize(key.length() > 3 ? 10 : 22);
            b.setTypeface(Typeface.create("sans-serif-condensed", Typeface.BOLD));
            b.setGravity(Gravity.CENTER);
            b.setTextColor(Color.WHITE);
            b.setBackground(background());
            b.setClickable(true);
            this.view = b;
            this.buttonIndex = buttonIndex;
            this.axisIndex = axisIndex;
            this.releasedAxis = releasedAxis;
            setPositionDefaults();
            b.setOnTouchListener((v, event) -> handleTouch(event));
            installDragHandler();
        }

        void setPositionDefaults() {
            if (key.equals("stick") || key.equals("gear")) return;
            if (key.equals("L2")) { nx = 0.02f; ny = 0.035f; }
            else if (key.equals("L1")) { nx = 0.13f; ny = 0.035f; }
            else if (key.equals("R1")) { nx = 0.77f; ny = 0.035f; }
            else if (key.equals("R2")) { nx = 0.88f; ny = 0.035f; }
            else if (key.equals("SELECT")) { nx = 0.40f; ny = 0.90f; }
            else if (key.equals("START")) { nx = 0.51f; ny = 0.90f; }
            else if (key.equals("△")) { nx = 0.865f; ny = 0.53f; }
            else if (key.equals("□")) { nx = 0.795f; ny = 0.66f; }
            else if (key.equals("○")) { nx = 0.935f; ny = 0.66f; }
            else if (key.equals("×")) { nx = 0.865f; ny = 0.79f; }
        }

        void installDragHandler() {
            if (view instanceof TextView && !key.equals("gear")) {
                view.setOnLongClickListener(v -> {
                    if (!editMode) {
                        editMode = true;
                        releaseAllControls();
                        applyStyle();
                        Toast.makeText(MainActivity.this, "Move controls, then tap the gear to lock them.", Toast.LENGTH_SHORT).show();
                    }
                    return true;
                });
            }
        }

        boolean handleTouch(MotionEvent event) {
            if (editMode) return handleDrag(event);
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                case MotionEvent.ACTION_POINTER_DOWN:
                    press();
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_POINTER_UP:
                case MotionEvent.ACTION_CANCEL:
                    release();
                    return true;
                default:
                    return true;
            }
        }

        boolean handleDrag(MotionEvent event) {
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) view.getLayoutParams();
            if (lp == null) return true;
            switch (event.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downX = event.getRawX();
                    downY = event.getRawY();
                    originalLeft = lp.leftMargin;
                    originalTop = lp.topMargin;
                    return true;
                case MotionEvent.ACTION_MOVE:
                    lp.leftMargin = Math.max(0, Math.min(touchOverlay.getWidth() - view.getWidth(),
                        originalLeft + Math.round(event.getRawX() - downX)));
                    lp.topMargin = Math.max(0, Math.min(touchOverlay.getHeight() - view.getHeight(),
                        originalTop + Math.round(event.getRawY() - downY)));
                    view.setLayoutParams(lp);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    float maxX = Math.max(1, touchOverlay.getWidth() - view.getWidth());
                    float maxY = Math.max(1, touchOverlay.getHeight() - view.getHeight());
                    nx = lp.leftMargin / maxX;
                    ny = lp.topMargin / maxY;
                    savePosition(key, nx, ny);
                    return true;
                default:
                    return true;
            }
        }

        void press() {
            pressed = true;
            if (buttonIndex >= 0) nativeTouchButton(buttonIndex, true);
            if (axisIndex >= 0) nativeTouchAxis(axisIndex, 1.0f);
            view.setAlpha(Math.min(1.0f, controlAlpha + 0.22f));
        }

        void release() {
            if (pressed && buttonIndex >= 0) nativeTouchButton(buttonIndex, false);
            if (axisIndex >= 0) nativeTouchAxis(axisIndex, releasedAxis);
            pressed = false;
            view.setAlpha(editMode ? Math.min(1.0f, controlAlpha + 0.20f) : controlAlpha);
        }
    }

    private final class TouchStick extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private float x;
        private float y;
        private float downRawX;
        private float downRawY;
        private int originalLeft;
        private int originalTop;

        TouchStick() {
            super(MainActivity.this);
            setLayerType(View.LAYER_TYPE_SOFTWARE, null);
            setOnTouchListener((v, e) -> {
                if (editMode) return handleStickDrag(e);
                switch (e.getActionMasked()) {
                    case MotionEvent.ACTION_DOWN:
                    case MotionEvent.ACTION_MOVE:
                        updateStick(e.getX(), e.getY());
                        invalidate();
                        return true;
                    case MotionEvent.ACTION_UP:
                    case MotionEvent.ACTION_CANCEL:
                        resetAxes();
                        invalidate();
                        return true;
                    default:
                        return true;
                }
            });
        }

        void updateStick(float px, float py) {
            float cx = getWidth() * 0.5f;
            float cy = getHeight() * 0.5f;
            float radius = Math.max(1f, Math.min(cx, cy) * 0.72f);
            float dx = (px - cx) / radius;
            float dy = (py - cy) / radius;
            float length = (float)Math.sqrt(dx * dx + dy * dy);
            if (length > 1f) { dx /= length; dy /= length; }
            if (Math.abs(dx) < 0.12f) dx = 0f;
            if (Math.abs(dy) < 0.12f) dy = 0f;
            x = dx; y = dy;
            nativeTouchAxis(0, dx);
            nativeTouchAxis(1, dy);
        }

        void resetAxes() {
            x = 0f; y = 0f;
            nativeTouchAxis(0, 0f);
            nativeTouchAxis(1, 0f);
        }

        boolean handleStickDrag(MotionEvent e) {
            FrameLayout.LayoutParams lp = (FrameLayout.LayoutParams) getLayoutParams();
            if (lp == null) return true;
            switch (e.getActionMasked()) {
                case MotionEvent.ACTION_DOWN:
                    downRawX = e.getRawX(); downRawY = e.getRawY();
                    originalLeft = lp.leftMargin; originalTop = lp.topMargin;
                    return true;
                case MotionEvent.ACTION_MOVE:
                    lp.leftMargin = Math.max(0, Math.min(touchOverlay.getWidth() - getWidth(), originalLeft + Math.round(e.getRawX() - downRawX)));
                    lp.topMargin = Math.max(0, Math.min(touchOverlay.getHeight() - getHeight(), originalTop + Math.round(e.getRawY() - downRawY)));
                    setLayoutParams(lp);
                    return true;
                case MotionEvent.ACTION_UP:
                case MotionEvent.ACTION_CANCEL:
                    float maxX = Math.max(1, touchOverlay.getWidth() - getWidth());
                    float maxY = Math.max(1, touchOverlay.getHeight() - getHeight());
                    savePosition("stick", lp.leftMargin / maxX, lp.topMargin / maxY);
                    return true;
                default: return true;
            }
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            float cx = getWidth() * 0.5f;
            float cy = getHeight() * 0.5f;
            float radius = Math.min(cx, cy) * 0.82f;
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(100, 8, 13, 22));
            canvas.drawCircle(cx, cy, radius, paint);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(dp(2));
            paint.setColor(Color.argb(215, 232, 171, 61));
            canvas.drawCircle(cx, cy, radius, paint);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(editMode ? 235 : 165, 232, 171, 61));
            canvas.drawCircle(cx + x * radius * 0.60f, cy + y * radius * 0.60f, radius * 0.36f, paint);
        }
    }

    @Override
    protected void onDestroy() {
        logStage("MainActivity onDestroy");
        watchdogStopped = true;
        releaseAllControls();
        if (selectedIsoFd != null) {
            try { selectedIsoFd.close(); } catch (IOException ignored) {}
            selectedIsoFd = null;
        }
        super.onDestroy();
    }
}
