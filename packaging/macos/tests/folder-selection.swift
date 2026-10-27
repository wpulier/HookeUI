import Foundation
import Darwin

@main struct FolderSelectionTests {
    static func main() throws {
        let fm = FileManager.default
        let root = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try fm.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: root) }
        let source = root.appendingPathComponent("selected.zarr")
        try fm.createDirectory(at: source.appendingPathComponent(".nested"), withIntermediateDirectories: true)
        try Data("metadata".utf8).write(to: source.appendingPathComponent(".zattrs"))
        try Data([0, 1, 255]).write(to: source.appendingPathComponent(".nested/chunk"))
        var snapshot: FolderSnapshot? = try FolderSnapshot.capture(source)
        let temporary = snapshot!.directory
        assert(snapshot!.name == "selected.zarr" && snapshot!.count == 2 && snapshot!.size == 11)
        // The chosen source can change after capture; transferred bytes stay exact.
        try Data("changed".utf8).write(to: source.appendingPathComponent(".zattrs"))
        var body = Data()
        while body.count < snapshot!.bytes {
            let offset = body.count, chunk = try snapshot!.read(offset: body.count)!
            let repeated = try snapshot!.read(offset: offset)
            assert(chunk.count <= 1_048_576 && chunk == repeated)
            body.append(chunk)
        }
        assert(body.count == snapshot!.bytes)
        let indexSize = body.prefix(4).reduce(0) { ($0 << 8) | Int($1) }
        let index = try JSONSerialization.jsonObject(with: body.subdata(in: 4..<(4 + indexSize))) as! [String: Any]
        let entries = index["entries"] as! [[String: Any]]
        assert(index["format"] as? String == "source-directory-v1")
        let expected: [String: Data] = [".zattrs": Data("metadata".utf8), ".nested/chunk": Data([0,1,255])]
        for entry in entries {
            let offset = 4 + indexSize + (entry["offset"] as! Int), size = entry["size"] as! Int
            assert(body.subdata(in: offset..<(offset + size)) == expected[entry["path"] as! String])
        }
        snapshot = nil
        assert(!fm.fileExists(atPath: temporary.path))
        do {
            _ = try FolderSnapshot.capture(source, afterEnumeration: {
                try Data("late".utf8).write(to: source.appendingPathComponent(".late"))
            })
            fatalError("changed directory membership accepted")
        } catch is FolderSelectionError {}
        let cancelled = FolderCaptureControl()
        do { _ = try FolderSnapshot.capture(source, control: cancelled, afterEnumeration: { cancelled.cancel() }); fatalError("cancelled capture accepted") } catch is FolderSelectionError {}
        do { _ = try FolderSnapshot.capture(source, control: FolderCaptureControl(seconds: -1)); fatalError("expired capture accepted") } catch is FolderSelectionError {}
        try fm.createSymbolicLink(at: source.appendingPathComponent("escape"), withDestinationURL: root)
        do { _ = try FolderSnapshot.capture(source); fatalError("symlink accepted") } catch is FolderSelectionError {}
        try fm.removeItem(at: source.appendingPathComponent("escape"))
        let huge = source.appendingPathComponent("huge")
        fm.createFile(atPath: huge.path, contents: nil)
        let handle = try FileHandle(forWritingTo: huge)
        try handle.truncate(atOffset: 64 * 1024 * 1024 * 1024 + 1); try handle.close()
        do { _ = try FolderSnapshot.capture(source); fatalError("oversized folder accepted") } catch is FolderSelectionError {}
        try fm.removeItem(at: huge)
        let fifo = source.appendingPathComponent("pipe")
        assert(mkfifo(fifo.path, 0o600) == 0)
        do { _ = try FolderSnapshot.capture(source); fatalError("special file accepted") } catch is FolderSelectionError {}
        print("Folder selection: hidden/nested bytes, immutable snapshot, cleanup, symlink, size and special-file checks passed")
    }
}
