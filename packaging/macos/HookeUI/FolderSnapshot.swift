import Foundation
import Darwin

enum FolderSelectionError: LocalizedError {
    case refused(String)
    var errorDescription: String? { if case .refused(let message) = self { return message }; return nil }
}

final class FolderCaptureControl {
    private let lock = NSLock()
    private var cancelled = false
    private let seconds: TimeInterval
    private var touched = Date()
    init(seconds: TimeInterval = 30) { self.seconds = seconds }
    func progressed() { lock.lock(); touched = Date(); lock.unlock() }
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
    func check() throws {
        lock.lock(); let stopped = cancelled, idle = Date().timeIntervalSince(touched); lock.unlock()
        if stopped || idle >= seconds { throw FolderSelectionError.refused("Folder reading stopped. Choose the folder again to retry.") }
    }
}

/// Complete, bounded selected-folder bytes. No scientific format knowledge or host path leaves this owner.
final class FolderSnapshot {
    let name: String, count: Int, size: Int, bytes: Int
    let directory: URL
    private let handle: FileHandle
    private init(name: String, count: Int, size: Int, bytes: Int, directory: URL, handle: FileHandle) {
        self.name = name; self.count = count; self.size = size; self.bytes = bytes
        self.directory = directory; self.handle = handle
    }
    deinit { try? handle.close(); try? FileManager.default.removeItem(at: directory) }
    func read(offset: Int) throws -> Data? {
        guard offset >= 0, offset < bytes else { throw Self.fail("Folder read offset is outside its retained snapshot.") }
        try handle.seek(toOffset: UInt64(offset))
        let data = try handle.read(upToCount: 1_048_576) ?? Data()
        return data.isEmpty ? nil : data
    }
    private struct Entry { let path: String; let size: Int; let identity: stat }
    private static func fail(_ message: String) -> FolderSelectionError { .refused(message) }
    private static func identity(_ descriptor: Int32) throws -> stat {
        var value = stat()
        guard fstat(descriptor, &value) == 0 else { throw fail("A selected file could not be inspected. Choose the folder again.") }
        return value
    }
    private static func same(_ a: stat, _ b: stat) -> Bool {
        a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size
        && a.st_mtimespec.tv_sec == b.st_mtimespec.tv_sec && a.st_mtimespec.tv_nsec == b.st_mtimespec.tv_nsec
        && a.st_ctimespec.tv_sec == b.st_ctimespec.tv_sec && a.st_ctimespec.tv_nsec == b.st_ctimespec.tv_nsec
    }
    private static func child(_ parent: Int32, _ name: String) throws -> Int32 {
        let descriptor = openat(parent, name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { throw fail("A selected entry is unreadable or is a link. Choose a folder containing regular files only.") }
        return descriptor
    }
    private static func walk(_ descriptor: Int32, prefix: String, entries: inout [Entry], directories: inout [Entry], total: inout Int, visited: inout Int, control: FolderCaptureControl, device: dev_t) throws {
        try control.check()
        let original = try identity(descriptor)
        directories.append(Entry(path: prefix, size: 0, identity: original))
        let copied = dup(descriptor)
        guard copied >= 0 else { throw fail("The selected folder could not be read.") }
        guard let stream = fdopendir(copied) else { close(copied); throw fail("The selected folder could not be read.") }
        defer { closedir(stream) }
        while true {
            try control.check()
            errno = 0
            guard let item = readdir(stream) else {
                guard errno == 0 else { throw fail("The selected folder changed or could not be read completely.") }
                break
            }
            let name = withUnsafePointer(to: &item.pointee.d_name) { pointer in
                pointer.withMemoryRebound(to: CChar.self, capacity: Int(item.pointee.d_namlen) + 1) { String(validatingUTF8: $0) }
            }
            guard let name else { throw fail("A selected name is not valid Unicode.") }
            if name == "." || name == ".." { continue }
            control.progressed()
            visited += 1
            let path = prefix.isEmpty ? name : prefix + "/" + name
            guard visited <= 8192, path.utf8.count <= 512,
                  !path.contains("\\"), !path.contains(":"), !path.unicodeScalars.contains(where: { $0.value < 32 || $0.value == 127 })
            else { throw fail("This folder has too many entries or unsupported names.") }
            let opened = try child(descriptor, name)
            defer { close(opened) }
            let info = try identity(opened)
            guard info.st_dev == device else { throw fail("Choose a folder on one local volume. Mounted volumes must be imported separately.") }
            if info.st_mode & S_IFMT == S_IFDIR {
                try walk(opened, prefix: path, entries: &entries, directories: &directories, total: &total, visited: &visited, control: control, device: device)
            } else {
                guard info.st_mode & S_IFMT == S_IFREG else { throw fail("Selected folders must contain regular files only.") }
                guard info.st_size >= 0, info.st_size <= 64 * 1024 * 1024 * 1024, entries.count < 4096 else { throw fail("Folder import supports up to 4,096 files and 64 GiB.") }
                total += Int(info.st_size)
                guard total <= 64 * 1024 * 1024 * 1024 else { throw fail("Folder import supports up to 64 GiB.") }
                entries.append(Entry(path: path, size: Int(info.st_size), identity: info))
            }
        }
        guard same(original, try identity(descriptor)) else { throw fail("Folder contents changed. Choose the folder again.") }
    }
    private static func openEntry(_ root: Int32, path: String) throws -> Int32 {
        var current = dup(root)
        guard current >= 0 else { throw fail("The selected folder is no longer available.") }
        do {
            for part in path.split(separator: "/") {
                let next = try child(current, String(part)); close(current); current = next
            }
            return current
        } catch { close(current); throw error }
    }
    static func capture(_ source: URL, control: FolderCaptureControl = FolderCaptureControl(),
                        afterEnumeration: (() throws -> Void)? = nil) throws -> FolderSnapshot {
        try control.check()
        guard try source.resourceValues(forKeys: [.volumeIsLocalKey]).volumeIsLocal == true else {
            throw fail("This folder importer supports local storage. Remote storage requires a remote reader.")
        }
        let root = open(source.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard root >= 0 else { throw fail("Choose a readable folder, not a link.") }
        defer { close(root) }
        let device = try identity(root).st_dev
        var entries = [Entry](), directories = [Entry](), total = 0, visited = 0
        try walk(root, prefix: "", entries: &entries, directories: &directories, total: &total, visited: &visited, control: control, device: device)
        guard !entries.isEmpty else { throw fail("Choose a folder containing at least one file.") }
        try afterEnumeration?()
        try control.check()
        entries.sort { $0.path < $1.path }
        var offset = 0
        let records: [[String: Any]] = entries.map { entry in
            defer { offset += entry.size }
            return ["path": entry.path, "offset": offset, "size": entry.size]
        }
        let index = try JSONSerialization.data(withJSONObject: ["format": "source-directory-v1", "entries": records], options: [.sortedKeys])
        guard index.count <= 1_048_576 else { throw fail("This folder has too much entry metadata.") }
        let fm = FileManager.default
        let space = try fm.attributesOfFileSystem(forPath: fm.temporaryDirectory.path)
        guard let free = space[.systemFreeSize] as? NSNumber, free.int64Value >= Int64(total) + 2 * 1024 * 1024 * 1024 else {
            throw fail("Free disk space before importing this folder; the selected files are unchanged.")
        }
        let directory = fm.temporaryDirectory.appendingPathComponent("Hooke-folder-" + UUID().uuidString)
        try fm.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        var retained = false
        defer { if !retained { try? fm.removeItem(at: directory) } }
        let target = directory.appendingPathComponent("selection")
        guard fm.createFile(atPath: target.path, contents: nil, attributes: [.posixPermissions: 0o600]) else { throw fail("Folder staging could not be created.") }
        let output = try FileHandle(forWritingTo: target)
        defer { try? output.close() }
        var length = UInt32(index.count).bigEndian
        try withUnsafeBytes(of: &length) { try output.write(contentsOf: Data($0)) }
        try output.write(contentsOf: index)
        for entry in entries {
            try control.check()
            let descriptor = try openEntry(root, path: entry.path)
            let input = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
            defer { try? input.close() }
            guard same(entry.identity, try identity(descriptor)) else { throw fail("A selected file changed. Choose the folder again.") }
            var remaining = entry.size
            while remaining > 0 {
                try control.check()
                let data = try input.read(upToCount: min(1_048_576, remaining)) ?? Data()
                guard !data.isEmpty else { throw fail("A selected file changed while being read. Choose the folder again.") }
                try output.write(contentsOf: data); remaining -= data.count; control.progressed()
            }
            guard same(entry.identity, try identity(descriptor)) else { throw fail("A selected file changed while being read. Choose the folder again.") }
        }
        for entry in directories {
            try control.check()
            let descriptor = try openEntry(root, path: entry.path)
            defer { close(descriptor) }
            guard same(entry.identity, try identity(descriptor)) else { throw fail("Folder contents changed while being read. Choose the folder again.") }
        }
        try control.check()
        try output.synchronize()
        let input = try FileHandle(forReadingFrom: target)
        retained = true
        return FolderSnapshot(name: source.lastPathComponent, count: entries.count, size: total,
                              bytes: 4 + index.count + total, directory: directory, handle: input)
    }
}
