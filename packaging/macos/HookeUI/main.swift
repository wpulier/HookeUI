// HookeUI: the Mac app's launcher (packaging/macos/README.md).
//
// The bundle carries the app's source snapshot with its studio and capture app prebuilt, uv and FFmpeg. On launch it
// copies the snapshot to ~/Library/Application Support/HookeUI/runtime/<build>/ (once per build), builds its Python
// there with uv from the hashed lockfile (first launch only), starts what `make start` starts (scripts/studio.sh, in
// its packaged mode) and shows the studio in its own window. The user's data stays in ~/MicroscopeMind, as for
// `make start`. A Beta (packaging/macos/channels.json) keeps all of that in folders, ports and a Keychain service of its
// own, so it never shares or overwrites Stable's. Quitting stops everything it started. Sparkle checks the appcast (SUFeedURL) automatically; Sparkle asks before downloading and lets the user choose when to restart.
import AppKit
import CryptoKit
import Darwin
import Sparkle
import WebKit

let fm = FileManager.default

// MARK: - Where things are

/// This copy's release channel, from its Info.plist (channels.py writes it from channels.json). Without the keys (a build
/// before channels) it is Stable, as every copy was.
struct Channel {
    private let info = Bundle.main.infoDictionary ?? [:]
    var id: String { info["HookeUIChannel"] as? String ?? "stable" }
    var name: String { info["CFBundleName"] as? String ?? "HookeUI" }
    var supportFolder: String { info["HookeUISupportFolder"] as? String ?? "HookeUI" }
    var dataFolder: String { info["HookeUIDataFolder"] as? String ?? "MicroscopeMind" }
    var keychainService: String { info["HookeUIKeychainService"] as? String ?? "MicroscopeMind" }
    var orchestratorPort: Int { info["HookeUIOrchestratorPort"] as? Int ?? 8770 }
    var cameraPort: Int { info["HookeUICameraPort"] as? Int ?? 8765 }
    var streamPort: Int { info["HookeUIStreamPort"] as? Int ?? 9710 }
    var settingsPort: Int { info["HookeUISettingsPort"] as? Int ?? 8767 }
    /// Beta: API keys, model downloads and caches of its own too, not the Mac-wide ones Stable reads.
    var isolated: Bool { info["HookeUIIsolated"] as? Bool ?? false }
}

let channel = Channel()
let appName = channel.name

struct Places {
    let bundle = Bundle.main.bundleURL
    var resources: URL { bundle.appendingPathComponent("Contents/Resources") }
    var helpers: URL { bundle.appendingPathComponent("Contents/Helpers") }
    var payload: URL { resources.appendingPathComponent("app") }
    let support = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/\(channel.supportFolder)")
    let logs = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/\(channel.supportFolder)")
    let data = fm.homeDirectoryForCurrentUser.appendingPathComponent(channel.dataFolder)
    var bin: URL { support.appendingPathComponent("bin") }
    var runtimes: URL { support.appendingPathComponent("runtime") }
    var envs: URL { support.appendingPathComponent("envs") }  // one Python per lockfile, shared by versions
    var log: URL { logs.appendingPathComponent("HookeUI.log") }
}

let places = Places()

final class Log {
    static let shared = Log()
    private let queue = DispatchQueue(label: "log")
    private var handle: FileHandle?

    func open() {
        try? fm.createDirectory(at: places.logs, withIntermediateDirectories: true)
        if !fm.fileExists(atPath: places.log.path) { fm.createFile(atPath: places.log.path, contents: nil) }
        handle = try? FileHandle(forWritingTo: places.log)
        _ = try? handle?.seekToEnd()
    }

    func line(_ text: String) {
        let stamp = ISO8601DateFormatter().string(from: Date())
        queue.async { self.handle?.write(Data("[\(stamp)] \(text)\n".utf8)) }
    }

    func raw(_ data: Data) { queue.async { self.handle?.write(data) } }
}

struct Failure: Error { let message: String }

extension Bundle {
    var shortVersion: String { object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "?" }
}

// MARK: - Running a command

/// Runs a command to its end, its output lines given to `line` as they come and kept in the log.
@discardableResult
func run(_ executable: String, _ arguments: [String], cwd: URL? = nil, env: [String: String],
         line: ((String) -> Void)? = nil) throws -> Int32 {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = arguments
    process.environment = env
    if let cwd { process.currentDirectoryURL = cwd }
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    var pending = Data()
    pipe.fileHandleForReading.readabilityHandler = { handle in
        let chunk = handle.availableData
        guard !chunk.isEmpty else { return }
        Log.shared.raw(chunk)
        pending.append(chunk)
        while let nl = pending.firstIndex(where: { $0 == 10 || $0 == 13 }) {
            let text = String(decoding: pending[..<nl], as: UTF8.self)
            pending.removeSubrange(...nl)
            if !text.isEmpty { line?(text) }
        }
    }
    try process.run()
    process.waitUntilExit()
    pipe.fileHandleForReading.readabilityHandler = nil
    return process.terminationStatus
}

func portIsFree(_ port: Int) -> Bool {
    let fd = socket(AF_INET, SOCK_STREAM, 0)
    guard fd >= 0 else { return false }
    defer { close(fd) }
    // As the servers bind (SO_REUSEADDR): a port whose last connections are closing (TIME_WAIT) is still free.
    var on: Int32 = 1
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &on, socklen_t(MemoryLayout<Int32>.size))
    var addr = sockaddr_in()
    addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
    addr.sin_family = sa_family_t(AF_INET)
    addr.sin_port = in_port_t(UInt16(port).bigEndian)
    addr.sin_addr.s_addr = inet_addr("127.0.0.1")
    let bound = withUnsafePointer(to: &addr) {
        $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(fd, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
    }
    return bound == 0
}

/// The preferred port when it is free, else the first free one after it.
func choosePort(_ preferred: Int, avoiding taken: Set<Int>) -> Int {
    var port = preferred
    while port < preferred + 200 {
        if !taken.contains(port) && portIsFree(port) { return port }
        port += 1
    }
    return preferred
}

func sha256(_ url: URL) -> String {
    guard let data = try? Data(contentsOf: url) else { return "" }
    return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

// MARK: - The services

/// Sets up the runtime and runs scripts/studio.sh; says each step through `say` (title, detail) and, when a step ends,
/// whether it ran through `finished` (the launch screen ticks only those: SetupWords.swift).
final class Services {
    var say: (String, String, SetupPhase) -> Void = { _, _, _ in }
    var finished: (SetupPhase, Bool) -> Void = { _, _ in }
    /// This launch's plan (plan()), which the steps' words follow.
    private(set) var planned: SetupPlan?
    /// The app's own Python as this build measured it (packaged.json runtimeDownload), for the first-launch question.
    private(set) var runtimeDownload: RuntimeDownload?
    var studio: Process?
    var runtime: URL?
    var studioURL: URL?
    var cameraPort = channel.cameraPort
    /// What this start removed or kept of earlier versions' copies, said after an update (UpdateWords.afterUpdate).
    var removedRuntimeBytes: Int64 = 0
    var removedPythonBytes: Int64 = 0
    var keptPython = false
    private var environment: [String: String] = [:]

    func buildID() throws -> String {
        let manifest = places.payload.appendingPathComponent("packaged.json")
        guard let data = try? Data(contentsOf: manifest),
              let packaged = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let version = packaged["version"] as? String, let content = packaged["contentHash"] as? String
        else { throw Failure(message: "This copy of \(appName) is incomplete (packaged.json). Download it again.") }
        return "\(version)-\(content.prefix(12))"
    }

    // What each step checks before it runs, shared by the steps and by plan(), so the screen plans exactly what runs.
    static func runtimeInstalled(_ target: URL) -> Bool { fm.fileExists(atPath: target.appendingPathComponent(".complete").path) }
    static func earlierRuntime(besides id: String) -> Bool {
        ((try? fm.contentsOfDirectory(atPath: places.runtimes.path)) ?? []).contains { !$0.hasPrefix(".") && $0 != id }
    }
    /// The Python of a lockfile (its hash): one folder of envs/ per lockfile.
    static func python(_ lock: String) -> URL { places.envs.appendingPathComponent(String(lock.prefix(16))) }
    static func pythonReady(_ venv: URL, lock: String) -> Bool {
        (try? String(contentsOf: venv.appendingPathComponent(".hookeui-lock"), encoding: .utf8)) == lock
            && fm.isExecutableFile(atPath: venv.appendingPathComponent("bin/python").path)
    }
    static func warmed(_ venv: URL, lock: String) -> Bool {
        (try? String(contentsOf: venv.appendingPathComponent(".hookeui-warm"), encoding: .utf8)) == lock
    }

    /// What this launch has to do, read before anything is shown. It reads the bundle's own lockfile and envs/ directly:
    /// on an update the new runtime folder (and its services/.venv link) does not exist yet.
    func plan() throws -> SetupPlan {
        let id = try buildID()
        runtimeDownload = (try? Data(contentsOf: places.payload.appendingPathComponent("packaged.json"))).flatMap(RuntimeDownload.read)
        let lock = sha256(places.payload.appendingPathComponent("services/uv.lock"))
        let venv = Services.python(lock)
        let others = ((try? fm.contentsOfDirectory(at: places.envs, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent != venv.lastPathComponent }
        let facts = SetupFacts(runtimeInstalled: Services.runtimeInstalled(places.runtimes.appendingPathComponent(id)),
                               earlierRuntime: Services.earlierRuntime(besides: id),
                               pythonReady: Services.pythonReady(venv, lock: lock),
                               otherPython: others.contains { other in
                                   (try? String(contentsOf: other.appendingPathComponent(".hookeui-lock"), encoding: .utf8))
                                       .map { Services.pythonReady(other, lock: $0) } ?? false },
                               warmed: Services.warmed(venv, lock: lock))
        let plan = SetupPlan(facts)
        planned = plan
        Log.shared.line("plan: \(plan.stages.map(\.rawValue).joined(separator: ", "))\(plan.update ? " (update)" : "")\(plan.asksFirst ? " (asks first)" : "")")
        return plan
    }

    /// The snapshot copied out of the bundle once per build, so the app can write beside its code as a checkout does.
    /// Returns whether it copied (false: this build's copy was there).
    func installRuntime(_ id: String) throws -> (URL, ran: Bool) {
        let target = places.runtimes.appendingPathComponent(id)
        if Services.runtimeInstalled(target) {
            let bundled = try Data(contentsOf: places.payload.appendingPathComponent("packaged.json"))
            let installed = try Data(contentsOf: target.appendingPathComponent("packaged.json"))
            guard bundled == installed else {
                throw Failure(message: "The cached app runtime does not match this update. Your Projects are unchanged. Reinstall this version or contact support.")
            }
            return (target, false)
        }
        if let plan = planned {
            let (heading, text) = SetupWords.setUp(plan, product: SetupWords.product(appName), version: Bundle.main.shortVersion)
            say(heading, text, .prepare)
        }
        try fm.createDirectory(at: places.runtimes, withIntermediateDirectories: true)
        let partial = places.runtimes.appendingPathComponent(".\(id).partial")
        try? fm.removeItem(at: partial)
        let base = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
        guard try run("/usr/bin/ditto", [places.payload.path, partial.path], env: base) == 0 else {
            throw Failure(message: "Could not copy the app's files into Application Support.")
        }
        let capture = places.helpers.appendingPathComponent("MicroscopeMind Camera.app")
        let build = partial.appendingPathComponent("experiments/iphone-camera-feed/capture-app/build")
        try fm.createDirectory(at: build, withIntermediateDirectories: true)
        guard try run("/usr/bin/ditto", [capture.path, build.appendingPathComponent("MicroscopeMind Camera.app").path],
                      env: base) == 0 else { throw Failure(message: "Could not copy the camera helper.") }
        try? fm.removeItem(at: target)
        try fm.moveItem(at: partial, to: target)
        fm.createFile(atPath: target.appendingPathComponent(".complete").path, contents: Data(id.utf8))
        return (target, true)
    }

    /// Every /usr/bin program that is only macOS's stub for the developer tools: the files identical to /usr/bin/git
    /// on a clean macOS 26.6.2 (VM, 2026-10-19), and xcrun. Eight were guarded before; uv's first sync ran
    /// install_name_tool on its Python and the install dialog opened behind the setup screen.
    let developerToolStubs = [
        "DeRez", "GetFileInfo", "ResMerger", "Rez", "SetFile", "SplitForks", "ar", "as", "asa", "bison", "bm4", "c++",
        "c++filt", "c89", "c99", "cc", "clang", "clang++", "clangd", "cmpdylib", "codesign_allocate", "cpp", "ctags",
        "ctf_insert", "dsymutil", "dwarfdump", "dyld_info", "flex", "flex++", "g++", "gatherheaderdoc", "gcc", "gcov",
        "git", "git-receive-pack", "git-shell", "git-upload-archive", "git-upload-pack", "gm4", "gnumake", "gperf",
        "hdxml2manxml", "headerdoc2html", "indent", "install_name_tool", "ld", "lex", "libtool", "lipo", "lldb",
        "llvm-g++", "llvm-gcc", "lorder", "m4", "make", "mig", "nm", "nmedit", "objdump", "otool", "pagestuff", "pip3",
        "python3", "ranlib", "resolveLinks", "rpcgen", "segedit", "size", "sourcekit-lsp", "strings", "strip", "swift",
        "swiftc", "unifdef", "unifdefall", "vtool", "xcrun", "xml2man", "yacc",
    ]

    /// uv, FFmpeg and guards for the developer tools: on a Mac without them, running one opens macOS's
    /// "install the command line developer tools" dialog. A guard says it is missing instead.
    func makeBin() throws {
        try? fm.removeItem(at: places.bin)
        try fm.createDirectory(at: places.bin, withIntermediateDirectories: true)
        for tool in ["uv", "ffmpeg", "ffprobe"] {
            try fm.createSymbolicLink(at: places.bin.appendingPathComponent(tool),
                                      withDestinationURL: places.helpers.appendingPathComponent(tool))
        }
        let guardScript = """
        #!/bin/sh
        # HookeUI: run the developer tool only when it is installed; never open macOS's install dialog.
        name="$(basename "$0")"
        if /usr/bin/xcode-select -p >/dev/null 2>&1; then exec "/usr/bin/$name" "$@"; fi
        echo "$name isn't installed on this Mac (xcode-select --install)" >&2
        exit 127

        """
        for tool in developerToolStubs {
            let url = places.bin.appendingPathComponent(tool)
            try Data(guardScript.utf8).write(to: url)
            try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: url.path)
        }
    }

    func baseEnvironment() -> [String: String] {
        let inherited = ProcessInfo.processInfo.environment
        var env: [String: String] = [:]
        for key in ["HOME", "USER", "LOGNAME", "TMPDIR", "SHELL"] { env[key] = inherited[key] }
        env["LANG"] = "en_US.UTF-8"
        env["PATH"] = "\(places.bin.path):/usr/bin:/bin:/usr/sbin:/sbin"
        let uv = places.support.appendingPathComponent("uv")
        env["UV_CACHE_DIR"] = uv.appendingPathComponent("cache").path
        env["UV_PYTHON_INSTALL_DIR"] = uv.appendingPathComponent("python").path
        env["UV_PYTHON_PREFERENCE"] = "only-managed"  // never probe /usr/bin/python3 (the install-tools dialog)
        env["UV_PYTHON_DOWNLOADS"] = "automatic"
        env["UV_NO_CONFIG"] = "1"
        env["MICROMIND_SAVES"] = places.data.path  // the three homes in ~/MicroscopeMind (docs/saves.md)
        env["MICROMIND_DATASETS_DIR"] = places.data.appendingPathComponent("datasets").path
        // The assets store (footage, camera source, catalog, its environments and tools, launch settings) and the
        // Keychain service: Stable's are the defaults; a Beta's are its own (channels.json).
        env["MICROMIND_ASSETS"] = places.data.path
        env["MICROMIND_KEYCHAIN_SERVICE"] = channel.keychainService
        env["MICROMIND_BLACKMAGIC_PORT"] = String(channel.streamPort)
        env["MICROMIND_CAMERA_SETTINGS_PORT"] = String(channel.settingsPort)
        if channel.isolated {
            // Not the Mac-wide API keys file (~/Desktop/micromind.txt) Stable reads: a Beta's keys are added in it, or
            // put deliberately in its own folder. Model downloads and caches are its own, so removing one in Beta
            // never removes Stable's.
            env["MICROMIND_KEYS_FILE"] = places.data.appendingPathComponent("micromind.txt").path
            let caches = places.support.appendingPathComponent("caches")
            env["XDG_CACHE_HOME"] = caches.path
            env["HF_HOME"] = caches.appendingPathComponent("huggingface").path
            env["TORCH_HOME"] = caches.appendingPathComponent("torch").path
            env["CELLPOSE_LOCAL_MODELS_PATH"] = caches.appendingPathComponent("cellpose/models").path
            env["MICROSAM_CACHEDIR"] = caches.appendingPathComponent("micro_sam").path
        }
        env["ORCH_WAIT_S"] = "120"
        env["HOOKEUI_APP"] = places.bundle.path
        return env
    }

    /// The Python of this lockfile: made once (uv, from the hashed lockfile) and shared by every version with the same
    /// lockfile, so an update that changes no package starts at once. The runtime's services/.venv points to it.
    /// Returns whether it downloaded (false: this lockfile's Python was there).
    @discardableResult
    func syncPython(_ runtime: URL, env: [String: String]) throws -> Bool {
        let services = runtime.appendingPathComponent("services")
        let lock = sha256(services.appendingPathComponent("uv.lock"))
        let venv = Services.python(lock)
        let link = services.appendingPathComponent(".venv")
        try? fm.removeItem(at: link)
        try fm.createDirectory(at: places.envs, withIntermediateDirectories: true)
        try fm.createSymbolicLink(at: link, withDestinationURL: venv)
        let marker = venv.appendingPathComponent(".hookeui-lock")
        if Services.pythonReady(venv, lock: lock) { return false }
        var env = env
        env["UV_PROJECT_ENVIRONMENT"] = venv.path
        let (heading, detail) = SetupWords.download(first: planned?.firstPython ?? true, runtime: runtimeDownload)
        say(heading, detail, .download)
        let started = Date()
        var last = ""
        // packaging/macos/runtime_download.py measures the first launch's download with these same arguments.
        let status = try run(places.helpers.appendingPathComponent("uv").path,
                             ["sync", "--locked", "--python", "3.13", "--all-groups", "--inexact"],
                             cwd: services, env: env) { [weak self] text in
            if let said = SetupWords.uvLine(text) {
                last = said
                self?.say(heading, SetupWords.progress(detail, said), .download)
            }
        }
        guard status == 0 else {
            throw Failure(message: "Downloading Hooke’s scientific software stopped (\(last.isEmpty ? "exit \(status)" : last)). "
                          + "Check the internet connection and try again.")
        }
        try Data(lock.utf8).write(to: marker)
        Log.shared.line("python ready in \(Int(Date().timeIntervalSince(started))) s")
        return true
    }

    /// The first import of each module and native library is slow on a new Mac (bytecode is compiled and macOS checks
    /// every library it hasn't seen): done once here, with words, instead of inside the services' start limits.
    /// Returns whether it loaded them (false: this lockfile's Python was loaded before).
    @discardableResult
    func warm(_ runtime: URL, env: [String: String]) throws -> Bool {
        let services = runtime.appendingPathComponent("services")
        let marker = services.appendingPathComponent(".venv/.hookeui-warm")  // in the shared Python
        let lock = sha256(services.appendingPathComponent("uv.lock"))
        if Services.warmed(Services.python(lock), lock: lock) { return false }
        say(SetupWords.getReady.0, SetupWords.getReady.1, .initialize)
        let started = Date()
        let python = services.appendingPathComponent(".venv/bin/python").path
        _ = try run(python, ["-m", "compileall", "-q", "-j", "0", "orchestrator", "gpu_worker", "research_worker",
                             "env_worker", "fiji_worker", "noncommercial"], cwd: services, env: env)
        let status = try run(python, ["-c", "import orchestrator.server, gpu_worker.worker, research_worker, cv2, scipy.ndimage, torch"],
                             cwd: services, env: env)
        guard status == 0 else { throw Failure(message: "The app's Python could not load its packages (exit \(status)).") }
        try Data(lock.utf8).write(to: marker)
        Log.shared.line("warmed in \(Int(Date().timeIntervalSince(started))) s")
        return true
    }

    /// Everything up to the studio answering. Called off the main thread.
    func start() throws {
        let begun = Date()
        let id = try buildID()
        Log.shared.line("starting \(appName) \(id) from \(places.bundle.path)")
        try fm.createDirectory(at: places.support, withIntermediateDirectories: true)
        if planned == nil { _ = try plan() }
        let (runtime, copied) = try installRuntime(id)
        finished(.prepare, copied)
        self.runtime = runtime
        try makeBin()
        var env = baseEnvironment()
        let downloaded = try syncPython(runtime, env: env)
        finished(.download, downloaded)
        let loaded = try warm(runtime, env: env)
        finished(.initialize, loaded)

        say(SetupWords.openTitle, SetupWords.startingCamera, .open)
        let script = runtime.appendingPathComponent("scripts/studio.sh").path
        // A copy left by a crash or a force quit, of this version or an earlier one (each version's studio.sh stops only
        // the processes of its own folder): otherwise it keeps 8770 and the camera, and this one starts beside it.
        for folder in (try? fm.contentsOfDirectory(at: places.runtimes, includingPropertiesForKeys: nil)) ?? []
        where fm.isExecutableFile(atPath: folder.appendingPathComponent("scripts/studio.sh").path) {
            _ = try? run("/bin/bash", [folder.appendingPathComponent("scripts/studio.sh").path, "stop"], cwd: folder, env: env)
        }
        let orchestrator = choosePort(channel.orchestratorPort, avoiding: [])
        let camera = choosePort(channel.cameraPort, avoiding: [orchestrator])
        cameraPort = camera
        env["ORCH_PORT"] = String(orchestrator)
        env["CAMERA_PORT"] = String(camera)
        environment = env

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [script, "start"]
        process.currentDirectoryURL = runtime
        process.environment = env
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        let ready = DispatchSemaphore(value: 0)
        var readyLine = false
        var tail: [String] = []
        var pending = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty else { return }
            Log.shared.raw(chunk)
            pending.append(chunk)
            while let nl = pending.firstIndex(of: 10) {
                let text = String(decoding: pending[..<nl], as: UTF8.self)
                pending.removeSubrange(...nl)
                tail = Array((tail + [text]).suffix(12))
                if text.hasPrefix("Starting the orchestrator") { self?.say(SetupWords.openTitle, SetupWords.startingWorkspace, .open) }
                if text.hasPrefix("Starting analysis workers") { self?.say(SetupWords.openTitle, SetupWords.startingTools, .open) }
                if text.contains(" is running") && !readyLine { readyLine = true; ready.signal() }
            }
        }
        process.terminationHandler = { _ in ready.signal() }
        try process.run()
        studio = process
        ready.wait()
        guard readyLine, process.isRunning else {
            Log.shared.line("services stopped while starting: " + tail.suffix(6).joined(separator: " | "))
            throw Failure(message: "Something stopped \(appName) while it was starting. Try again; if it happens again, "
                          + "Show log has the details to send us.")
        }
        studioURL = URL(string: "http://127.0.0.1:\(orchestrator)/")
        Log.shared.line("studio ready at \(studioURL!.absoluteString) after \(Int(Date().timeIntervalSince(begun))) s")
        // Earlier versions' copies, once this one is up (the user's data is never in them).
        for old in (try? fm.contentsOfDirectory(at: places.runtimes, includingPropertiesForKeys: nil)) ?? []
        where old.lastPathComponent != id {
            let bytes = UpdateWords.folderBytes(old)
            if (try? fm.removeItem(at: old)) != nil { removedRuntimeBytes += bytes }
        }
        // Pythons of other lockfiles: the newest one stays (going back a version starts at once), older ones go.
        let current = (try? fm.destinationOfSymbolicLink(atPath: runtime.appendingPathComponent("services/.venv").path)) ?? ""
        let others = ((try? fm.contentsOfDirectory(at: places.envs, includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
            .filter { $0.path != current }
            .sorted { a, b in
                let date = { (u: URL) in (try? u.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast }
                return date(a) > date(b)
            }
        keptPython = !others.isEmpty
        for old in others.dropFirst() {
            let bytes = UpdateWords.folderBytes(old)
            if (try? fm.removeItem(at: old)) != nil { removedPythonBytes += bytes }
        }
        Log.shared.line("earlier copies: removed runtime \(removedRuntimeBytes) bytes, python \(removedPythonBytes) bytes; previous python kept: \(keptPython)")
    }

    func port(_ name: String) -> String { environment[name] ?? "" }

    /// Stops what start started: studio.sh's own trap stops its servers; `stop` then ends anything left.
    func stop() {
        if let studio, studio.isRunning {
            studio.terminate()
            let deadline = Date().addingTimeInterval(10)
            while studio.isRunning && Date() < deadline { usleep(100_000) }
        }
        if let runtime {
            _ = try? run("/bin/bash", [runtime.appendingPathComponent("scripts/studio.sh").path, "stop"], cwd: runtime,
                         env: environment.isEmpty ? baseEnvironment() : environment)
        }
    }
}

// MARK: - The setup window: SetupView.swift draws what SetupWords.swift decides.

// MARK: - The studio window

final class Studio: NSObject, WKUIDelegate, WKNavigationDelegate, WKDownloadDelegate {
    let webView: WKWebView
    var url: URL
    private var popups: [NSWindow] = []
    let folderBridge: FolderBridge
    let cameraNetwork: CameraNetwork

    init(url: URL, updates: Updates, cameraPort: Int) {
        self.url = url
        folderBridge = FolderBridge(origin: url)
        cameraNetwork = CameraNetwork(origin: url, cameraPort: cameraPort)
        let configuration = WKWebViewConfiguration()
        configuration.userContentController.addScriptMessageHandler(folderBridge, contentWorld: .page, name: "hookeFolder")
        configuration.userContentController.addScriptMessageHandler(cameraNetwork, contentWorld: .page, name: "hookeCameraNetwork")
        updates.origin = url
        configuration.userContentController.addScriptMessageHandler(updates, contentWorld: .page, name: "hookeUpdates")
        configuration.preferences.isElementFullscreenEnabled = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.setValue(false, forKey: "drawsBackground")  // the window's dark colour shows while the page loads
        super.init()
        updates.webView = webView
        webView.uiDelegate = self
        webView.navigationDelegate = self
        webView.isInspectable = true
        // A new native release must not start with a cached older workspace shell.
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData))
    }

    func isStudio(_ target: URL?) -> Bool {
        guard let target, let host = target.host else { return false }
        return ["127.0.0.1", "localhost", "::1"].contains(host)
    }

    // Links out of the app open in the browser; the studio's own pages stay here.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload { decisionHandler(.download); return }
        if let target = action.request.url, ["http", "https"].contains(target.scheme ?? ""), !isStudio(target) {
            NSWorkspace.shared.open(target)
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        decisionHandler(response.canShowMIMEType ? .allow : .download)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in
            guard let self else { return }
            self.webView.load(URLRequest(url: self.url))
        }
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

    // A new window: the studio's own pages open in a window of the app; anything else in the browser.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let target = action.request.url, ["http", "https"].contains(target.scheme ?? ""), !isStudio(target) {
            NSWorkspace.shared.open(target)
            return nil
        }
        let popup = WKWebView(frame: NSRect(x: 0, y: 0, width: 1100, height: 760), configuration: configuration)
        popup.uiDelegate = self
        popup.navigationDelegate = self
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 760),
                              styleMask: [.titled, .closable, .resizable, .miniaturizable], backing: .buffered, defer: false)
        window.contentView = popup
        window.title = appName
        window.isReleasedWhenClosed = false
        window.center()
        window.makeKeyAndOrderFront(nil)
        popups.append(window)
        return popup
    }

    func webViewDidClose(_ webView: WKWebView) {
        popups.first { $0.contentView === webView }?.close()
        popups.removeAll { $0.contentView === webView }
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn)
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String, defaultText: String?,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        completionHandler(alert.runModal() == .alertFirstButtonReturn ? field.stringValue : nil)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.canChooseFiles = true
        panel.begin { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    // Talk (the microphone) and the camera, for the studio's own pages only.
    func webView(_ webView: WKWebView, requestMediaCapturePermissionFor origin: WKSecurityOrigin,
                 initiatedByFrame frame: WKFrameInfo, type: WKMediaCaptureType,
                 decisionHandler: @escaping (WKPermissionDecision) -> Void) {
        decisionHandler(["127.0.0.1", "localhost"].contains(origin.host) ? .grant : .prompt)
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String,
                  completionHandler: @escaping (URL?) -> Void) {
        let folder = fm.urls(for: .downloadsDirectory, in: .userDomainMask).first ?? fm.homeDirectoryForCurrentUser
        let name = suggestedFilename.isEmpty ? "download" : suggestedFilename
        var target = folder.appendingPathComponent(name)
        var n = 2
        while fm.fileExists(atPath: target.path) {
            let stem = (name as NSString).deletingPathExtension, ext = (name as NSString).pathExtension
            target = folder.appendingPathComponent(ext.isEmpty ? "\(stem) \(n)" : "\(stem) \(n).\(ext)")
            n += 1
        }
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        if let target = download.progress.fileURL { NSWorkspace.shared.activateFileViewerSelecting([target]) }
    }
}

// MARK: - The app

// MARK: - Updates

/// Release notes as plain text a sheet can show: the appcast's small HTML (appcast.py writes h3, p, ul/li only).
func notesText(_ html: String) -> NSAttributedString {
    var text = html
    for (tag, with) in [("<li>", "• "), ("</li>", "\n"), ("</p>", "\n"), ("</h3>", "\n"), ("<ul>", ""), ("</ul>", "\n")] {
        text = text.replacingOccurrences(of: tag, with: with)
    }
    text = text.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
    for (entity, char) in [("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#x27;", "'"), ("&amp;", "&")] {
        text = text.replacingOccurrences(of: entity, with: char)
    }
    let paragraph = NSMutableParagraphStyle()
    paragraph.paragraphSpacing = 4
    paragraph.headIndent = 12
    return NSAttributedString(string: text.trimmingCharacters(in: .whitespacesAndNewlines),
                              attributes: [.font: NSFont.systemFont(ofSize: 13), .foregroundColor: NSColor.labelColor,
                                           .paragraphStyle: paragraph])
}

func notesView(_ html: String) -> NSView {
    let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 380, height: 150))
    scroll.hasVerticalScroller = true
    scroll.borderType = .noBorder
    scroll.drawsBackground = false
    let text = NSTextView(frame: scroll.bounds)
    text.isEditable = false
    text.drawsBackground = false
    text.textStorage?.setAttributedString(notesText(html))
    scroll.documentView = text
    return scroll
}

/// Run from the disk image (or a translocated copy), the app can't update itself: offer to move it to Applications.
func originalBundleURL() -> URL {
    let url = Bundle.main.bundleURL
    guard url.path.contains("/AppTranslocation/"),
          let security = dlopen("/System/Library/Frameworks/Security.framework/Security", RTLD_LAZY),
          let symbol = dlsym(security, "SecTranslocateCreateOriginalPathForURL") else { return url }
    typealias Original = @convention(c) (CFURL, UnsafeMutablePointer<Unmanaged<CFError>?>?) -> Unmanaged<CFURL>?
    let original = unsafeBitCast(symbol, to: Original.self)
    return original(url as CFURL, nil)?.takeRetainedValue() as URL? ?? url
}

func offerToMoveToApplications() -> Bool {
    let source = originalBundleURL()
    let applications = URL(fileURLWithPath: "/Applications")
    guard source.path.hasPrefix("/Volumes/") || Bundle.main.bundleURL.path.contains("/AppTranslocation/"),
          source.deletingLastPathComponent().path != applications.path else { return false }
    let alert = NSAlert()
    alert.messageText = "Move \(appName) to your Applications folder?"
    alert.informativeText = "\(appName) runs from Applications, so it can keep itself up to date."
    alert.addButton(withTitle: "Move to Applications")
    alert.addButton(withTitle: "Not Now")
    NSApp.activate(ignoringOtherApps: true)
    guard alert.runModal() == .alertFirstButtonReturn else { return false }
    let target = applications.appendingPathComponent("\(appName).app")  // a Beta never replaces Stable's copy
    if fm.fileExists(atPath: target.path) { try? fm.trashItem(at: target, resultingItemURL: nil) }
    let base = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
    guard (try? run("/usr/bin/ditto", [source.path, target.path], env: base)) == 0 else {
        let failed = NSAlert()
        failed.messageText = "\(appName) couldn't be moved"
        failed.informativeText = "Drag \(appName) into the Applications folder in the window it came in, then open it from there."
        failed.runModal()
        return false
    }
    // The user opened this copy already; the moved one is the same app.
    _ = try? run("/usr/bin/xattr", ["-dr", "com.apple.quarantine", target.path], env: base)
    Log.shared.line("moved to \(target.path)")
    let configuration = NSWorkspace.OpenConfiguration()
    configuration.createsNewApplicationInstance = true
    NSWorkspace.shared.openApplication(at: target, configuration: configuration) { _, _ in
        DispatchQueue.main.async { NSApp.terminate(nil) }
    }
    return true
}

// MARK: - The app

final class AppDelegate: NSObject, NSApplicationDelegate {
    var window: NSWindow!
    let setup = SetupView()
    let services = Services()
    var studio: Studio?
    var quitting = false
    /// Restarting into an update, approved after its check (LeaveFlow): the next quit request goes ahead.
    var leaveApproved = false
    /// A plain quit waits for its check (terminateLater): its answer goes back to AppKit, so a logout waits for it too.
    var quitReplyPending = false
    var moving = false
    let updates = Updates()
    lazy var updater = SPUStandardUpdaterController(startingUpdater: false, updaterDelegate: updates, userDriverDelegate: updates)
    /// What restarting into an update or quitting would stop or lose, read before either and said on a sheet when
    /// something is at stake (UpdateWords.swift; packaging/AGENTS.md "Updates").
    lazy var leave: LeaveFlow = {
        let flow = LeaveFlow(
            read: { [weak self] done in
                guard let self else { done(.unreadable); return }
                self.updates.reads.read(done)
            },
            checking: { [weak self] leaving in self?.showChecking(leaving) },
            ask: { [weak self] prompt, answer in
                guard let self, let window = self.window else { answer(.stay); return }
                window.makeKeyAndOrderFront(nil)
                let alert = UpdateWords.alert(prompt)
                alert.beginSheetModal(for: window) { answer(UpdateWords.answer(prompt, $0)) }
            },
            proceed: { [weak self] leaving in
                guard let self else { return }
                Log.shared.line(leaving == .restart ? "update restart approved after its check" : "quit approved after its check")
                if self.quitReplyPending, let window = self.window {
                    self.quitReplyPending = false
                    self.stopForTermination(window)  // replies to the waiting quit once services have stopped
                } else {
                    self.leaveApproved = true
                    // Not inside this block: its quit waits for an answer the main queue could not deliver (Termination.swift).
                    Termination.terminate()
                }
            },
            stay: { [weak self] leaving in
                guard let self else { return }
                Log.shared.line(leaving == .restart ? "update restart deferred; workspace remains running"
                                                    : "quit cancelled; workspace remains running")
                if self.quitReplyPending { self.quitReplyPending = false; NSApp.reply(toApplicationShouldTerminate: false) }
                self.updates.publish()  // with an update waiting, the workspace says it is downloaded and waits
            })
        flow.log = { Log.shared.line($0) }
        return flow
    }()

    func applicationDidFinishLaunching(_ notification: Notification) {
        Log.shared.open()
        Log.shared.line("\(appName) \(Bundle.main.shortVersion) (\(Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") ?? "?"), \(channel.id) channel)")
        if offerToMoveToApplications() { moving = true; return }
        updates.check = { [weak self] in
            guard let self else { return }
            // Sparkle keeps its installer waiting after Later. Ask about the restart again here (Restart to update in the
            // workspace, or the Hooke menu's Check for Updates…) instead of asking the busy updater to check again.
            if self.updates.installing { self.askToRestart() }
            else { self.updater.checkForUpdates(nil) }
        }
        updater.startUpdater()
        buildMenu()
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1440, height: 900),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = appName
        window.minSize = NSSize(width: 900, height: 600)
        window.backgroundColor = NSColor(calibratedWhite: 0.09, alpha: 1)
        window.appearance = NSAppearance(named: .darkAqua)  // the setup screen's words on the studio's dark ground
        window.contentView = setup
        window.setFrameAutosaveName("HookeUI.main")
        if !window.setFrameUsingName("HookeUI.main") { window.center() }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        begin()
    }

    /// The first launch's download was agreed to in this run: Try again does not ask again.
    var consented = false

    /// Plans the launch (SetupWords.swift), asks once before a first launch's required download, then starts.
    func begin() {
        setup.buttons.arrangedSubviews.forEach { $0.removeFromSuperview() }
        setup.spinner.isHidden = false
        let plan: SetupPlan
        do { plan = try services.plan() } catch {
            let message = (error as? Failure)?.message ?? error.localizedDescription
            Log.shared.line("failed: \(message)")
            showFailure(message)
            return
        }
        setup.plan(plan, version: Bundle.main.shortVersion)
        let (heading, text) = SetupWords.opening(plan, product: SetupWords.product(appName), version: Bundle.main.shortVersion)
        if plan.asksFirst && !consented {
            setup.ask(SetupWords.consent(services.runtimeDownload), target: self, accept: #selector(downloadAndOpen))
            Log.shared.line("asking before the first download")
            return
        }
        setup.show(heading, text, SetupPhase(plan.stages.first ?? .open))
        run()
    }

    @objc func downloadAndOpen() {
        consented = true
        Log.shared.line("first download agreed")
        begin()
    }

    /// Starts the services off the main thread; the screen follows what each step says and reports.
    func run() {
        services.say = { [weak self] heading, text, phase in
            DispatchQueue.main.async { self?.setup.show(heading, text, phase) }
        }
        services.finished = { [weak self] phase, ran in
            DispatchQueue.main.async { self?.setup.finish(phase, ran: ran) }
        }
        DispatchQueue.global(qos: .userInitiated).async { [self] in
            do {
                try services.start()
                DispatchQueue.main.async { self.showStudio() }
            } catch {
                var message = (error as? Failure)?.message ?? error.localizedDescription
                Log.shared.line("failed: \(message)")
                services.stop()
                if self.justUpdated { message = UpdateWords.failedAfterUpdate(message) }
                DispatchQueue.main.async { self.showFailure(message) }
            }
        }
    }

    func showStudio() {
        guard let url = services.studioURL, !quitting else { return }
        let studio = Studio(url: url, updates: updates, cameraPort: services.cameraPort)
        self.studio = studio
        window.contentView = studio.webView
        watchVisibility()
        sayWhatsNew()
        if let process = services.studio {
            DispatchQueue.global().async { [weak self] in
                process.waitUntilExit()
                DispatchQueue.main.async {
                    guard let self, !self.quitting else { return }
                    self.window.contentView = self.setup
                    self.showFailure("\(appName)'s services stopped. The log says why.")
                }
            }
        }
    }

    /// What the studio's page says about being seen (it lands a session only while visible), logged when it changes.
    var lastVisibility = ""
    func watchVisibility() {
        Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            guard let self, let web = self.studio?.webView else { return }
            web.evaluateJavaScript("document.visibilityState + ' focus=' + document.hasFocus()") { value, _ in
                let seen = "\(value ?? "?") window=\(self.window.occlusionState.contains(.visible) ? "visible" : "occluded")"
                if seen != self.lastVisibility { self.lastVisibility = seen; Log.shared.line("studio page: \(seen)") }
            }
        }
    }

    func showFailure(_ message: String) {
        setup.spinner.isHidden = true
        setup.show("Let’s get you back on track", message, .failed)
        let again = NSButton(title: "Try again", target: self, action: #selector(tryAgain))
        again.keyEquivalent = "\r"
        let log = NSButton(title: "Show log", target: self, action: #selector(showLog))
        let quit = NSButton(title: "Quit", target: NSApp, action: #selector(NSApplication.terminate(_:)))
        [quit, log, again].forEach { setup.buttons.addArrangedSubview($0) }
    }

    /// This version was just installed by Sparkle and hasn't opened its workspace yet.
    var justUpdated: Bool { UpdateWords.justUpdated(.standard, version: Bundle.main.shortVersion) }

    /// Once, on the first launch of a version Sparkle installed: what changed, and what the update did to storage.
    func sayWhatsNew() {
        guard let new = UserDefaults.standard.dictionary(forKey: "HookeUIWhatsNew") as? [String: String],
              new["version"] == Bundle.main.shortVersion else { return }
        UserDefaults.standard.removeObject(forKey: "HookeUIWhatsNew")
        let alert = NSAlert()
        alert.messageText = "What's new in \(appName) \(Bundle.main.shortVersion)"
        alert.informativeText = UpdateWords.afterUpdate(removedRuntime: services.removedRuntimeBytes,
                                                        removedPython: services.removedPythonBytes, keptPython: services.keptPython)
        if let notes = new["notes"], !notes.isEmpty { alert.accessoryView = notesView(notes) }
        alert.addButton(withTitle: "OK")
        alert.beginSheetModal(for: window)
    }

    @objc func tryAgain() { begin() }
    @objc func showLog() { NSWorkspace.shared.activateFileViewerSelecting([places.log]) }
    @objc func showData() { NSWorkspace.shared.open(places.data) }
    @objc func website() { NSWorkspace.shared.open(URL(string: "https://hookeui.org")!) }
    @objc func reload() { studio?.webView.reload() }
    // The Tab menu sends its command to the studio, which does what the same keys do there (hookeTabCommand).
    @objc func tabCommand(_ sender: NSMenuItem) {
        guard window?.isKeyWindow == true, let name = sender.representedObject as? String else { return }
        studio?.webView.evaluateJavaScript("window.hookeTabCommand && window.hookeTabCommand(\(String(reflecting: name)))")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // The installation alert closes before the main window exists. Copying the
        // app pumps AppKit events, so that alert must not terminate installation.
        window != nil
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if moving { return .terminateNow }
        if quitting { return .terminateLater }
        // Explicit quit during installation has no services or main window to stop.
        guard let window else { return .terminateNow }
        // Without a workspace there is nothing of it to stop or lose; an approved restart or quit goes ahead.
        if leaveApproved || studio == nil { stopForTermination(window); return .terminateLater }
        // Its sheet is open: that sheet answers.
        guard !leave.busy else { return .terminateCancel }
        // Sparkle's Install and Relaunch asks to restart; anything else (⌘Q, Quit, closing the window, logging out) is a
        // plain quit. Both read what is at stake first.
        if updates.installing && updates.takeRestartRequest() {
            // Cancel Sparkle's request before the asynchronous check: keeping NSApplication in its termination loop
            // would strand its later requests. Approving asks to quit again.
            DispatchQueue.main.async { self.leave.start(.restart, version: self.updates.downloadedVersion, updateWaiting: true) }
            return .terminateCancel
        }
        // A plain quit waits for its check and its answer, as a document's Save sheet does, so a logout waits too.
        quitReplyPending = true
        DispatchQueue.main.async {
            self.leave.start(.quit, version: self.updates.downloadedVersion, updateWaiting: self.updates.installing)
        }
        return .terminateLater
    }

    /// The restart sheet again, while an update waits for it (Later was chosen).
    private func askToRestart() {
        guard studio != nil else { Termination.terminate(); return }  // no workspace: nothing to check, restart into it
        leave.start(.restart, version: updates.downloadedVersion, updateWaiting: true)
    }
    @objc func checkForUpdates(_ sender: Any?) { updates.check?() }

    /// While the reads run (at most a few seconds): a quiet panel over the workspace, faded in so a quick read never
    /// flashes it.
    private var checkingView: NSView?
    private func showChecking(_ leaving: Leaving?) {
        checkingView?.removeFromSuperview()
        checkingView = nil
        guard let leaving, let content = window?.contentView else { return }
        // Sparkle's window has just closed: the workspace comes forward to say what happens next. A quit by closing
        // the window does not reopen it unless there is something to ask.
        if leaving == .restart { window.makeKeyAndOrderFront(nil) }
        let view = UpdateWords.checkingView(leaving)
        view.translatesAutoresizingMaskIntoConstraints = false
        view.alphaValue = 0
        content.addSubview(view)
        NSLayoutConstraint.activate([view.centerXAnchor.constraint(equalTo: content.centerXAnchor),
                                     view.centerYAnchor.constraint(equalTo: content.centerYAnchor)])
        checkingView = view
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak view] in
            NSAnimationContext.runAnimationGroup { context in context.duration = 0.15; view?.animator().alphaValue = 1 }
        }
    }

    private func stopForTermination(_ window: NSWindow) {
        quitting = true
        studio?.folderBridge.close()
        studio?.cameraNetwork.close()
        window.contentView = setup
        setup.buttons.arrangedSubviews.forEach { $0.removeFromSuperview() }
        setup.spinner.isHidden = false
        setup.show("Closing your workspace", "Finishing up and stopping your tools.", .stopping)
        DispatchQueue.global().async { [self] in
            services.stop()
            Log.shared.line("stopped")
            Termination.reply(true)  // through the run loop: answered however the quit began
        }
    }

    func buildMenu() {
        let main = NSMenu()
        func submenu(_ title: String, _ items: [NSMenuItem]) {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            items.forEach { menu.addItem($0) }
            item.submenu = menu
            main.addItem(item)
        }
        func item(_ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command,
                  target: AnyObject? = nil) -> NSMenuItem {
            let i = NSMenuItem(title: title, action: action, keyEquivalent: key)
            i.keyEquivalentModifierMask = mods
            i.target = target
            return i
        }
        submenu(appName, [
            item("About \(appName)", #selector(NSApplication.orderFrontStandardAboutPanel(_:)), target: NSApp),
            // Through Updates.check: with an update waiting for its restart this asks about the restart, never Sparkle's
            // own window (whose Install and Relaunch would arrive as a quit Hooke cannot tell apart from ⌘Q).
            item("Check for Updates…", #selector(checkForUpdates(_:)), target: self),
            .separator(),
            item("Hide \(appName)", #selector(NSApplication.hide(_:)), "h", target: NSApp),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option], target: NSApp),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:)), target: NSApp),
            .separator(),
            item("Quit \(appName)", #selector(NSApplication.terminate(_:)), "q", target: NSApp),
        ])
        submenu("Edit", [
            item("Undo", Selector(("undo:")), "z"), item("Redo", Selector(("redo:")), "z", [.command, .shift]),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x"), item("Copy", #selector(NSText.copy(_:)), "c"),
            item("Paste", #selector(NSText.paste(_:)), "v"), item("Select All", #selector(NSText.selectAll(_:)), "a"),
        ])
        submenu("View", [
            item("Reload", #selector(reload), "r", target: self),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control]),
        ])
        func tab(_ title: String, _ name: String, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command) -> NSMenuItem {
            let i = item(title, #selector(tabCommand(_:)), key, mods, target: self)
            i.representedObject = name
            return i
        }
        // Tabs in the open Project, with the keys a browser uses for its tabs. Main stays open: ⌘W there says so.
        submenu("Tab", [
            // Leave Command-T to the focused web control: scientific views use it for transpose.
            tab("New Tab", "new"), tab("Duplicate Tab", "duplicate"),
            tab("Close Tab", "close", "w"), tab("Reopen Closed Tab", "reopen", "t", [.command, .shift]),
            .separator(),
            tab("Show Previous Tab", "previous", "{"), tab("Show Next Tab", "next", "}"),
            .separator(),
        ] + (1...8).map { tab("Go to Tab \($0)", "go:\($0)", "\($0)") } + [tab("Go to Last Tab", "go:9", "9")])
        submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Zoom", #selector(NSWindow.performZoom(_:))),
        ])
        submenu("Help", [
            item("\(appName) Website", #selector(website), target: self),
            item("Show Your Data in Finder", #selector(showData), target: self),
            item("Show Log in Finder", #selector(showLog), target: self),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
