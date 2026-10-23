package kz.nurasql77.urbanreign;

import android.app.Activity;
import android.app.AlertDialog;
import android.Manifest;
import android.content.pm.PackageManager;
import android.content.Intent;
import android.database.Cursor;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import android.provider.DocumentsContract;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;

/**
 * Java-only launcher. The ISO is chosen and checked BEFORE NativeActivity starts,
 * so the NativeActivity/native startup thread cannot block the file picker.
 */
public final class LauncherActivity extends Activity {
    private static final int PICK_ISO_REQUEST = 7421;
    private static final int PICK_FOLDER_REQUEST = 7422;
    private static final int STORAGE_PERMISSION_REQUEST = 7423;
    private static final String PREFS = "urban_reign_touch";
    private static final long MIN_ISO_BYTES = 16L * 1024L * 1024L;

    private TextView status;
    private Button startButton;
    private Uri selectedUri;
    private Uri pendingFolderUri;
    private String selectedDiscPath;
    private boolean discIsFolder;
    private boolean validating;

    private void logStage(String message) {
        String line = "[Java][LauncherActivity] " + System.currentTimeMillis() + " " + message + "\n";
        try (FileOutputStream out = new FileOutputStream(new File(getFilesDir(), "urban_native.log"), true)) {
            out.write(line.getBytes(StandardCharsets.UTF_8));
            out.getFD().sync();
        } catch (Exception error) {
            android.util.Log.e("UrbanReignDiag", message, error);
        }
        android.util.Log.i("UrbanReignDiag", message);
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        logStage("LauncherActivity onCreate entered");
        android.content.SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        boolean savedFolder = prefs.getBoolean("disc_folder", false);
        String savedPath = prefs.getString("disc_path", null);
        if (savedFolder && savedPath != null && new File(savedPath).isDirectory()) {
            selectedDiscPath = savedPath;
            discIsFolder = true;
            String tree = prefs.getString("disc_uri", null);
            if (tree != null) selectedUri = Uri.parse(tree);
        } else {
            String saved = prefs.getString("disc_uri", prefs.getString("iso_uri", null));
            if (!savedFolder && saved != null) selectedUri = Uri.parse(saved);
        }
        setContentView(createMenu());
        refreshStatus();
        logStage("Launcher menu displayed");
    }

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    private LinearLayout createMenu() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER_VERTICAL);
        root.setPadding(dp(28), dp(28), dp(28), dp(28));
        root.setBackgroundColor(Color.rgb(15, 18, 24));

        TextView title = new TextView(this);
        title.setText("URBAN REIGN");
        title.setTextColor(Color.rgb(242, 184, 74));
        title.setTextSize(28);
        title.setGravity(Gravity.CENTER);
        title.setTypeface(android.graphics.Typeface.DEFAULT_BOLD);
        root.addView(title, new LinearLayout.LayoutParams(-1, -2));

        TextView subtitle = new TextView(this);
        subtitle.setText("Android port • ARM64");
        subtitle.setTextColor(Color.LTGRAY);
        subtitle.setTextSize(14);
        subtitle.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams subtitleParams = new LinearLayout.LayoutParams(-1, -2);
        subtitleParams.topMargin = dp(8);
        root.addView(subtitle, subtitleParams);

        status = new TextView(this);
        status.setTextColor(Color.WHITE);
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, dp(28), 0, dp(20));
        root.addView(status, new LinearLayout.LayoutParams(-1, -2));

        Button chooseButton = new Button(this);
        chooseButton.setText("Choose ISO or game folder");
        chooseButton.setOnClickListener(v -> launchPicker());
        root.addView(chooseButton, buttonParams());

        startButton = new Button(this);
        startButton.setText("Start game");
        startButton.setOnClickListener(v -> startGame());
        LinearLayout.LayoutParams startParams = buttonParams();
        startParams.topMargin = dp(8);
        root.addView(startButton, startParams);

        Button diagnosticsButton = new Button(this);
        diagnosticsButton.setText("Show diagnostic log");
        diagnosticsButton.setOnClickListener(v -> showDiagnostics());
        LinearLayout.LayoutParams diagnosticsParams = buttonParams();
        diagnosticsParams.topMargin = dp(8);
        root.addView(diagnosticsButton, diagnosticsParams);

        TextView note = new TextView(this);
        note.setText("Choose your own Urban Reign (USA) ISO or its extracted game folder. The folder must directly contain SYSTEM.CNF and SLUS_212.09. Keep the folder in internal shared storage so the native reader can access its files. If generated game code is missing, rebuild via Actions with boot_elf_url.");
        note.setTextColor(Color.rgb(185, 190, 200));
        note.setTextSize(12);
        note.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams noteParams = new LinearLayout.LayoutParams(-1, -2);
        noteParams.topMargin = dp(22);
        root.addView(note, noteParams);
        return root;
    }

    private LinearLayout.LayoutParams buttonParams() {
        return new LinearLayout.LayoutParams(-1, dp(54));
    }

    @Override
    protected void onResume() {
        super.onResume();
        String error = MainActivity.pendingLauncherError;
        if (error != null) {
            MainActivity.pendingLauncherError = null;
            validating = false;
            refreshStatus();
            showError("Game could not start", error);
        }
    }

    private void refreshStatus() {
        if (status == null) return;
        if (validating) {
            startButton.setEnabled(false);
            return;
        }
        if (discIsFolder && selectedDiscPath != null && new File(selectedDiscPath).isDirectory()) {
            status.setText("Selected game folder:\n" + selectedDiscPath + "\n\nPress Start game to verify the disc files and launch it.");
            startButton.setEnabled(true);
        } else if (!discIsFolder && selectedUri != null) {
            status.setText("Selected ISO:\n" + displayName(selectedUri) + "\n\nPress Start game to verify the disc and launch it.");
            startButton.setEnabled(true);
        } else {
            status.setText("No disc selected. Choose an ISO file or the extracted game folder.");
            startButton.setEnabled(false);
        }
    }

    private String displayName(Uri uri) {
        try (Cursor cursor = getContentResolver().query(uri, null, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                int column = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME);
                if (column >= 0) {
                    String name = cursor.getString(column);
                    if (name != null && !name.isEmpty()) return name;
                }
            }
        } catch (Exception ignored) {}
        return uri.getLastPathSegment() == null ? "Selected ISO" : uri.getLastPathSegment();
    }

    private void launchPicker() {
        new AlertDialog.Builder(this)
            .setTitle("Choose game source")
            .setItems(new String[]{"ISO file", "Extracted game folder"}, (dialog, which) -> {
                if (which == 0) launchIsoPicker();
                else launchFolderPicker();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void launchIsoPicker() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            startActivityForResult(Intent.createChooser(intent, "Select Urban Reign (USA) ISO"), PICK_ISO_REQUEST);
        } catch (RuntimeException error) {
            showError("File picker could not be opened", error.getMessage());
        }
    }

    private void launchFolderPicker() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT_TREE);
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION);
        try {
            startActivityForResult(Intent.createChooser(intent, "Select extracted Urban Reign game folder"), PICK_FOLDER_REQUEST);
        } catch (RuntimeException error) {
            showError("Folder picker could not be opened", error.getMessage());
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();

        if (requestCode == PICK_FOLDER_REQUEST) {
            int flags = data.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
            try {
                if (flags != 0) getContentResolver().takePersistableUriPermission(uri, flags);
            } catch (SecurityException ignored) {}
            pendingFolderUri = uri;
            if (checkSelfPermission(Manifest.permission.READ_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
                try {
                    requestPermissions(new String[]{Manifest.permission.READ_EXTERNAL_STORAGE}, STORAGE_PERMISSION_REQUEST);
                    return;
                } catch (RuntimeException ignored) {}
            }
            pendingFolderUri = null;
            prepareSelectedFolder(uri);
            return;
        }

        if (requestCode != PICK_ISO_REQUEST) return;
        int flags = data.getFlags() & (Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
        try {
            if (flags != 0) getContentResolver().takePersistableUriPermission(uri, flags);
        } catch (SecurityException ignored) {
            // A descriptor remains usable for this launch even if the grant is not persistent.
        }

        try (ParcelFileDescriptor fd = getContentResolver().openFileDescriptor(uri, "r")) {
            if (fd == null) throw new IOException("Android could not open the selected file.");
            if (fd.getStatSize() < MIN_ISO_BYTES) {
                throw new IOException("The file is too small or this provider does not expose a seekable local file. Download the ISO to device storage and select it again.");
            }
            selectedUri = uri;
            selectedDiscPath = null;
            discIsFolder = false;
            getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                .putString("disc_uri", uri.toString())
                .putString("iso_uri", uri.toString())
                .putBoolean("disc_folder", false)
                .remove("disc_path")
                .apply();
            refreshStatus();
            Toast.makeText(this, "ISO selected. Press Start game to verify it.", Toast.LENGTH_SHORT).show();
        } catch (Exception error) {
            showError("Cannot open this file", error.getMessage() == null ? error.toString() : error.getMessage());
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == STORAGE_PERMISSION_REQUEST && pendingFolderUri != null) {
            Uri uri = pendingFolderUri;
            pendingFolderUri = null;
            prepareSelectedFolder(uri);
        }
    }

    private void prepareSelectedFolder(Uri treeUri) {
        validating = true;
        status.setText("Checking the extracted game folder…");
        startButton.setEnabled(false);
        logStage("Selected extracted folder URI=" + treeUri);
        new Thread(() -> {
            String path = null;
            String issue = null;
            try {
                path = FolderDiscPathResolver.resolve(this, treeUri);
            } catch (Throwable error) {
                issue = error.getMessage() == null ? error.toString() : error.getMessage();
                logStage("Folder path resolution failed: " + android.util.Log.getStackTraceString(error));
            }
            final String resultPath = path;
            final String resultIssue = issue;
            runOnUiThread(() -> {
                validating = false;
                if (isFinishing()) return;
                if (resultPath == null) {
                    refreshStatus();
                    showError("Cannot use this game folder", resultIssue == null ? "Could not resolve the folder path." : resultIssue);
                    return;
                }
                selectedUri = treeUri;
                selectedDiscPath = resultPath;
                discIsFolder = true;
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .putString("disc_uri", treeUri.toString())
                    .putString("disc_path", resultPath)
                    .putBoolean("disc_folder", true)
                    .remove("iso_uri")
                    .apply();
                refreshStatus();
                Toast.makeText(this, "Game folder selected. Press Start game to verify it.", Toast.LENGTH_LONG).show();
                logStage("Selected extracted folder path=" + resultPath);
            });
        }, "urban-folder-prepare").start();
    }

    private void startGame() {
        final boolean folder = discIsFolder && selectedDiscPath != null;
        if ((!folder && selectedUri == null) || validating) {
            refreshStatus();
            return;
        }
        final Uri uri = selectedUri;
        final String folderPath = selectedDiscPath;
        logStage("Start game tapped; source=" + (folder ? folderPath : uri));
        validating = true;
        status.setText("Checking the Urban Reign disc…\nThe menu stays open while the native checker starts.");
        startButton.setEnabled(false);

        Thread checker = new Thread(() -> {
            String issue = null;
            try {
                logStage("Disc checker thread started");
                logStage("Before System.loadLibrary(urban) in launcher");
                System.loadLibrary("urban");
                logStage("System.loadLibrary(urban) succeeded in launcher");
                String cache = new File(getFilesDir(), "game_data").getAbsolutePath();
                String validation;
                if (folder) {
                    logStage("Before nativeValidateIsoPath for extracted folder");
                    validation = MainActivity.nativeValidateIsoPath(folderPath, cache);
                } else {
                    try (ParcelFileDescriptor fd = getContentResolver().openFileDescriptor(uri, "r")) {
                        if (fd == null) throw new IOException("Android could not reopen the selected ISO.");
                        if (fd.getStatSize() < MIN_ISO_BYTES) {
                            throw new IOException("The ISO is too small or is no longer accessible as a seekable local file.");
                        }
                        String path = "/proc/self/fd/" + fd.getFd();
                        logStage("Before nativeValidateIsoPath for ISO file");
                        validation = MainActivity.nativeValidateIsoPath(path, cache);
                    }
                }
                logStage("nativeValidateIsoPath returned; hasError=" + (validation != null && !validation.isEmpty()));
                if (validation != null && !validation.isEmpty()) issue = validation;
            } catch (Throwable error) {
                logStage("Disc checker failed: " + android.util.Log.getStackTraceString(error));
                issue = error.getMessage() == null ? error.toString() : error.getMessage();
            }

            final String validationIssue = issue;
            runOnUiThread(() -> {
                validating = false;
                if (isFinishing()) return;
                if (validationIssue != null && !validationIssue.isEmpty()) {
                    refreshStatus();
                    status.setText("Disc check failed. Choose the correct Urban Reign (USA) ISO or extracted folder.");
                    showError("Cannot use this disc", validationIssue);
                    return;
                }
                logStage("Disc validation passed; launching MainActivity");
                Intent intent = new Intent(LauncherActivity.this, MainActivity.class);
                if (folder) intent.putExtra("disc_path", folderPath);
                else intent.putExtra("iso_uri", uri.toString());
                startActivity(intent);
                logStage("startActivity(MainActivity) returned");
            });
        }, "urban-disc-check");
        checker.start();
    }

    private void showDiagnostics() {
        logStage("Diagnostic log dialog opened");
        File logFile = new File(getFilesDir(), "urban_native.log");
        String logText;
        try {
            if (!logFile.isFile() || logFile.length() == 0) {
                logText = "No native log has been saved yet.\n\n1. Press Start game.\n2. Wait for the black screen / Not responding message.\n3. Force-close the game, reopen Urban Reign, then tap Show diagnostic log.";
            } else {
                byte[] bytes = Files.readAllBytes(logFile.toPath());
                logText = new String(bytes, StandardCharsets.UTF_8);
                if (logText.length() > 14000) {
                    logText = "...(older log omitted)...\n" + logText.substring(logText.length() - 14000);
                }
            }
        } catch (Exception error) {
            logText = "Could not read native log: " + error;
        }

        TextView content = new TextView(this);
        content.setText(logText);
        // Use a light log panel so text remains visible with a light dialog theme.
        content.setTextColor(Color.BLACK);
        content.setTextSize(12);
        content.setTextIsSelectable(true);
        content.setBackgroundColor(Color.WHITE);
        content.setPadding(dp(12), dp(12), dp(12), dp(12));
        android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        scroll.setFillViewport(true);
        scroll.addView(content);
        new AlertDialog.Builder(this)
            .setTitle("Urban Reign diagnostic log")
            .setView(scroll)
            .setPositiveButton("Close", null)
            .setNeutralButton("Clear log", (dialog, which) -> {
                try {
                    Files.deleteIfExists(logFile.toPath());
                    Toast.makeText(this, "Log cleared.", Toast.LENGTH_SHORT).show();
                } catch (Exception error) {
                    showError("Could not clear log", error.toString());
                }
            })
            .show();
    }

    private void showError(String title, String message) {
        new AlertDialog.Builder(this)
            .setTitle(title)
            .setMessage(message == null ? "Unknown error." : message)
            .setPositiveButton("OK", null)
            .show();
    }
}
