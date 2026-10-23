package kz.nurasql77.urbanreign;

import android.content.Context;
import android.net.Uri;
import android.os.Environment;
import android.provider.DocumentsContract;

import java.io.File;
import java.io.FileInputStream;
import java.io.IOException;

/** Resolves standard shared-storage document-tree URIs to readable paths for native code. */
public final class FolderDiscPathResolver {
    private FolderDiscPathResolver() {}

    public static String resolve(Context context, Uri treeUri) throws IOException {
        try {
            String documentId = DocumentsContract.getTreeDocumentId(treeUri);
            int separator = documentId.indexOf(':');
            if (separator < 0) {
                throw new IOException("This folder provider does not expose a filesystem path. Move the extracted game folder to internal shared storage and select it there.");
            }

            String volume = documentId.substring(0, separator);
            String relativePath = documentId.substring(separator + 1);
            File volumeRoot = ("primary".equalsIgnoreCase(volume)
                    ? Environment.getExternalStorageDirectory()
                    : new File("/storage", volume)).getCanonicalFile();
            File folder = (relativePath.isEmpty() ? volumeRoot : new File(volumeRoot, relativePath)).getCanonicalFile();
            if (!folder.toPath().startsWith(volumeRoot.toPath())) {
                throw new IOException("The selected folder path is outside its storage volume.");
            }

            if (!folder.isDirectory()) {
                throw new IOException("Android could not resolve that folder as normal device storage. Move it to internal shared storage and select it again.");
            }

            File systemCnf = findChild(folder, "SYSTEM.CNF");
            File bootElf = findChild(folder, "SLUS_212.09");
            if (systemCnf == null || bootElf == null) {
                throw new IOException("The selected folder must directly contain SYSTEM.CNF and SLUS_212.09. Select the inner game folder, not its parent.");
            }

            try (FileInputStream cnf = new FileInputStream(systemCnf);
                 FileInputStream elf = new FileInputStream(bootElf)) {
                if (cnf.read() < 0 || elf.read() < 0) {
                    throw new IOException("SYSTEM.CNF or SLUS_212.09 is empty.");
                }
            }
            return folder.getAbsolutePath();
        } catch (SecurityException error) {
            throw new IOException("Android denied direct access to this folder. Allow file access or move the game folder to internal shared storage.", error);
        } catch (IllegalArgumentException error) {
            throw new IOException("Android returned an unsupported folder URI.", error);
        }
    }

    private static File findChild(File folder, String expectedName) {
        File[] children = folder.listFiles();
        if (children == null) return null;
        for (File child : children) {
            if (child.getName().equalsIgnoreCase(expectedName)) return child;
        }
        return null;
    }
}
