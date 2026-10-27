// Checks of the launch screen (HookeUI/SetupWords.swift, drawn by HookeUI/SetupView.swift), run by `make native-test`
// (part of `make test` on a Mac with swiftc). Nothing here starts, downloads or installs anything.
//
//   make native-test
//   <the built executable> --draw OUTDIR     # the screen at each kind of launch, drawn to OUTDIR/*.png
import AppKit

var failures = 0
func expect(_ ok: Bool, _ what: String) {
    if !ok { failures += 1; print("FAIL: \(what)") } else { print("ok: \(what)") }
}

let firstLaunch = SetupFacts(runtimeInstalled: false, earlierRuntime: false, pythonReady: false, otherPython: false, warmed: false)
let sameSoftware = SetupFacts(runtimeInstalled: false, earlierRuntime: true, pythonReady: true, otherPython: false, warmed: true)
let changedSoftware = SetupFacts(runtimeInstalled: false, earlierRuntime: true, pythonReady: false, otherPython: true, warmed: false)
let ordinary = SetupFacts(runtimeInstalled: true, earlierRuntime: false, pythonReady: true, otherPython: true, warmed: true)
let measured = RuntimeDownload.read(Data(#"""
{"version": "0.4.1", "contentHash": "x", "runtimeDownload": {"bytes": 314883956, "packages": 126, "packageBytes": 299215810,
 "python": {"version": "3.13.5", "bytes": 15668146, "host": "github.com", "project": "astral-sh/python-build-standalone"},
 "diskBytes": 1200000000, "from": ["pypi.org"], "notable": ["PyTorch", "OpenCV", "SciPy", "NumPy"]}}
"""#.utf8))

func names(_ strip: SetupStrip) -> [String] { strip.labels.map(\.text) }

func planChecks() {
    let first = SetupPlan(firstLaunch)
    expect(first.stages == [.setUp, .download, .getReady, .open] && first.asksFirst && !first.update,
           "first launch: every stage, and it asks before the download")
    expect(names(SetupStrip(first, version: "0.4.1")) == ["1  Set up", "2  Download", "3  Get ready", "4  Open"],
           "first launch strip: Set up · Download · Get ready · Open")

    let same = SetupPlan(sameSoftware)
    let sameStrip = SetupStrip(same, version: "0.4.1")
    expect(same.stages == [.setUp, .open] && !same.asksFirst && same.update, "update, same software: Set up and Open only")
    expect(names(sameStrip) == ["1  Set up 0.4.1", "2  Open"] && sameStrip.shown, "update strip: Set up 0.4.1 · Open")
    let words = SetupWords.opening(same, product: SetupWords.product("HookeUI Beta"), version: "0.4.1")
    expect(words == ("Updating to Hooke Beta 0.4.1", "Setting up the new version. Nothing to download: its scientific software is unchanged."),
           "update, same software: says nothing is downloaded")

    let changed = SetupPlan(changedSoftware)
    expect(changed.stages == [.setUp, .download, .getReady, .open] && !changed.asksFirst && changed.update,
           "update, changed software: downloads without asking again (the update was accepted)")
    expect(SetupWords.download(first: changed.firstPython, runtime: measured).0 == "Downloading this version’s software changes",
           "update, changed software: says it downloads the changes")
    expect(!SetupWords.download(first: false, runtime: measured).1.contains("MB"), "the changes' size is not invented")
    expect(names(SetupStrip(changed, version: "0.4.1")) == ["1  Set up 0.4.1", "2  Download", "3  Get ready", "4  Open"],
           "update strip with a download")

    let plain = SetupPlan(ordinary)
    expect(plain.stages == [.open] && !plain.setsUp && !SetupStrip(plain, version: "0.4.1").shown,
           "ordinary launch: no stage strip")
    expect(SetupWords.opening(plain, product: "Hooke", version: "0.4.1") == ("Opening your workspace", ""),
           "ordinary launch: only Opening your workspace")

    let interrupted = SetupPlan(SetupFacts(runtimeInstalled: true, earlierRuntime: false, pythonReady: false, otherPython: false, warmed: false))
    expect(interrupted.stages == [.download, .getReady, .open] && interrupted.asksFirst,
           "a first download that never finished asks again")
    let downloadedNotLoaded = SetupPlan(SetupFacts(runtimeInstalled: true, earlierRuntime: false, pythonReady: true, otherPython: false, warmed: false))
    expect(downloadedNotLoaded.stages == [.getReady, .open] && !downloadedNotLoaded.asksFirst, "made but never loaded: Get ready only")
}

func tickChecks() {
    var strip = SetupStrip(SetupPlan(firstLaunch), version: "0.4.1")
    expect(!strip.labels.contains { $0.text.hasPrefix("✓") }, "nothing is ticked before a step reports")
    strip.start(.setUp)
    expect(strip.labels[0].current && strip.labels[0].text == "1  Set up", "the running stage is current, not ticked")
    strip.start(.download)
    expect(strip.labels[0].text == "1  Set up" && strip.labels[1].current, "starting a later stage ticks nothing earlier")
    strip.finish(.setUp, ran: true)
    expect(strip.labels[0].text == "✓  Set up" && strip.labels[0].accessibility == "Set up: done", "a stage that ran is ticked")
    strip.finish(.download, ran: false)
    expect(names(strip) == ["✓  Set up", "2  Get ready", "3  Open"], "a stage that had nothing to do is removed, never ticked")
    strip.start(.open)
    expect(names(strip) == ["✓  Set up", "2  Get ready", "3  Open"] && strip.labels[2].current,
           "Open does not tick Get ready, which never reported")
    strip.finish(.getReady, ran: false)
    strip.finish(.setUp, ran: false)  // a later report never takes a tick back
    expect(names(strip) == ["✓  Set up", "2  Open"] && strip.shown, "a tick stays")
    var skipped = SetupStrip(SetupPlan(sameSoftware), version: "0.4.1")
    skipped.finish(.setUp, ran: false)
    expect(names(skipped) == ["1  Open"] && !skipped.shown, "every stage skipped: no strip at all")
    var unplanned = SetupStrip(SetupPlan(ordinary), version: "0.4.1")
    unplanned.finish(.download, ran: true)
    expect(names(unplanned) == ["1  Open"], "a step that was not planned adds no stage")
}

func wordChecks() {
    let consent = SetupWords.consent(measured)
    expect(consent.title == "Hooke downloads its scientific software first", "the question's title")
    expect(consent.accept == "Download and Open" && consent.quit == "Quit", "Download and Open, or Quit")
    for fact in ["126 open-source packages", "such as PyTorch, OpenCV and SciPy",
                 "downloaded once from the Python Package Index (pypi.org), with Python itself from GitHub (astral-sh/python-build-standalone)", "about 310 MB to download", "about 1.2 GB on disk",
                 "kept in your Library", "Later versions reuse them.", "No account is needed",
                 "downloading sends none of your files or pictures", "Models are separate: you choose them in the workspace."] {
        expect(consent.text.contains(fact), "the question says: \(fact)")
    }
    let unmeasured = SetupWords.consent(nil)
    expect(!unmeasured.text.contains("MB") && !unmeasured.text.contains("GB") && unmeasured.text.contains("Models are separate"),
           "without the build's measurement, no number is said")
    expect(RuntimeDownload.read(Data(#"{"version": "0.4.0", "contentHash": "x"}"#.utf8)) == nil, "an older build's packaged.json has none")
    expect(SetupWords.download(first: true, runtime: measured).1 == "Python and 126 packages, about 310 MB, from pypi.org and GitHub.",
           "first download's words")
    expect(SetupWords.megabytes(55_953_586) == "56 MB" && SetupWords.megabytes(1_233_586_851) == "1.2 GB", "sizes as the workspace says them")

    let uv: [(String, String?)] = [
        ("Downloading cpython-3.13.5-macos-aarch64-none (download) (14.9MiB)", "Downloading Python 3.13.5 (14.9 MiB)"),
        (" Downloading cpython-3.13.5-macos-aarch64-none (download)", "Downloaded Python 3.13.5"),
        ("Using CPython 3.13.5", "Using Python 3.13.5"),
        ("Creating virtual environment at: /Users/x/Library/Application Support/HookeUI/envs/e35e", "Making Hooke’s Python environment"),
        ("Resolved 152 packages in 6ms", nil),
        ("Downloading torch (121.4MiB)", "Downloading torch (121.4 MiB)"),
        (" Downloading torch", "Downloaded torch"),
        ("Prepared 126 packages in 6.52s", "Prepared 126 packages"),
        ("Installed 1 package in 3ms", "Installed 1 package"),
        (" + torch==2.14.1", nil),
    ]
    for (raw, said) in uv { expect(SetupWords.uvLine(raw) == said, "uv's \(raw.debugDescription) is said as \(said ?? "nothing")") }

    // No word of the launch screen is the jargon it replaced, and none is a second welcome.
    var all = [consent.title, consent.text, unmeasured.text, SetupWords.getReady.0, SetupWords.getReady.1, SetupWords.openTitle,
               SetupWords.startingCamera, SetupWords.startingWorkspace, SetupWords.startingTools]
    for facts in [firstLaunch, sameSoftware, changedSoftware, ordinary] {
        let plan = SetupPlan(facts)
        let o = SetupWords.opening(plan, product: "Hooke Beta", version: "0.4.1"), s = SetupWords.setUp(plan, product: "Hooke Beta", version: "0.4.1")
        all += [o.0, o.1, s.0, s.1] + SetupStrip(plan, version: "0.4.1").labels.map(\.text)
    }
    for word in ["Welcome", "Initialize", "camera bridge", "Preparing", "studio", "350 MB"] {
        expect(!all.contains { $0.contains(word) }, "no \(word.debugDescription) on the launch screen")
    }
}

/// The screen at each kind of launch, as the app draws it, for looking at (not a check).
func draw(_ folder: URL) {
    _ = NSApplication.shared
    NSApp.appearance = NSAppearance(named: .darkAqua)
    try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
    func save(_ name: String, _ build: (SetupView) -> Void) {
        let view = SetupView()
        view.frame = NSRect(x: 0, y: 0, width: 1100, height: 720)
        view.appearance = NSAppearance(named: .darkAqua)
        build(view)
        view.layoutSubtreeIfNeeded()
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        let out = folder.appendingPathComponent("\(name).png")
        try? rep.representation(using: .png, properties: [:])?.write(to: out)
        print("drawn: \(out.path)")
    }
    final class Target: NSObject { @objc func accept() {} }
    let target = Target()
    save("1-first-launch-question") { view in
        view.plan(SetupPlan(firstLaunch), version: "0.4.1")
        view.ask(SetupWords.consent(measured), target: target, accept: #selector(Target.accept))
    }
    save("2-first-launch-downloading") { view in
        let plan = SetupPlan(firstLaunch)
        view.plan(plan, version: "0.4.1")
        let s = SetupWords.setUp(plan, product: "Hooke Beta", version: "0.4.1")
        view.show(s.0, s.1, .prepare)
        view.finish(.prepare, ran: true)
        let d = SetupWords.download(first: true, runtime: measured)
        view.show(d.0, SetupWords.progress(d.1, SetupWords.uvLine("Downloading torch (121.4MiB)")), .download)
    }
    save("3-first-launch-get-ready") { view in
        view.plan(SetupPlan(firstLaunch), version: "0.4.1")
        view.finish(.prepare, ran: true)
        view.finish(.download, ran: true)
        view.show(SetupWords.getReady.0, SetupWords.getReady.1, .initialize)
    }
    save("4-update-same-software") { view in
        let plan = SetupPlan(sameSoftware)
        view.plan(plan, version: "0.4.1")
        let o = SetupWords.opening(plan, product: "Hooke Beta", version: "0.4.1")
        view.show(o.0, o.1, .prepare)
    }
    save("5-update-same-software-opening") { view in
        view.plan(SetupPlan(sameSoftware), version: "0.4.1")
        view.finish(.prepare, ran: true)
        view.show(SetupWords.openTitle, SetupWords.startingCamera, .open)
    }
    save("6-update-changed-software") { view in
        let plan = SetupPlan(changedSoftware)
        view.plan(plan, version: "0.4.1")
        view.finish(.prepare, ran: true)
        let d = SetupWords.download(first: false, runtime: measured)
        view.show(d.0, SetupWords.progress(d.1, SetupWords.uvLine("Downloading h5py (2.9MiB)")), .download)
    }
    save("7-ordinary-launch") { view in
        view.plan(SetupPlan(ordinary), version: "0.4.1")
        view.show(SetupWords.openTitle, SetupWords.startingWorkspace, .open)
    }
}

let args = CommandLine.arguments
if args.count >= 3, args[1] == "--draw" {
    draw(URL(fileURLWithPath: args[2]))
} else {
    planChecks()
    tickChecks()
    wordChecks()
    print(failures == 0 ? "all setup words checks passed" : "\(failures) failed")
    exit(failures == 0 ? 0 : 1)
}
