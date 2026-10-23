// Android extracted-folder support: present a directory as a synthetic ISO9660 image.
// The image metadata is generated in memory; file payloads are read from the selected folder.
namespace
{
    constexpr uint64_t kFolderSectorSize = PS2CdImage::kSectorSize;

    struct FolderIsoNode
    {
        std::string isoName;
        std::filesystem::path sourcePath;
        FolderIsoNode *parent = nullptr;
        bool directory = false;
        uint64_t sourceSize = 0;
        uint32_t lba = 0;
        uint32_t dataSize = 0;
        std::vector<std::unique_ptr<FolderIsoNode>> children;
    };

    struct FolderIsoInput
    {
        std::filesystem::path relativePath;
        std::filesystem::path sourcePath;
        bool directory = false;
        uint64_t size = 0;
    };

    std::string folderIsoIdentifier(std::string name, bool directory)
    {
        std::string result;
        result.reserve(name.size() + 2);
        for (unsigned char c : name)
        {
            if (c >= 'a' && c <= 'z')
                c = static_cast<unsigned char>(c - 'a' + 'A');
            const bool allowed = (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                                 c == '_' || c == '.' || c == '-';
            result.push_back(allowed ? static_cast<char>(c) : '_');
        }
        while (!result.empty() && result.front() == '.')
        {
            // Leading dots are legal in ISO9660, but they often indicate host-side metadata
            // (for example .DS_Store); retain the name while avoiding an empty identifier.
            break;
        }
        if (result.empty() || result == "." || result == "..")
            result = "_";
        if (directory)
        {
            if (result.size() > 31)
                result.resize(31);
        }
        else
        {
            if (result.size() > 29)
                result.resize(29);
            result += ";1";
        }
        return result;
    }

    void collectFolderDirectories(FolderIsoNode *node, std::vector<FolderIsoNode *> &out)
    {
        if (!node || !node->directory)
            return;

        // ISO9660 path tables are ordered by parent directory number (breadth-first),
        // with each parent's child directories ordered by their on-disc identifiers.
        // A recursive depth-first walk puts a grandchild before its parent's siblings
        // and can make valid nested-folder images unreadable to stricter ISO readers.
        std::vector<FolderIsoNode *> pending{node};
        for (size_t index = 0; index < pending.size(); ++index)
        {
            FolderIsoNode *current = pending[index];
            out.push_back(current);

            std::vector<FolderIsoNode *> childDirectories;
            for (const auto &child : current->children)
                if (child->directory)
                    childDirectories.push_back(child.get());
            std::sort(childDirectories.begin(), childDirectories.end(),
                      [](const FolderIsoNode *a, const FolderIsoNode *b)
                      {
                          return a->isoName < b->isoName;
                      });
            pending.insert(pending.end(), childDirectories.begin(), childDirectories.end());
        }
    }

    void collectFolderFiles(FolderIsoNode *node, std::vector<FolderIsoNode *> &out)
    {
        for (auto &child : node->children)
        {
            if (child->directory)
                collectFolderFiles(child.get(), out);
            else
                out.push_back(child.get());
        }
    }

    void putBothEndian32(uint8_t *destination, uint32_t value)
    {
        destination[0] = static_cast<uint8_t>(value);
        destination[1] = static_cast<uint8_t>(value >> 8);
        destination[2] = static_cast<uint8_t>(value >> 16);
        destination[3] = static_cast<uint8_t>(value >> 24);
        destination[4] = static_cast<uint8_t>(value >> 24);
        destination[5] = static_cast<uint8_t>(value >> 16);
        destination[6] = static_cast<uint8_t>(value >> 8);
        destination[7] = static_cast<uint8_t>(value);
    }

    void putBothEndian16(uint8_t *destination, uint16_t value)
    {
        destination[0] = static_cast<uint8_t>(value);
        destination[1] = static_cast<uint8_t>(value >> 8);
        destination[2] = static_cast<uint8_t>(value >> 8);
        destination[3] = static_cast<uint8_t>(value);
    }

    std::vector<uint8_t> makeFolderDirectoryRecord(
        const std::vector<uint8_t> &identifier, uint32_t lba, uint32_t size, bool directory)
    {
        const size_t identifierLength = identifier.size();
        const size_t recordLength = 33 + identifierLength + ((identifierLength % 2 == 0) ? 1 : 0);
        if (recordLength > 255)
            return {};
        std::vector<uint8_t> record(recordLength, 0);
        record[0] = static_cast<uint8_t>(recordLength);
        putBothEndian32(record.data() + 2, lba);
        putBothEndian32(record.data() + 10, size);
        record[18] = 126; // 2026, years since 1900
        record[19] = 1;
        record[20] = 1;
        record[25] = directory ? 2 : 0;
        putBothEndian16(record.data() + 28, 1);
        record[32] = static_cast<uint8_t>(identifierLength);
        std::copy(identifier.begin(), identifier.end(), record.begin() + 33);
        return record;
    }

    void putLittleEndian16(uint8_t *destination, uint16_t value)
    {
        destination[0] = static_cast<uint8_t>(value);
        destination[1] = static_cast<uint8_t>(value >> 8);
    }

    void putBigEndian16(uint8_t *destination, uint16_t value)
    {
        destination[0] = static_cast<uint8_t>(value >> 8);
        destination[1] = static_cast<uint8_t>(value);
    }

    void putLittleEndian32(uint8_t *destination, uint32_t value)
    {
        destination[0] = static_cast<uint8_t>(value);
        destination[1] = static_cast<uint8_t>(value >> 8);
        destination[2] = static_cast<uint8_t>(value >> 16);
        destination[3] = static_cast<uint8_t>(value >> 24);
    }

    void putBigEndian32(uint8_t *destination, uint32_t value)
    {
        destination[0] = static_cast<uint8_t>(value >> 24);
        destination[1] = static_cast<uint8_t>(value >> 16);
        destination[2] = static_cast<uint8_t>(value >> 8);
        destination[3] = static_cast<uint8_t>(value);
    }

    // ISO9660 requires Type-L (little-endian) and Type-M (big-endian) path tables.
    // Parent directory numbers are 1-based indexes into the same ordered directory list.
    std::vector<uint8_t> serializeFolderPathTable(
        const std::vector<FolderIsoNode *> &directories, bool bigEndian)
    {
        if (directories.empty() || directories.size() > UINT16_MAX)
            return {};

        std::map<const FolderIsoNode *, uint16_t> numbers;
        for (size_t i = 0; i < directories.size(); ++i)
            numbers.emplace(directories[i], static_cast<uint16_t>(i + 1));

        std::vector<uint8_t> data;
        for (const FolderIsoNode *directory : directories)
        {
            std::vector<uint8_t> identifier;
            if (!directory->parent)
                identifier.push_back(0); // ISO9660 root directory identifier
            else
                identifier.assign(directory->isoName.begin(), directory->isoName.end());

            if (identifier.empty() || identifier.size() > UINT8_MAX)
                return {};
            const size_t recordSize = 8 + identifier.size() + (identifier.size() % 2);
            const size_t offset = data.size();
            data.resize(offset + recordSize, 0);
            data[offset] = static_cast<uint8_t>(identifier.size());
            data[offset + 1] = 0; // extended-attribute record length

            if (bigEndian)
            {
                putBigEndian32(data.data() + offset + 2, directory->lba);
                const auto parent = directory->parent ? numbers.find(directory->parent) : numbers.find(directory);
                if (parent == numbers.end())
                    return {};
                putBigEndian16(data.data() + offset + 6, parent->second);
            }
            else
            {
                putLittleEndian32(data.data() + offset + 2, directory->lba);
                const auto parent = directory->parent ? numbers.find(directory->parent) : numbers.find(directory);
                if (parent == numbers.end())
                    return {};
                putLittleEndian16(data.data() + offset + 6, parent->second);
            }
            std::copy(identifier.begin(), identifier.end(), data.begin() + offset + 8);
        }
        return data;
    }

    void appendFolderRecord(std::vector<uint8_t> &data, const std::vector<uint8_t> &record)
    {
        const size_t withinSector = data.size() % kFolderSectorSize;
        if (withinSector + record.size() > kFolderSectorSize)
            data.resize(data.size() + (kFolderSectorSize - withinSector), 0);
        data.insert(data.end(), record.begin(), record.end());
    }

    std::vector<uint8_t> serializeFolderDirectory(FolderIsoNode *node)
    {
        std::vector<uint8_t> data;
        FolderIsoNode *parent = node->parent ? node->parent : node;
        appendFolderRecord(data, makeFolderDirectoryRecord({0}, node->lba, node->dataSize, true));
        appendFolderRecord(data, makeFolderDirectoryRecord({1}, parent->lba, parent->dataSize, true));

        std::vector<FolderIsoNode *> children;
        children.reserve(node->children.size());
        for (auto &child : node->children)
            children.push_back(child.get());
        std::sort(children.begin(), children.end(), [](const FolderIsoNode *a, const FolderIsoNode *b)
        {
            return a->isoName < b->isoName;
        });

        for (const FolderIsoNode *child : children)
        {
            const std::vector<uint8_t> id(child->isoName.begin(), child->isoName.end());
            const uint32_t childSize = child->directory ? child->dataSize : static_cast<uint32_t>(child->sourceSize);
            appendFolderRecord(data, makeFolderDirectoryRecord(id, child->lba, childSize, child->directory));
        }

        const size_t remainder = data.size() % kFolderSectorSize;
        if (remainder != 0)
            data.resize(data.size() + (kFolderSectorSize - remainder), 0);
        return data;
    }

    bool buildSyntheticFolderImage(
        const std::filesystem::path &folder,
        std::vector<uint8_t> &prefix,
        std::map<uint64_t, PS2CdImageFileExtent> &fileExtents,
        uint64_t &totalSize)
    {
        std::error_code ec;
        if (!std::filesystem::is_directory(folder, ec) || ec)
            return false;

        std::vector<FolderIsoInput> inputs;
        for (std::filesystem::recursive_directory_iterator it(
                 folder, std::filesystem::directory_options::skip_permission_denied, ec), end;
             it != end; it.increment(ec))
        {
            if (ec)
            {
                ec.clear();
                continue;
            }
            const auto status = it->symlink_status(ec);
            if (ec)
            {
                ec.clear();
                continue;
            }
            if (std::filesystem::is_symlink(status))
            {
                if (std::filesystem::is_directory(status))
                    it.disable_recursion_pending();
                continue;
            }
            const bool isDirectory = std::filesystem::is_directory(status);
            const bool isFile = std::filesystem::is_regular_file(status);
            if (!isDirectory && !isFile)
                continue;

            FolderIsoInput input;
            input.sourcePath = it->path();
            input.relativePath = std::filesystem::relative(it->path(), folder, ec);
            if (ec || input.relativePath.empty())
            {
                ec.clear();
                return false;
            }
            input.directory = isDirectory;
            if (isFile)
            {
                input.size = std::filesystem::file_size(it->path(), ec);
                if (ec || input.size > UINT32_MAX)
                {
                    ec.clear();
                    return false;
                }
            }
            inputs.push_back(std::move(input));
        }

        std::sort(inputs.begin(), inputs.end(), [](const FolderIsoInput &a, const FolderIsoInput &b)
        {
            const auto da = std::distance(a.relativePath.begin(), a.relativePath.end());
            const auto db = std::distance(b.relativePath.begin(), b.relativePath.end());
            if (da != db)
                return da < db;
            return a.relativePath.generic_string() < b.relativePath.generic_string();
        });

        FolderIsoNode root;
        root.directory = true;
        root.sourcePath = folder;
        std::map<std::string, FolderIsoNode *> directories;
        directories.emplace("", &root);

        for (const FolderIsoInput &input : inputs)
        {
            const std::string relative = input.relativePath.generic_string();
            const std::string parentKey = input.relativePath.parent_path().generic_string();
            auto parentIt = directories.find(parentKey);
            if (parentIt == directories.end())
                return false;

            auto node = std::make_unique<FolderIsoNode>();
            node->sourcePath = input.sourcePath;
            node->directory = input.directory;
            node->sourceSize = input.size;
            node->parent = parentIt->second;
            node->isoName = folderIsoIdentifier(input.relativePath.filename().string(), input.directory);
            FolderIsoNode *raw = node.get();
            parentIt->second->children.push_back(std::move(node));
            if (input.directory)
                directories[relative] = raw;
        }

        // ISO9660 names must be unique within a directory after case folding/sanitizing.
        std::vector<FolderIsoNode *> allDirectories;
        collectFolderDirectories(&root, allDirectories);
        for (FolderIsoNode *directory : allDirectories)
        {
            std::set<std::string> names;
            for (const auto &child : directory->children)
                if (!names.insert(child->isoName).second)
                    return false;
        }

        bool hasSystemCnf = false;
        bool hasBootElf = false;
        for (const auto &child : root.children)
        {
            std::string name = child->isoName;
            const size_t semicolon = name.rfind(';');
            if (semicolon != std::string::npos)
                name.resize(semicolon);
            if (name == "SYSTEM.CNF")
                hasSystemCnf = true;
            if (name == "SLUS_212.09")
                hasBootElf = true;
        }
        if (!hasSystemCnf || !hasBootElf)
            return false;

        // A standards-compliant ISO9660 PVD must expose both mandatory path tables.
        // Their sizes depend on directory names, not LBAs, so they can be laid out first.
        if (allDirectories.size() > UINT16_MAX)
            return false;
        std::vector<uint8_t> pathTableLittle = serializeFolderPathTable(allDirectories, false);
        std::vector<uint8_t> pathTableBig = serializeFolderPathTable(allDirectories, true);
        if (pathTableLittle.empty() || pathTableBig.size() != pathTableLittle.size() ||
            pathTableLittle.size() > UINT32_MAX)
            return false;

        const uint32_t pathTableSize = static_cast<uint32_t>(pathTableLittle.size());
        const uint64_t pathTableSectors = (pathTableSize + kFolderSectorSize - 1) / kFolderSectorSize;
        constexpr uint64_t firstPathTableLba = 18; // 0-15 system area; PVD=16; terminator=17.
        const uint64_t secondPathTableLba = firstPathTableLba + pathTableSectors;
        uint64_t nextLba = secondPathTableLba + pathTableSectors;

        // Place directory extents after the two path tables so every LBA is final and stable.
        for (FolderIsoNode *directory : allDirectories)
        {
            if (nextLba > UINT32_MAX)
                return false;
            directory->lba = static_cast<uint32_t>(nextLba);
            std::vector<uint8_t> estimate = serializeFolderDirectory(directory);
            if (estimate.empty() || estimate.size() > (64u << 20) || estimate.size() % kFolderSectorSize != 0)
                return false;
            directory->dataSize = static_cast<uint32_t>(estimate.size());
            nextLba += estimate.size() / kFolderSectorSize;
        }

        std::vector<FolderIsoNode *> files;
        collectFolderFiles(&root, files);
        std::sort(files.begin(), files.end(), [](const FolderIsoNode *a, const FolderIsoNode *b)
        {
            return a->sourcePath.generic_string() < b->sourcePath.generic_string();
        });
        fileExtents.clear();
        for (FolderIsoNode *file : files)
        {
            if (nextLba > UINT32_MAX)
                return false;
            file->lba = static_cast<uint32_t>(nextLba);
            const uint64_t sectors = std::max<uint64_t>(1, (file->sourceSize + kFolderSectorSize - 1) / kFolderSectorSize);
            const uint64_t allocated = sectors * kFolderSectorSize;
            const uint64_t start = nextLba * kFolderSectorSize;
            fileExtents.emplace(start, PS2CdImageFileExtent{file->sourcePath, file->sourceSize, allocated});
            nextLba += sectors;
        }
        if (nextLba > UINT32_MAX || nextLba * kFolderSectorSize > std::numeric_limits<size_t>::max())
            return false;

        const uint64_t prefixBytes = (files.empty() ? nextLba : files.front()->lba) * kFolderSectorSize;
        prefix.assign(static_cast<size_t>(prefixBytes), 0);

        // Rebuild the path tables now that each directory has its final sector LBA.
        pathTableLittle = serializeFolderPathTable(allDirectories, false);
        pathTableBig = serializeFolderPathTable(allDirectories, true);
        if (pathTableLittle.size() != pathTableSize || pathTableBig.size() != pathTableSize)
            return false;

        const size_t pathLittleOffset = static_cast<size_t>(firstPathTableLba * kFolderSectorSize);
        const size_t pathBigOffset = static_cast<size_t>(secondPathTableLba * kFolderSectorSize);
        if (pathLittleOffset > prefix.size() || pathTableLittle.size() > prefix.size() - pathLittleOffset ||
            pathBigOffset > prefix.size() || pathTableBig.size() > prefix.size() - pathBigOffset)
            return false;
        std::copy(pathTableLittle.begin(), pathTableLittle.end(), prefix.begin() + pathLittleOffset);
        std::copy(pathTableBig.begin(), pathTableBig.end(), prefix.begin() + pathBigOffset);

        // Re-serialize directories now that every extent and size is known.
        for (FolderIsoNode *directory : allDirectories)
        {
            std::vector<uint8_t> bytes = serializeFolderDirectory(directory);
            if (bytes.size() != directory->dataSize)
                return false;
            const size_t offset = static_cast<size_t>(directory->lba) * kFolderSectorSize;
            if (offset > prefix.size() || bytes.size() > prefix.size() - offset)
                return false;
            std::copy(bytes.begin(), bytes.end(), prefix.begin() + offset);
        }

        const uint32_t volumeSectors = static_cast<uint32_t>(nextLba);
        uint8_t *pvd = prefix.data() + 16u * kFolderSectorSize;
        pvd[0] = 1;
        std::memcpy(pvd + 1, "CD001", 5);
        pvd[6] = 1;
        // ISO9660 fixed-width identifiers are space-padded, not NUL-padded.
        std::memset(pvd + 8, ' ', 32);
        constexpr char kSystemIdentifier[] = "URBAN REIGN EXTRACTED FOLDER";
        std::memcpy(pvd + 8, kSystemIdentifier, sizeof(kSystemIdentifier) - 1);
        std::memset(pvd + 40, ' ', 32);
        constexpr char kVolumeIdentifier[] = "URBAN_REIGN_FOLDER";
        std::memcpy(pvd + 40, kVolumeIdentifier, sizeof(kVolumeIdentifier) - 1);
        putBothEndian32(pvd + 80, volumeSectors);
        putBothEndian16(pvd + 120, 1);
        putBothEndian16(pvd + 124, 1);
        pvd[128] = static_cast<uint8_t>(kFolderSectorSize);
        pvd[129] = static_cast<uint8_t>(kFolderSectorSize >> 8);
        pvd[130] = static_cast<uint8_t>(kFolderSectorSize >> 8);
        pvd[131] = static_cast<uint8_t>(kFolderSectorSize);
        putBothEndian32(pvd + 132, pathTableSize);
        putLittleEndian32(pvd + 140, static_cast<uint32_t>(firstPathTableLba));
        putBigEndian32(pvd + 148, static_cast<uint32_t>(secondPathTableLba));
        pvd[881] = 1; // ISO9660 file structure version

        const auto rootRecord = makeFolderDirectoryRecord({0}, root.lba, root.dataSize, true);
        if (rootRecord.size() != 34)
            return false;
        std::copy(rootRecord.begin(), rootRecord.end(), pvd + 156);

        uint8_t *terminator = prefix.data() + 17u * kFolderSectorSize;
        terminator[0] = 255;
        std::memcpy(terminator + 1, "CD001", 5);
        terminator[6] = 1;

        totalSize = nextLba * kFolderSectorSize;
        return !fileExtents.empty();
    }
}

bool PS2CdImage::readFolderUnlocked(uint64_t offset, void *destination, size_t size) const
{
    if ((!destination && size != 0) || size == 0)
        return size == 0;

    auto *out = static_cast<uint8_t *>(destination);
    while (size != 0)
    {
        if (offset < m_virtualPrefix.size())
        {
            const size_t count = static_cast<size_t>(std::min<uint64_t>(size, m_virtualPrefix.size() - offset));
            std::memcpy(out, m_virtualPrefix.data() + static_cast<size_t>(offset), count);
            out += count;
            offset += count;
            size -= count;
            continue;
        }

        auto it = m_virtualFiles.upper_bound(offset);
        if (it == m_virtualFiles.begin())
            return false;
        --it;
        const uint64_t start = it->first;
        const PS2CdImageFileExtent &extent = it->second;
        if (offset < start || offset - start >= extent.allocatedBytes)
            return false;

        const uint64_t within = offset - start;
        const size_t count = static_cast<size_t>(std::min<uint64_t>(size, extent.allocatedBytes - within));
        if (within >= extent.sizeBytes)
        {
            std::memset(out, 0, count);
        }
        else
        {
            const size_t dataCount = static_cast<size_t>(std::min<uint64_t>(count, extent.sizeBytes - within));
            std::FILE *file = std::fopen(extent.path.string().c_str(), "rb");
            if (!file)
                return false;
            const bool seekOk = seekFile(file, within);
            const size_t readCount = seekOk ? std::fread(out, 1u, dataCount, file) : 0u;
            std::fclose(file);
            if (!seekOk || readCount != dataCount)
                return false;
            if (dataCount < count)
                std::memset(out + dataCount, 0, count - dataCount);
        }
        out += count;
        offset += count;
        size -= count;
    }
    return true;
}
