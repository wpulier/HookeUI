import AppKit
import WebKit

// What leaving says and reads before Hooke restarts into an update or quits, apart from Sparkle so `make native-test`
// checks it on its own (packaging/macos/tests/update-restart). main.swift gives LeaveFlow its window, sheet and
// termination, and uses folderBytes and justUpdated.

/// Why Hooke is closing: to finish an update (Sparkle's Install and Relaunch, or Restart to update in the workspace), or
/// a plain quit (⌘Q, Quit, closing the window, logging out). Every plain quit follows one rule, an update waiting or not.
enum Leaving: Equatable { case restart, quit }

/// What restarting or quitting would stop or lose, as read: the workspace's unsaved work (blockers) and its running work,
/// each in the workspace's own words, with the app's own downloads and installs. Unreadable when any read failed, timed
/// out or answered in another shape: never taken as "nothing is running".
enum Reading: Equatable {
    case read(blockers: [String], running: [String])
    case unreadable
}

/// The reads behind leaving: the workspace's answer (apps/studio/src/app-updates.ts `hookeUpdateReadiness`) and the
/// app's downloads and installs, from the orchestrator this window shows.
final class RestartReads {
    struct Workspace: Equatable { let blockers: [String]; let running: [String] }
    let webView: () -> WKWebView?
    let origin: () -> URL?
    /// How long each read may take before it counts as unreadable.
    let timeout: TimeInterval
    init(webView: @escaping () -> WKWebView?, origin: @escaping () -> URL?, timeout: TimeInterval = 5) {
        self.webView = webView; self.origin = origin; self.timeout = timeout
    }

    /// Both reads at once; one reading once both have answered (each is bounded on its own). Either unreadable makes it
    /// unreadable.
    static func combine(readWorkspace: (@escaping (Workspace?) -> Void) -> Void, readActive: (@escaping ([String]?) -> Void) -> Void,
                        _ completion: @escaping (Reading) -> Void) {
        var workspace: Workspace?, active: [String]?, answered = 0
        let done = {
            answered += 1
            guard answered == 2 else { return }
            if let w = workspace, let a = active { completion(.read(blockers: w.blockers, running: w.running + a)) }
            else { completion(.unreadable) }
        }
        readWorkspace { value in DispatchQueue.main.async { workspace = value; done() } }
        readActive { value in DispatchQueue.main.async { active = value; done() } }
    }

    func read(_ completion: @escaping (Reading) -> Void) {
        RestartReads.combine(readWorkspace: readWorkspace, readActive: readActiveWork, completion)
    }

    /// The workspace's answer, `{blockers, running}` (both lists of words); anything else is nil.
    static func workspace(_ value: Any?) -> Workspace? {
        guard let result = value as? [String: Any], let blockers = result["blockers"] as? [String],
              let running = result["running"] as? [String] else { return nil }
        return Workspace(blockers: blockers, running: running)
    }

    /// Read-only and bounded: no window, no answer in time, a failed read or another shape is nil (unreadable). The
    /// workspace's read is asynchronous (it asks the orchestrator what is running in every Project).
    func readWorkspace(_ completion: @escaping (Workspace?) -> Void) {
        var finished = false
        let finish: (Workspace?) -> Void = { value in
            guard !finished else { return }; finished = true; completion(value)
        }
        guard let web = webView() else { finish(nil); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout) { finish(nil) }
        web.callAsyncJavaScript("const read = window.hookeUpdateReadiness; return typeof read === 'function' ? await read() : null;",
                                arguments: [:], in: nil, in: .page) { result in
            if case .success(let value) = result { finish(RestartReads.workspace(value)) } else { finish(nil) }
        }
    }

    /// The app's own downloads and installs still going, in its own words ("Downloading DINOv2 small"), read from the
    /// orchestrator this window shows. Bounded; an unreadable answer is nil, never "nothing".
    func readActiveWork(_ completion: @escaping ([String]?) -> Void) {
        guard let origin = origin(), let url = URL(string: "api/capabilities", relativeTo: origin) else { completion(nil); return }
        var request = URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: max(1, timeout - 1))
        request.httpMethod = "GET"
        URLSession.shared.dataTask(with: request) { data, response, _ in
            let lines = (response as? HTTPURLResponse)?.statusCode == 200 ? data.flatMap(UpdateWords.activeWork) : nil
            DispatchQueue.main.async { completion(lines) }
        }.resume()
    }
}

/// One leave at a time: read, ask when something is at stake, read again before going on (going on needs nothing new
/// at stake since the sheet), then go on or stay. main.swift supplies the reads, the feedback, the sheet and the quit.
final class LeaveFlow {
    let read: (@escaping (Reading) -> Void) -> Void
    /// Shows the feedback while reading ("Checking your work before restarting…"), or hides it (nil).
    let checking: (Leaving?) -> Void
    let ask: (UpdateWords.Prompt, @escaping (UpdateWords.Answer) -> Void) -> Void
    let proceed: (Leaving) -> Void
    let stay: (Leaving) -> Void
    var log: (String) -> Void = { _ in }
    private(set) var busy = false

    init(read: @escaping (@escaping (Reading) -> Void) -> Void, checking: @escaping (Leaving?) -> Void,
         ask: @escaping (UpdateWords.Prompt, @escaping (UpdateWords.Answer) -> Void) -> Void,
         proceed: @escaping (Leaving) -> Void, stay: @escaping (Leaving) -> Void) {
        self.read = read; self.checking = checking; self.ask = ask; self.proceed = proceed; self.stay = stay
    }

    /// Starts a leave; false while one is under way (its sheet is still open).
    @discardableResult func start(_ leaving: Leaving, version: String?, updateWaiting: Bool) -> Bool {
        guard !busy else { return false }
        busy = true
        check(leaving, version: version, updateWaiting: updateWaiting, agreed: nil)
        return true
    }

    private func check(_ leaving: Leaving, version: String?, updateWaiting: Bool, agreed: UpdateWords.Prompt?) {
        checking(leaving)
        read { [self] reading in
            checking(nil)
            log(UpdateWords.logLine(leaving, reading, recheck: agreed != nil))
            if let agreed, UpdateWords.stillAgreed(agreed, reading) { finish(leaving, go: true); return }
            guard let prompt = UpdateWords.prompt(leaving, reading, version: version, updateWaiting: updateWaiting) else {
                finish(leaving, go: true); return
            }
            ask(prompt) { [self] answer in
                guard answer == .proceed else { finish(leaving, go: false); return }
                if prompt.recheck { check(leaving, version: version, updateWaiting: updateWaiting, agreed: prompt) }
                else { finish(leaving, go: true) }
            }
        }
    }

    private func finish(_ leaving: Leaving, go: Bool) {
        busy = false
        if go { proceed(leaving) } else { stay(leaving) }
    }
}

/// What updating and leaving say, kept apart from Sparkle so the words are checked on their own (packaging/macos/tests).
enum UpdateWords {
    enum Answer: Equatable { case proceed, stay }
    struct Button: Equatable {
        let title: String
        let answer: Answer
        /// Return presses it: at most one per sheet, and never one that stops work.
        var isDefault = false
        /// Escape presses it.
        var isCancel = false
        var destructive = false
    }
    struct Prompt: Equatable {
        let leaving: Leaving
        let title: String
        let text: String
        /// In the order NSAlert adds them: the first is the rightmost.
        let buttons: [Button]
        /// Everything it names as running (the text shows at most 6): going on after the recheck allows no more.
        let running: [String]
        /// Going on reads again first.
        let recheck: Bool
        var canProceed: Bool { buttons.contains { $0.answer == .proceed } }
    }

    /// Where the user's own work is: the three homes in ~/MicroscopeMind (docs/saves.md), or a Beta's own folder
    /// (HookeUIDataFolder, packaging/macos/channels.json). Updating never writes there.
    static let savedWork = "~/" + (Bundle.main.object(forInfoDictionaryKey: "HookeUIDataFolder") as? String ?? "MicroscopeMind")

    /// At most six lines, then how many more.
    static func list(_ lines: [String]) -> String {
        (lines.prefix(6).map { "• " + $0 } + (lines.count > 6 ? ["and \(lines.count - 6) more"] : [])).joined(separator: "\n")
    }

    private static let ok = Button(title: "OK", answer: .stay, isDefault: true)

    /// The sheet for leaving, from what was read; nil for a quit with nothing at stake (it just quits). A restart with
    /// nothing at stake keeps a short confirmation whose Return restarts (Will, 2026-10-26: "Restart Now as the default
    /// when nothing is running"); one that would stop work has no Return default, and unsaved or unreadable work refuses
    /// the restart. A quit can always go ahead (Quit Anyway): no state leaves Hooke unable to quit.
    static func prompt(_ leaving: Leaving, _ reading: Reading, version: String?, updateWaiting: Bool = false) -> Prompt? {
        let hooke = version.map { "Hooke \($0)" } ?? "The update"
        let installsOnQuit = updateWaiting ? " \(hooke) is downloaded and installs as Hooke quits." : ""
        switch (leaving, reading) {
        case (.restart, .unreadable):
            return Prompt(leaving: leaving, title: "Hooke can’t check your work right now",
                          text: "The workspace isn’t answering, so Hooke won’t restart yet. The update is downloaded and waits. "
                              + "Try again in a moment.", buttons: [ok], running: [], recheck: false)
        case (.restart, .read(let blockers, let running)) where !blockers.isEmpty:
            return Prompt(leaving: leaving, title: "Save your work before restarting",
                          text: blockers.joined(separator: "\n") + "\n\n\(hooke) is downloaded and waits.",
                          buttons: [ok], running: running, recheck: false)
        case (.restart, .read(_, let running)) where !running.isEmpty:
            return Prompt(leaving: leaving, title: "Restart Hooke and stop what’s running?",
                          text: "\(hooke) is downloaded. Restarting now stops:\n\(list(running))\n\n"
                              + "Choose Later and the update waits until you restart. Your Projects and data are kept.",
                          buttons: [Button(title: "Stop and Restart", answer: .proceed, destructive: true),
                                    Button(title: "Later", answer: .stay, isCancel: true)],
                          running: running, recheck: true)
        case (.restart, .read):
            return Prompt(leaving: leaving, title: "Restart Hooke to finish updating?",
                          text: "\(hooke) is downloaded and ready. Restarting usually takes less than a minute. "
                              + "Your Projects and data in \(savedWork) stay as they are.",
                          buttons: [Button(title: "Restart Now", answer: .proceed, isDefault: true),
                                    Button(title: "Later", answer: .stay, isCancel: true)],
                          running: [], recheck: true)
        case (.quit, .unreadable):
            return Prompt(leaving: leaving, title: "Hooke can’t check your work right now",
                          text: "The workspace isn’t answering, so Hooke can’t tell what quitting would stop. Quit Anyway "
                              + "stops whatever is running, and what isn’t saved may be lost." + installsOnQuit,
                          buttons: [Button(title: "Quit Anyway", answer: .proceed, destructive: true),
                                    Button(title: "Cancel", answer: .stay, isCancel: true)], running: [], recheck: false)
        case (.quit, .read(let blockers, let running)) where !blockers.isEmpty:
            return Prompt(leaving: leaving, title: "Save your work before quitting",
                          text: blockers.joined(separator: "\n")
                              + (running.isEmpty ? "" : "\n\nQuitting also stops:\n\(list(running))")
                              + "\n\nQuit Anyway and what isn’t saved may be lost." + installsOnQuit,
                          buttons: [Button(title: "Quit Anyway", answer: .proceed, destructive: true),
                                    Button(title: "Cancel", answer: .stay, isCancel: true)], running: running, recheck: false)
        case (.quit, .read(_, let running)) where !running.isEmpty:
            return Prompt(leaving: leaving, title: "Quit Hooke and stop what’s running?",
                          text: "Quitting now stops:\n\(list(running))\n\nYour Projects and data are kept." + installsOnQuit,
                          buttons: [Button(title: "Stop and Quit", answer: .proceed, destructive: true),
                                    Button(title: "Cancel", answer: .stay, isCancel: true)], running: running, recheck: true)
        case (.quit, .read):
            return nil
        }
    }

    /// After the person chose to go on: the latest reading lets it go on only when nothing is unsaved, it was readable,
    /// and nothing runs that the sheet did not name.
    static func stillAgreed(_ prompt: Prompt, _ latest: Reading) -> Bool {
        guard case .read(let blockers, let running) = latest, blockers.isEmpty else { return false }
        return Set(running).isSubset(of: Set(prompt.running))
    }

    /// The sheet: buttons in order, Return and Escape only where the prompt says, destructive ones marked.
    static func alert(_ prompt: Prompt) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = prompt.title
        alert.informativeText = prompt.text
        for spec in prompt.buttons {
            let button = alert.addButton(withTitle: spec.title)
            button.keyEquivalent = spec.isDefault ? "\r" : spec.isCancel ? "\u{1b}" : ""
            button.hasDestructiveAction = spec.destructive
        }
        return alert
    }

    /// The person's answer, by the button pressed.
    static func answer(_ prompt: Prompt, _ response: NSApplication.ModalResponse) -> Answer {
        let index = response.rawValue - NSApplication.ModalResponse.alertFirstButtonReturn.rawValue
        return prompt.buttons.indices.contains(index) ? prompt.buttons[index].answer : .stay
    }

    /// The log's line for a check: counts only, never the work's own words.
    static func logLine(_ leaving: Leaving, _ reading: Reading, recheck: Bool) -> String {
        let what = leaving == .restart ? "update restart" : "quit"
        switch reading {
        case .unreadable: return "\(what) \(recheck ? "recheck" : "check"): unreadable"
        case .read(let blockers, let running):
            return "\(what) \(recheck ? "recheck" : "check"): \(blockers.count) unsaved, \(running.count) running"
        }
    }

    /// Shown over the workspace while the reads run, so leaving never waits on nothing (Sparkle's window has closed).
    static func checkingView(_ leaving: Leaving) -> NSView {
        let hud = NSVisualEffectView()
        hud.material = .hudWindow
        hud.blendingMode = .withinWindow
        hud.state = .active
        hud.wantsLayer = true
        hud.layer?.cornerRadius = 12
        hud.layer?.masksToBounds = true
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.startAnimation(nil)
        let label = NSTextField(labelWithString: leaving == .restart ? "Checking your work before restarting…"
                                                                     : "Checking your work before quitting…")
        label.font = .systemFont(ofSize: 13)
        let stack = NSStackView(views: [spinner, label])
        stack.orientation = .horizontal
        stack.spacing = 10
        stack.edgeInsets = NSEdgeInsets(top: 14, left: 18, bottom: 14, right: 20)
        stack.translatesAutoresizingMaskIntoConstraints = false
        hud.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: hud.leadingAnchor), stack.trailingAnchor.constraint(equalTo: hud.trailingAnchor),
            stack.topAnchor.constraint(equalTo: hud.topAnchor), stack.bottomAnchor.constraint(equalTo: hud.bottomAnchor),
        ])
        return hud
    }

    /// The capabilities wire's setup rows still in progress (orchestrator capabilities_wire: setup.downloads, installs);
    /// nil when the answer is not that wire.
    static func activeWork(_ data: Data) -> [String]? {
        guard let wire = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let setup = wire["setup"] as? [String: Any],
              let downloads = setup["downloads"] as? [[String: Any]], let installs = setup["installs"] as? [[String: Any]]
        else { return nil }
        let going: Set<String> = ["queued", "downloading", "checking", "installing"]
        return (downloads + installs).compactMap { row in
            guard let state = row["state"] as? String, going.contains(state) else { return nil }
            let says = (row["says"] as? String) ?? (row["name"] as? String) ?? ""
            return says.isEmpty ? nil : says
        }
    }

    static func size(_ bytes: Int64) -> String {
        bytes >= 1 << 30 ? String(format: "%.2f GB", Double(bytes) / Double(1 << 30))
            : bytes >= 1 << 20 ? String(format: "%.1f MB", Double(bytes) / Double(1 << 20)) : "\(bytes) bytes"
    }

    /// After an update opens: what it did to storage, from what the launcher actually removed and kept (main.swift
    /// Services.start: earlier versions' runtime folders go; of other lockfiles' Pythons the newest stays, older ones go).
    static func afterUpdate(removedRuntime: Int64, removedPython: Int64, keptPython: Bool) -> String {
        var text = "Your Projects, saved results, datasets and recordings in \(savedWork) were not part of the update. "
            + "Prepared tools and downloaded models were kept."
        if removedRuntime > 0 { text += " App files from earlier versions (\(size(removedRuntime))) were removed." }
        if removedPython > 0 { text += " Older Python files that neither this version nor the previous one uses (\(size(removedPython))) were removed." }
        // The newest other Python is the previous version's when this update changed packages; when it didn't, the
        // previous version uses this one. Either way going back one version doesn't download Python again.
        if keptPython { text += " The previous version’s Python is kept, so going back a version doesn’t download it again." }
        return text
    }

    /// The disk a folder takes, its links counted as links (never followed).
    static func folderBytes(_ folder: URL) -> Int64 {
        let keys: [URLResourceKey] = [.totalFileAllocatedSizeKey, .isSymbolicLinkKey]
        var total: Int64 = 0
        for case let url as URL in FileManager.default.enumerator(at: folder, includingPropertiesForKeys: keys) ?? FileManager.DirectoryEnumerator() {
            total += Int64((try? url.resourceValues(forKeys: Set(keys)).totalFileAllocatedSize) ?? 0)
        }
        return total
    }

    /// This version was just installed by Sparkle and hasn't opened its workspace yet (Updates.willInstallUpdate wrote
    /// the version; sayWhatsNew clears it once the workspace opens).
    static func justUpdated(_ defaults: UserDefaults, version: String) -> Bool {
        (defaults.dictionary(forKey: "HookeUIWhatsNew") as? [String: String])?["version"] == version
    }

    /// The setup screen's words when this version cannot start just after an update.
    static func failedAfterUpdate(_ message: String) -> String {
        message + "\n\nThe update replaced only the app: your Projects and data in \(savedWork) are where they were. The previous version's files stay "
            + "on this Mac until this version opens. To go back, install the previous version from "
            + "github.com/wpulier/hookeui.org/releases."
    }
}
