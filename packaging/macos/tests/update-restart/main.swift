// Checks of leaving Hooke (restarting into an update, or quitting) and what updating says about storage
// (HookeUI/UpdateWords.swift), run by `make native-test` (part of `make test` on a Mac with swiftc). Nothing here
// installs, restarts, quits or touches a feed: the reads, the sheet's answers and the quit are stand-ins.
//
//   make native-test
//   <the built executable> --draw DIR
//       # every sheet the app can show (and the panel shown while it reads) drawn to DIR/<case>.png, as the app draws
//       # them, with each prompt's words printed (off-screen windows, captured with screencapture -l)
import AppKit
import WebKit

var failures = 0
func expect(_ ok: Bool, _ what: String) {
    if !ok { failures += 1; print("FAIL: \(what)") } else { print("ok: \(what)") }
}

/// Runs the main run loop until `done()` or the deadline, as the app's own main thread would. `mode` .modalPanel is how
/// AppKit runs it while a quit waits for its answer (terminateLater).
func wait(_ seconds: Double, mode: RunLoop.Mode = .default, until done: () -> Bool) {
    let deadline = Date().addingTimeInterval(seconds)
    while !done() && Date() < deadline { RunLoop.main.run(mode: mode, before: Date().addingTimeInterval(0.02)) }
}

let version = "0.4.1"
// As apps/studio/src/restart-readiness.ts says them.
let note = "A Project note is unsaved or has a conflict. Save it, or resolve the conflict."
let disconnected = "The workspace has lost its connection to Hooke, so Hooke can’t tell whether your work is saved. Wait until it reconnects."
let download = "Downloading DINOv2 small"
let record = "Record (it carries on when Hooke opens again)"
let researcher = "Researcher 2, “Counting stomata” (it shows as interrupted when Hooke opens again)"
let many = [download, record, researcher, "Cellpose-SAM, running in Blood smear", "Your voice conversation",
            "An answer the Assistant is still writing", "Writing a message you haven’t sent (it may not come back)",
            "The tool session for “Cells.tif” (what you haven’t saved in it is lost)"]

/// Every sheet the app can show, by name (the drawn PNG's name).
let cases: [(name: String, leaving: Leaving, reading: Reading, updateWaiting: Bool)] = [
    ("restart-a-nothing-running", .restart, .read(blockers: [], running: []), true),
    ("restart-b-download", .restart, .read(blockers: [], running: [download]), true),
    ("restart-b-record", .restart, .read(blockers: [], running: [record]), true),
    ("restart-b-researcher", .restart, .read(blockers: [], running: [researcher]), true),
    ("restart-b-eight-things", .restart, .read(blockers: [], running: many), true),
    ("restart-c-unsaved-note", .restart, .read(blockers: [note], running: [download]), true),
    ("restart-c-disconnected", .restart, .read(blockers: [disconnected], running: []), true),
    ("restart-unreadable", .restart, .unreadable, true),
    ("quit-b-running-update-waiting", .quit, .read(blockers: [], running: [record, researcher]), true),
    ("quit-b-running", .quit, .read(blockers: [], running: [download]), false),
    ("quit-c-unsaved", .quit, .read(blockers: [note], running: [record]), true),
    ("quit-unreadable", .quit, .unreadable, true),
]

func prompt(_ leaving: Leaving, _ reading: Reading, _ updateWaiting: Bool = true) -> UpdateWords.Prompt? {
    UpdateWords.prompt(leaving, reading, version: version, updateWaiting: updateWaiting)
}
func titles(_ alert: NSAlert) -> [String] { alert.buttons.map(\.title) }
func returnKey(_ alert: NSAlert) -> [String] { alert.buttons.filter { $0.keyEquivalent == "\r" }.map(\.title) }
func escapeKey(_ alert: NSAlert) -> [String] { alert.buttons.filter { $0.keyEquivalent == "\u{1b}" }.map(\.title) }
func destructive(_ alert: NSAlert) -> [String] { alert.buttons.filter(\.hasDestructiveAction).map(\.title) }

func sheetChecks() {
    // A: nothing unsaved, nothing running. Return restarts (Will, 2026-10-26).
    let a = prompt(.restart, .read(blockers: [], running: []))!
    expect(a.title == "Restart Hooke to finish updating?", "A: the title says what restarting does")
    expect(a.text == "Hooke 0.4.1 is downloaded and ready. Restarting usually takes less than a minute. "
           + "Your Projects and data in \(UpdateWords.savedWork) stay as they are.", "A: the version, the time measured (9-35 s) and the data folder")
    let aAlert = UpdateWords.alert(a)
    expect(titles(aAlert) == ["Restart Now", "Later"] && returnKey(aAlert) == ["Restart Now"] && escapeKey(aAlert) == ["Later"]
           && destructive(aAlert).isEmpty, "A: Restart Now is the default (Return), Later is Escape")
    expect(a.recheck && a.running.isEmpty, "A: restarting reads again first")

    // B: something running, nothing unsaved. No Return default: an accidental Return never stops work.
    for (lines, what) in [([download], "a download"), ([record], "Record"), ([researcher], "a researcher")] {
        let b = prompt(.restart, .read(blockers: [], running: lines))!
        let bAlert = UpdateWords.alert(b)
        expect(b.title == "Restart Hooke and stop what’s running?" && b.text == "Hooke 0.4.1 is downloaded. Restarting now stops:\n• \(lines[0])\n\n"
               + "Choose Later and the update waits until you restart. Your Projects and data are kept.", "B with \(what): names it in its own words")
        expect(titles(bAlert) == ["Stop and Restart", "Later"] && returnKey(bAlert).isEmpty && escapeKey(bAlert) == ["Later"]
               && destructive(bAlert) == ["Stop and Restart"], "B with \(what): no Return default, Later on Escape, Stop and Restart destructive")
    }
    let eight = prompt(.restart, .read(blockers: [], running: many))!
    expect(eight.text.contains("• \(many[5])\nand 2 more\n\n") && !eight.text.contains(many[6]) && eight.running == many,
           "B: six lines, then how many more; the recheck still knows all eight")
    expect(!eight.text.contains("Keep Working") && !eight.text.contains("does not automatically resume"), "B: none of the old words")

    // C: something unsaved refuses the restart; the update waits.
    let c = prompt(.restart, .read(blockers: [note], running: [download]))!
    expect(c.title == "Save your work before restarting" && c.text == "\(note)\n\nHooke 0.4.1 is downloaded and waits.", "C: the blocker, then the update waits")
    expect(titles(UpdateWords.alert(c)) == ["OK"] && !c.canProceed, "C: only OK, no restart")
    let off = prompt(.restart, .read(blockers: [disconnected], running: []))!
    expect(off.text.hasPrefix(disconnected) && !off.canProceed, "C disconnected: words that fit a lost connection, no restart")

    // Unreadable: never taken as nothing running.
    let u = prompt(.restart, .unreadable)!
    expect(u.title == "Hooke can’t check your work right now" && u.text == "The workspace isn’t answering, so Hooke won’t restart yet. "
           + "The update is downloaded and waits. Try again in a moment.", "unreadable: says it can't check and won't restart yet")
    expect(titles(UpdateWords.alert(u)) == ["OK"] && !u.canProceed, "unreadable: only OK")

    // Quits: one rule, an update waiting or not; nothing at stake just quits.
    expect(prompt(.quit, .read(blockers: [], running: []), true) == nil && prompt(.quit, .read(blockers: [], running: []), false) == nil,
           "a quit with nothing at stake asks nothing")
    let qb = prompt(.quit, .read(blockers: [], running: [record]), true)!, qbAlert = UpdateWords.alert(qb)
    expect(qb.title == "Quit Hooke and stop what’s running?" && qb.text == "Quitting now stops:\n• \(record)\n\nYour Projects and data are kept. "
           + "Hooke 0.4.1 is downloaded and installs as Hooke quits.", "quit B: names what stops; the waiting update installs as Hooke quits")
    expect(!prompt(.quit, .read(blockers: [], running: [record]), false)!.text.contains("installs"), "quit B without an update says nothing of one")
    let quitWords = prompt(.quit, .read(blockers: [], running: [download]), true)!.text
    expect(!quitWords.contains("opens again") && !quitWords.contains("relaunch"), "quit B never promises a relaunch (unverified)")
    expect(titles(qbAlert) == ["Stop and Quit", "Cancel"] && returnKey(qbAlert).isEmpty && escapeKey(qbAlert) == ["Cancel"]
           && destructive(qbAlert) == ["Stop and Quit"] && qb.recheck, "quit B: Cancel on Escape, Stop and Quit destructive, read again first")
    let qc = prompt(.quit, .read(blockers: [note], running: [record]))!
    expect(qc.title == "Save your work before quitting" && qc.text.hasPrefix(note) && qc.text.contains("Quitting also stops:\n• \(record)")
           && titles(UpdateWords.alert(qc)) == ["Quit Anyway", "Cancel"] && !qc.recheck, "quit C: the blocker, what stops, and Quit Anyway")
    let qu = prompt(.quit, .unreadable)!
    expect(qu.title == "Hooke can’t check your work right now" && titles(UpdateWords.alert(qu)) == ["Quit Anyway", "Cancel"],
           "quit unreadable: Quit Anyway is still there")
    let readings: [Reading] = [.unreadable, .read(blockers: [note], running: []), .read(blockers: [], running: [record]),
                               .read(blockers: [note], running: [record])]
    expect(readings.allSatisfy { prompt(.quit, $0)!.canProceed }, "no reading leaves Hooke unable to quit")
    expect(readings.filter { r in if case .read(let b, _) = r { return !b.isEmpty } else { return true } }
        .allSatisfy { !prompt(.restart, $0)!.canProceed }, "unsaved or unreadable work never offers the restart")
    expect(prompt(.restart, .read(blockers: [], running: []), true).map { $0.text.contains("Hooke 0.4.1") } == true
           && UpdateWords.prompt(.restart, .read(blockers: [], running: []), version: nil)!.text.hasPrefix("The update is downloaded"),
           "the version from willInstallUpdate, or plain words without one")

    // The answer is the button pressed, never its position's old meaning.
    expect(UpdateWords.answer(a, .alertFirstButtonReturn) == .proceed && UpdateWords.answer(a, .alertSecondButtonReturn) == .stay
           && UpdateWords.answer(eight, .alertFirstButtonReturn) == .proceed && UpdateWords.answer(eight, .alertSecondButtonReturn) == .stay
           && UpdateWords.answer(c, .alertFirstButtonReturn) == .stay && UpdateWords.answer(a, .abort) == .stay, "answers by button")

    // The recheck after a button press covers blockers and running work.
    let b = prompt(.restart, .read(blockers: [], running: [download]))!
    expect(UpdateWords.stillAgreed(b, .read(blockers: [], running: [download])) && UpdateWords.stillAgreed(b, .read(blockers: [], running: [])),
           "recheck: what the sheet named, or less, goes on")
    expect(!UpdateWords.stillAgreed(b, .read(blockers: [], running: [download, record])), "recheck: anything newly running asks again")
    expect(!UpdateWords.stillAgreed(b, .read(blockers: [note], running: [])) && !UpdateWords.stillAgreed(b, .unreadable),
           "recheck: anything unsaved, or unreadable, asks again")
    expect(!UpdateWords.stillAgreed(a, .read(blockers: [], running: [record])), "recheck: Restart Now with something now running asks again")
    expect(UpdateWords.logLine(.restart, .read(blockers: [note], running: many), recheck: true) == "update restart recheck: 1 unsaved, 8 running"
           && UpdateWords.logLine(.quit, .unreadable, recheck: false) == "quit check: unreadable", "the log counts, never the work's own words")
}

func wordChecks() {
    let wire = #"{"setup":{"downloads":[{"state":"downloading","says":"Downloading DINOv2 small"},{"state":"done","says":"X is downloaded"}],"installs":[{"state":"installing","says":"Installing Spot shape"},{"state":"failed","says":"no"}]}}"#
    expect(UpdateWords.activeWork(Data(wire.utf8)) == ["Downloading DINOv2 small", "Installing Spot shape"],
           "only work still in progress is named")
    expect(UpdateWords.activeWork(Data("not json".utf8)) == nil && UpdateWords.activeWork(Data(#"{"setup":{}}"#.utf8)) == nil,
           "an answer that is not the capabilities wire is unreadable, never nothing")
    let rows = (1...8).map { #"{"state":"queued","says":"Download \#($0)"}"# }.joined(separator: ",")
    expect(UpdateWords.activeWork(Data(#"{"setup":{"downloads":[\#(rows)],"installs":[]}}"#.utf8))?.count == 8, "every download is kept for the recheck")
    let after = UpdateWords.afterUpdate(removedRuntime: 3 << 20, removedPython: 0, keptPython: true)
    expect(after.contains("App files from earlier versions (3.0 MB) were removed") && after.contains("previous version’s Python is kept")
           && after.contains("doesn’t download it again") && !after.contains("starts at once") && !after.contains("Older Python"),
           "after an update: only what was really removed is said, and what keeping the Python saves")
    expect(UpdateWords.afterUpdate(removedRuntime: 0, removedPython: 5 << 20, keptPython: false).contains("Older Python files that neither this version nor the previous one uses (5.0 MB)"),
           "removed Pythons are said as what they were")
    expect(!UpdateWords.afterUpdate(removedRuntime: 0, removedPython: 0, keptPython: false).contains("removed"),
           "nothing removed, nothing said removed")
    expect(UpdateWords.failedAfterUpdate("It stopped.").contains("To go back"), "a failed start after updating says the way back")
}

/// main.swift's folderBytes (earlier versions' copies, said after an update) and justUpdated (the failure words).
func helperChecks() {
    let fm = FileManager.default
    let root = fm.temporaryDirectory.appendingPathComponent("hooke-update-restart-\(UUID().uuidString)")
    let folder = root.appendingPathComponent("runtime/0.1.1-abc"), outside = root.appendingPathComponent("outside.bin")
    try! fm.createDirectory(at: folder.appendingPathComponent("services"), withIntermediateDirectories: true)
    try! Data(repeating: 1, count: 300_000).write(to: folder.appendingPathComponent("services/a.bin"))
    try! Data(repeating: 2, count: 5_000_000).write(to: outside)
    try! fm.createSymbolicLink(at: folder.appendingPathComponent("link"), withDestinationURL: outside)
    let bytes = UpdateWords.folderBytes(folder)
    expect(bytes >= 300_000 && bytes < 1_000_000, "folderBytes counts the folder's files and never follows a link (\(bytes))")
    expect(UpdateWords.folderBytes(root.appendingPathComponent("missing")) == 0, "a missing folder takes nothing")
    try? fm.removeItem(at: root)

    let defaults = UserDefaults(suiteName: "hooke-update-restart-test")!
    defaults.removePersistentDomain(forName: "hooke-update-restart-test")
    expect(!UpdateWords.justUpdated(defaults, version: "0.1.3"), "no installed update: not just updated")
    defaults.set(["version": "0.1.3", "notes": ""], forKey: "HookeUIWhatsNew")
    expect(UpdateWords.justUpdated(defaults, version: "0.1.3"), "the version Sparkle just installed is just updated")
    expect(!UpdateWords.justUpdated(defaults, version: "0.1.2"), "another version is not")
    defaults.removePersistentDomain(forName: "hooke-update-restart-test")
}

/// LeaveFlow with scripted reads and answers: what it asks, in order, and whether Hooke goes or stays.
final class Script {
    var reads: [Reading]
    var answers: [UpdateWords.Answer]
    var asked: [UpdateWords.Prompt] = []
    var checking: [Leaving?] = []
    var outcome: String?
    var readCount = 0
    lazy var flow = LeaveFlow(
        read: { [unowned self] done in readCount += 1; done(reads.removeFirst()) },
        checking: { [unowned self] in checking.append($0) },
        ask: { [unowned self] prompt, answer in asked.append(prompt); answer(answers.removeFirst()) },
        proceed: { [unowned self] in outcome = "proceed \($0)" },
        stay: { [unowned self] in outcome = "stay \($0)" })
    init(_ reads: [Reading], _ answers: [UpdateWords.Answer]) { self.reads = reads; self.answers = answers }
    func run(_ leaving: Leaving, updateWaiting: Bool = true) -> Script {
        flow.start(leaving, version: version, updateWaiting: updateWaiting); return self
    }
}

func flowChecks() {
    let nothing = Reading.read(blockers: [], running: [])
    var s = Script([nothing, nothing], [.proceed]).run(.restart)
    expect(s.asked.map(\.title) == ["Restart Hooke to finish updating?"] && s.outcome == "proceed restart" && s.readCount == 2,
           "restart, nothing at stake: one confirmation, read again, then restart")
    expect(s.checking == [.restart, nil, .restart, nil], "the checking panel shows for each read and goes before the sheet")
    s = Script([nothing, .read(blockers: [], running: [record]), .read(blockers: [], running: [record])], [.proceed, .proceed]).run(.restart)
    expect(s.asked.map(\.title) == ["Restart Hooke to finish updating?", "Restart Hooke and stop what’s running?"] && s.outcome == "proceed restart",
           "Restart Now, then Record started: the recheck asks with Record named, and Stop and Restart goes on")
    s = Script([.read(blockers: [], running: [download]), .read(blockers: [], running: [download, researcher])], [.proceed, .stay]).run(.restart)
    expect(s.asked.count == 2 && s.asked[1].running == [download, researcher] && s.outcome == "stay restart",
           "Stop and Restart, then a researcher started: asks again naming both; Later stays")
    s = Script([.read(blockers: [], running: [download]), .read(blockers: [note], running: [download])], [.proceed, .stay]).run(.restart)
    expect(s.asked.map(\.title).last == "Save your work before restarting" && s.outcome == "stay restart", "Stop and Restart, then a note unsaved: refused")
    s = Script([.read(blockers: [], running: [download]), .unreadable], [.proceed, .stay]).run(.restart)
    expect(s.asked.map(\.title).last == "Hooke can’t check your work right now" && s.outcome == "stay restart", "Stop and Restart, then unreadable: refused")
    s = Script([.read(blockers: [], running: [download])], [.stay]).run(.restart)
    expect(s.outcome == "stay restart" && s.readCount == 1, "Later stays without reading again")
    s = Script([.read(blockers: [], running: [download]), .read(blockers: [], running: [])], [.proceed]).run(.restart)
    expect(s.outcome == "proceed restart", "what it named has finished meanwhile: the restart goes on")
    s = Script([nothing], []).run(.quit)
    expect(s.asked.isEmpty && s.outcome == "proceed quit", "a quit with nothing at stake quits without a sheet")
    s = Script([.read(blockers: [note], running: [])], [.proceed]).run(.quit)
    expect(s.outcome == "proceed quit" && s.readCount == 1, "Quit Anyway quits (no recheck: the person accepted the loss)")
    s = Script([.read(blockers: [], running: [record]), nothing], [.proceed]).run(.quit)
    expect(s.outcome == "proceed quit" && s.readCount == 2, "Stop and Quit reads again, then quits")
    s = Script([.unreadable], [.stay]).run(.quit)
    expect(s.outcome == "stay quit", "Cancel stays")

    // One at a time: a second request while the first reads is turned away; the first finishes.
    var pending: ((Reading) -> Void)?
    var outcome = ""
    let flow = LeaveFlow(read: { pending = $0 }, checking: { _ in }, ask: { _, answer in answer(.proceed) },
                         proceed: { outcome = "proceed \($0)" }, stay: { outcome = "stay \($0)" })
    expect(flow.start(.quit, version: nil, updateWaiting: false) && flow.busy, "a quit starts its check")
    expect(!flow.start(.restart, version: version, updateWaiting: true), "a second request while the sheet is open is turned away")
    pending?(.read(blockers: [], running: []))
    expect(outcome == "proceed quit" && !flow.busy, "the first finishes, and the next may start")
}

/// RestartReads: both reads combined once, in either order; no workspace window or an unreadable read is unreadable.
func readChecks() {
    var readings: [Reading] = []
    var finishWorkspace: ((RestartReads.Workspace?) -> Void)?, finishActive: (([String]?) -> Void)?
    RestartReads.combine(readWorkspace: { finishWorkspace = $0 }, readActive: { finishActive = $0 }) { readings.append($0) }
    finishActive?(["Installing Spot shape"])
    wait(0.5) { false }
    expect(readings.isEmpty, "no reading until the workspace has answered too")
    finishWorkspace?(RestartReads.Workspace(blockers: [], running: [record]))
    wait(2) { !readings.isEmpty }
    expect(readings == [.read(blockers: [], running: [record, "Installing Spot shape"])], "one reading with both answers, whichever comes last")
    readings = []
    RestartReads.combine(readWorkspace: { $0(RestartReads.Workspace(blockers: [], running: [])) }, readActive: { $0(nil) }) { readings.append($0) }
    wait(2) { !readings.isEmpty }
    expect(readings == [.unreadable], "downloads unreadable: the whole reading is unreadable, never nothing running")

    let reads = RestartReads(webView: { nil }, origin: { nil })
    var reading: Reading?
    let started = Date()
    reads.read { reading = $0 }
    wait(6) { reading != nil }
    expect(reading == .unreadable && Date().timeIntervalSince(started) < 1, "no workspace window cannot certify anything, and says so at once")

    expect(RestartReads.workspace(["blockers": [], "running": ["a"]]) == RestartReads.Workspace(blockers: [], running: ["a"])
           && RestartReads.workspace(["blockers": []]) == nil && RestartReads.workspace(NSNull()) == nil, "the workspace's answer has both lists")
}

/// The real bridge: a page's hookeUpdateReadiness, read as the app reads it (callAsyncJavaScript, bounded).
func pageChecks() {
    let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 400, height: 300))
    func page(_ script: String, mode: RunLoop.Mode = .default) -> RestartReads.Workspace?? {
        var loaded = false
        final class Loaded: NSObject, WKNavigationDelegate { var done: () -> Void = {}; func webView(_ w: WKWebView, didFinish n: WKNavigation!) { done() } }
        let delegate = Loaded(); delegate.done = { loaded = true }
        web.navigationDelegate = delegate
        web.loadHTMLString("<script>\(script)</script>", baseURL: URL(string: "http://127.0.0.1:1/"))
        wait(10) { loaded }
        let reads = RestartReads(webView: { web }, origin: { nil }, timeout: 1)
        var answer: RestartReads.Workspace??
        reads.readWorkspace { answer = .some($0) }
        wait(5, mode: mode) { answer != nil }
        return withExtendedLifetime(delegate) { answer }
    }
    expect(page("window.hookeUpdateReadiness = async () => ({blockers: [], running: ['Record (it carries on when Hooke opens again)']});")
           == .some(RestartReads.Workspace(blockers: [], running: [record])), "an asynchronous answer is read")
    expect(page("window.hookeUpdateReadiness = () => new Promise(r => setTimeout(() => r({blockers: ['A note'], running: []}), 300));")
           == .some(RestartReads.Workspace(blockers: ["A note"], running: [])), "an answer that takes a moment is waited for")
    expect(page("window.hookeUpdateReadiness = async () => { throw new Error('/api/research could not be read.'); };") == .some(nil),
           "a read that fails is unreadable")
    expect(page("") == .some(nil), "a page without the read is unreadable")
    expect(page("window.hookeUpdateReadiness = async () => ({blockers: []});") == .some(nil), "an answer without its running work is unreadable")
    let started = Date()
    expect(page("window.hookeUpdateReadiness = () => new Promise(() => {});") == .some(nil) && Date().timeIntervalSince(started) < 4,
           "an answer that never comes is unreadable once its time is up")
    // A plain quit reads while AppKit waits for its answer: only the modal panel mode runs then.
    expect(page("window.hookeUpdateReadiness = () => new Promise(r => setTimeout(() => r({blockers: [], running: []}), 200));", mode: .modalPanel)
           == .some(RestartReads.Workspace(blockers: [], running: [])), "the read answers while a quit waits (modal panel run loop mode)")
}

/// The orchestrator's capabilities read: only a 200 with the wire is read.
final class Capabilities: URLProtocol {
    override class func canInit(with request: URLRequest) -> Bool { request.url?.host?.hasSuffix("update-restart.test") == true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let host = request.url!.host!
        let status = host.hasPrefix("down.") ? 503 : 200
        let body = host.hasPrefix("odd.") ? "<html>" : #"{"setup":{"downloads":[{"state":"downloading","says":"Downloading DINOv2 small"}],"installs":[]}}"#
        client?.urlProtocol(self, didReceive: HTTPURLResponse(url: request.url!, statusCode: status, httpVersion: nil, headerFields: nil)!,
                            cacheStoragePolicy: .notAllowed)
        client?.urlProtocol(self, didLoad: Data(body.utf8))
        client?.urlProtocolDidFinishLoading(self)
    }
    override func stopLoading() {}
}

func capabilityChecks() {
    URLProtocol.registerClass(Capabilities.self)
    func active(_ host: String) -> [String]?? {
        var answer: [String]??
        RestartReads(webView: { nil }, origin: { URL(string: "http://\(host)/") }).readActiveWork { answer = .some($0) }
        wait(5, mode: .modalPanel) { answer != nil }
        return answer
    }
    expect(active("ok.update-restart.test") == .some([download]), "the orchestrator's downloads are read")
    expect(active("down.update-restart.test") == .some(nil), "an orchestrator that answers with an error is unreadable")
    expect(active("odd.update-restart.test") == .some(nil), "an answer that is not the wire is unreadable")
    URLProtocol.unregisterClass(Capabilities.self)
}

/// Every sheet, as the app draws it on its dark window, and the panel shown while reading. This macOS draws an alert's
/// words and buttons only in a window on screen, so each is shown off screen (never where anyone sees it) and captured
/// by the window server (`screencapture -l`). An alert of an app in the background draws its Return button grey: which
/// button Return presses is printed beside each picture, from the button itself.
func draw(to folder: URL) {
    try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    func capture(_ window: NSWindow, _ name: String) {
        window.setFrameOrigin(NSPoint(x: -8000, y: -8000))
        window.orderFrontRegardless()
        wait(0.4) { false }
        let url = folder.appendingPathComponent("\(name).png")
        let shot = Process()
        shot.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        shot.arguments = ["-x", "-o", "-l", String(window.windowNumber), url.path]
        try? shot.run(); shot.waitUntilExit()
        window.orderOut(nil)
        if shot.terminationStatus == 0 && FileManager.default.fileExists(atPath: url.path) { print("drawn: \(url.path)") }
        else { print("FAIL: drawing \(name)"); failures += 1 }
    }
    for item in cases {
        guard let p = UpdateWords.prompt(item.leaving, item.reading, version: version, updateWaiting: item.updateWaiting) else { continue }
        let alert = UpdateWords.alert(p)
        alert.window.appearance = NSAppearance(named: .darkAqua)
        alert.layout()
        print("\n[\(item.name)]\ntitle: \(p.title)\ntext:\n\(p.text)\nbuttons (right to left): \(p.buttons.map(\.title)); "
              + "Return: \(returnKey(alert)); Escape: \(escapeKey(alert)); destructive: \(destructive(alert))")
        capture(alert.window, item.name)
    }
    for leaving in [Leaving.restart, .quit] {
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 420, height: 120), styleMask: [.titled, .fullSizeContentView],
                              backing: .buffered, defer: false)
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(calibratedWhite: 0.09, alpha: 1)
        let panel = UpdateWords.checkingView(leaving)
        panel.translatesAutoresizingMaskIntoConstraints = false
        window.contentView!.addSubview(panel)
        NSLayoutConstraint.activate([panel.centerXAnchor.constraint(equalTo: window.contentView!.centerXAnchor),
                                     panel.centerYAnchor.constraint(equalTo: window.contentView!.centerYAnchor)])
        capture(window, "checking-\(leaving == .restart ? "restart" : "quit")")
    }
}

let args = CommandLine.arguments
let app = NSApplication.shared
// Drawing needs windows (off screen); the checks need none.
app.setActivationPolicy(args.count >= 3 && args[1] == "--draw" ? .accessory : .prohibited)
if args.count >= 3, args[1] == "--draw" {
    draw(to: URL(fileURLWithPath: args[2]))
    exit(failures == 0 ? 0 : 1)
}
sheetChecks()
wordChecks()
helperChecks()
flowChecks()
readChecks()
pageChecks()
capabilityChecks()
print(failures == 0 ? "all update restart checks passed" : "\(failures) failed")
exit(failures == 0 ? 0 : 1)
