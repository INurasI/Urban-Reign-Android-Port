#!/usr/bin/env python3
"""Static regression checks for the generated ISO9660 folder-disc reader.

These checks intentionally do not claim to execute the C++ reader. The Android
compile-only workflow separately compiles the patched native runtime.
"""
from pathlib import Path
import unittest

SUPPORT = Path(__file__).with_name("ps2_folder_disc_support.inc").read_text(
    encoding="utf-8"
)


class FolderDiscSupportTests(unittest.TestCase):
    def test_directory_path_table_order_is_breadth_first(self):
        start = SUPPORT.index(
            "void collectFolderDirectories(FolderIsoNode *node,"
        )
        end = SUPPORT.index(
            "\n    void collectFolderFiles(", start
        )
        function = SUPPORT[start:end]

        # A path table assigns the parent directory number before its children;
        # all siblings must therefore appear before grandchildren.
        self.assertIn("std::vector<FolderIsoNode *> pending{node};", function)
        self.assertIn(
            "for (size_t index = 0; index < pending.size(); ++index)",
            function,
        )
        self.assertIn("std::sort(childDirectories.begin()", function)
        self.assertIn("return a->isoName < b->isoName;", function)
        self.assertNotIn("collectFolderDirectories(child.get(), out)", function)

    def test_type_l_and_type_m_path_tables_use_correct_endianness(self):
        start = SUPPORT.index(
            "std::vector<uint8_t> serializeFolderPathTable("
        )
        end = SUPPORT.index(
            "\n    void appendFolderRecord(", start
        )
        function = SUPPORT[start:end]

        self.assertIn("putLittleEndian32(data.data() + offset + 2", function)
        self.assertIn("putBigEndian32(data.data() + offset + 2", function)
        self.assertIn("putLittleEndian16(data.data() + offset + 6", function)
        self.assertIn("putBigEndian16(data.data() + offset + 6", function)
        self.assertIn("numbers.find(directory->parent)", function)

    def test_pvd_advertises_both_path_tables_and_iso_version(self):
        self.assertIn("putBothEndian32(pvd + 132, pathTableSize);", SUPPORT)
        self.assertIn(
            "putLittleEndian32(pvd + 140, static_cast<uint32_t>(firstPathTableLba));",
            SUPPORT,
        )
        self.assertIn(
            "putBigEndian32(pvd + 148, static_cast<uint32_t>(secondPathTableLba));",
            SUPPORT,
        )
        self.assertIn("pvd[881] = 1;", SUPPORT)
        self.assertIn('std::memcpy(terminator + 1, "CD001", 5);', SUPPORT)

    def test_required_game_files_are_checked_at_disc_root(self):
        self.assertIn("if (!hasSystemCnf || !hasBootElf)", SUPPORT)
        self.assertIn('if (name == "SYSTEM.CNF")', SUPPORT)
        self.assertIn('if (name == "SLUS_212.09")', SUPPORT)

    def test_path_tables_are_rebuilt_after_directory_lbas_are_assigned(self):
        assigned = SUPPORT.index(
            "directory->lba = static_cast<uint32_t>(nextLba);"
        )
        rebuild = SUPPORT.index(
            "// Rebuild the path tables now that each directory has its final sector LBA."
        )
        write = SUPPORT.index(
            "std::copy(pathTableLittle.begin(), pathTableLittle.end()",
            rebuild,
        )
        self.assertLess(assigned, rebuild)
        self.assertLess(rebuild, write)


if __name__ == "__main__":
    unittest.main(verbosity=2)
