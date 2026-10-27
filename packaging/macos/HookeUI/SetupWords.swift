import Foundation

// What the launch screen says and which stages it shows, apart from the app so `make native-test` checks it on its own
// (packaging/macos/tests/setup-words). main.swift's Services reads the facts and runs the steps; SetupView.swift shows
// what this file decides.
//
// The rule (ADR 0006, never claim what did not happen): a launch is planned before anything is shown, from the same
// marker files each step checks. Only planned stages appear. A stage is ticked only when its step reports that it ran;
// a planned stage whose step turns out to have nothing to do is removed, never ticked. A launch with nothing to set up
// shows no stages at all.

/// What a launch is planned from, read before anything is shown (Services.facts).
struct SetupFacts: Equatable {
    /// This build's copy of the app is already in runtime/ (its .complete marker).
    var runtimeInstalled: Bool
    /// Another version's copy is there: this launch is an update.
    var earlierRuntime: Bool
    /// The Python of the bundle's own lockfile is made (envs/<lock>/: its lock marker and bin/python).
    var pythonReady: Bool
    /// A finished Python of another lockfile is there (an earlier version's).
    var otherPython: Bool
    /// The Python of the bundle's lockfile has been loaded once (its warm marker).
    var warmed: Bool
}

enum SetupStage: String, CaseIterable {
    case setUp, download, getReady, open
}

struct SetupPlan: Equatable {
    let stages: [SetupStage]
    let update: Bool
    /// No finished Python of any lockfile on this Mac: its download is the first one.
    let firstPython: Bool

    init(_ facts: SetupFacts) {
        var stages: [SetupStage] = []
        if !facts.runtimeInstalled { stages.append(.setUp) }
        if !facts.pythonReady { stages.append(.download) }
        if !facts.warmed { stages.append(.getReady) }
        stages.append(.open)
        self.stages = stages
        update = facts.earlierRuntime
        firstPython = !facts.pythonReady && !facts.otherPython
    }

    /// The required download is asked for once, before it starts: only when there is no Python on this Mac yet, so an
    /// update (whose offer the person already accepted) never waits on the question.
    var asksFirst: Bool { stages.contains(.download) && firstPython }
    /// Anything to set up before opening: the stage strip is shown only then.
    var setsUp: Bool { stages != [.open] }
}

/// The stage strip under the words. Built from the plan; changed only by what the steps report.
struct SetupStrip: Equatable {
    enum State: Equatable { case waiting, current, done }
    struct Item: Equatable {
        let stage: SetupStage
        let name: String
        var state: State
    }
    struct Label: Equatable {
        let text: String
        let current: Bool
        let accessibility: String
    }

    private(set) var items: [Item]

    init(_ plan: SetupPlan, version: String) {
        items = plan.stages.map { Item(stage: $0, name: SetupWords.stageName($0, update: plan.update, version: version), state: .waiting) }
    }

    /// Nothing to show for a launch that only opens (or a strip whose other stages all turned out to have nothing to do).
    var shown: Bool { items.contains { $0.stage != .open } }

    /// The stage whose step is running now. No other stage changes: a stage is never ticked for being earlier.
    mutating func start(_ stage: SetupStage) {
        for index in items.indices where items[index].state == .current { items[index].state = .waiting }
        if let index = items.firstIndex(where: { $0.stage == stage }) { items[index].state = .current }
    }

    /// A step ended: ticked when it ran, removed when it had nothing to do. A tick stays.
    mutating func finish(_ stage: SetupStage, ran: Bool) {
        guard let index = items.firstIndex(where: { $0.stage == stage }), items[index].state != .done else { return }
        if ran { items[index].state = .done } else { items.remove(at: index) }
    }

    var labels: [Label] {
        items.enumerated().map { index, item in
            Label(text: "\(item.state == .done ? "✓" : String(index + 1))  \(item.name)", current: item.state == .current,
                  accessibility: "\(item.name): \(item.state == .done ? "done" : item.state == .current ? "in progress" : "waiting")")
        }
    }
}

/// The app's own Python as the build measured it (packaging/macos/runtime_download.py writes it into packaged.json).
struct RuntimeDownload: Equatable {
    var bytes: Int64
    var packages: Int
    var diskBytes: Int64
    var registries: [String]
    var notable: [String]
    var pythonHost: String
    var pythonProject: String

    /// packaged.json's `runtimeDownload`, or nil when the build did not measure it (then no number is said).
    static func read(_ packaged: Data) -> RuntimeDownload? {
        guard let json = try? JSONSerialization.jsonObject(with: packaged) as? [String: Any],
              let d = json["runtimeDownload"] as? [String: Any],
              let bytes = (d["bytes"] as? NSNumber)?.int64Value, bytes > 0,
              let packages = (d["packages"] as? NSNumber)?.intValue, packages > 0,
              let disk = (d["diskBytes"] as? NSNumber)?.int64Value, disk > 0 else { return nil }
        let python = d["python"] as? [String: Any] ?? [:]
        return RuntimeDownload(bytes: bytes, packages: packages, diskBytes: disk,
                               registries: d["from"] as? [String] ?? [], notable: d["notable"] as? [String] ?? [],
                               pythonHost: python["host"] as? String ?? "", pythonProject: python["project"] as? String ?? "")
    }
}

/// Every word of the launch screen (sentence case, plain: docs/design/2026-10-20-hooke-words.md).
enum SetupWords {
    struct Consent: Equatable {
        let title: String
        let text: String
        let accept: String
        let quit: String
    }

    /// The product as people know it: "HookeUI Beta" (the bundle's name) is Hooke Beta.
    static func product(_ bundleName: String) -> String {
        bundleName.replacingOccurrences(of: "HookeUI", with: "Hooke")
    }

    static func stageName(_ stage: SetupStage, update: Bool, version: String) -> String {
        switch stage {
        case .setUp: return update ? "Set up \(version)" : "Set up"
        case .download: return "Download"
        case .getReady: return "Get ready"
        case .open: return "Open"
        }
    }

    /// The words shown first, before any step says its own (the native screen never says Welcome: the workspace's
    /// first-launch Welcome is the one welcome).
    static func opening(_ plan: SetupPlan, product: String, version: String) -> (String, String) {
        switch plan.stages.first ?? .open {
        case .setUp: return setUp(plan, product: product, version: version)
        case .download: return download(first: plan.firstPython, runtime: nil)
        case .getReady: return getReady
        case .open: return (openTitle, "")
        }
    }

    static func setUp(_ plan: SetupPlan, product: String, version: String) -> (String, String) {
        guard plan.update else { return ("Setting up \(product)", "Copying the app’s files into your Library.") }
        return ("Updating to \(product) \(version)", plan.stages.contains(.download)
            ? "Setting up the new version. Its scientific software changed: only what changed is downloaded."
            : "Setting up the new version. Nothing to download: its scientific software is unchanged.")
    }

    static func download(first: Bool, runtime: RuntimeDownload?) -> (String, String) {
        guard first else { return ("Downloading this version’s software changes", "Only what changed is downloaded; the rest is reused.") }
        guard let runtime else { return ("Downloading Hooke’s scientific software", "Python and its open-source packages, once.") }
        return ("Downloading Hooke’s scientific software",
                "Python and \(runtime.packages) packages, about \(megabytes(runtime.bytes)), from \(hosts(runtime, short: true)).")
    }

    /// The download's words with uv's latest report under them.
    static func progress(_ detail: String, _ reported: String?) -> String {
        guard let reported, !reported.isEmpty else { return detail }
        return "\(detail)\n\(reported)"
    }

    static let getReady = ("Getting the software ready",
                           "Each package is loaded once while macOS checks it. This happens only now and when the software changes.")
    static let openTitle = "Opening your workspace"
    static let startingCamera = "Starting the camera connection."
    static let startingWorkspace = "Starting the workspace."
    static let startingTools = "Starting your analysis tools."

    /// The one question of a first launch, before the required download (Will, 2026-10-26). Only measured facts: with no
    /// measurement in this build, no number is said.
    static func consent(_ runtime: RuntimeDownload?) -> Consent {
        var text: String
        if let runtime {
            let such = runtime.notable.prefix(3)
            let names = such.count > 1 ? such.dropLast().joined(separator: ", ") + " and " + such.last! : such.first ?? ""
            text = "Hooke runs on Python and \(runtime.packages) open-source packages\(names.isEmpty ? "" : ", such as \(names)"). "
                + "They are downloaded once from \(hosts(runtime, short: false)), and kept in your Library: about "
                + "\(megabytes(runtime.bytes)) to download, about \(gigabytes(runtime.diskBytes)) on disk."
        } else {
            text = "Hooke runs on Python and open-source scientific packages. They are downloaded once and kept in your Library."
        }
        text += " Later versions reuse them. No account is needed, and downloading sends none of your files or pictures. "
            + "Models are separate: you choose them in the workspace."
        return Consent(title: "Hooke downloads its scientific software first", text: text, accept: "Download and Open", quit: "Quit")
    }

    /// Where the packages and Python come from, as the build read them: "pypi.org and GitHub", or in full "the Python
    /// Package Index (pypi.org), with Python itself from GitHub (astral-sh/python-build-standalone)".
    static func hosts(_ runtime: RuntimeDownload, short: Bool) -> String {
        let registries = runtime.registries.map { host -> String in
            host == "pypi.org" && !short ? "the Python Package Index (pypi.org)" : host
        }
        let github = runtime.pythonHost == "github.com"
        let python = runtime.pythonHost.isEmpty ? "" : github ? "GitHub" : runtime.pythonHost
        func list(_ items: [String]) -> String {
            items.count > 1 ? items.dropLast().joined(separator: ", ") + " and " + items.last! : items.first ?? ""
        }
        if short { return list(registries + (python.isEmpty ? [] : [python])) }
        let packages = list(registries)
        guard !python.isEmpty else { return packages }
        let whole = python + (runtime.pythonProject.isEmpty ? "" : " (\(runtime.pythonProject))")
        return packages.isEmpty ? whole : "\(packages), with Python itself from \(whole)"
    }

    /// uv's own report of one line, in plain words, or nil for lines the screen does not show. Sizes are uv's.
    static func uvLine(_ raw: String) -> String? {
        let finished = raw.hasPrefix(" ")  // uv 0.7 prints a finished download again, indented and without its size
        let line = raw.trimmingCharacters(in: .whitespaces)
        func groups(_ pattern: String) -> [String]? {
            guard let regex = try? NSRegularExpression(pattern: pattern),
                  let match = regex.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)) else { return nil }
            return (1..<match.numberOfRanges).map { Range(match.range(at: $0), in: line).map { String(line[$0]) } ?? "" }
        }
        func size(_ uv: String) -> String { uv.replacingOccurrences(of: "MiB", with: " MiB").replacingOccurrences(of: "GiB", with: " GiB")
            .replacingOccurrences(of: "KiB", with: " KiB") }
        if let g = groups(#"^Downloading cpython-(\d+\.\d+\.\d+)\S* \(download\)(?: \((.+)\))?$"#) {
            return finished || g[1].isEmpty ? "Downloaded Python \(g[0])" : "Downloading Python \(g[0]) (\(size(g[1])))"
        }
        if let g = groups(#"^Downloading (\S+)(?: \((.+)\))?$"#) {
            return finished || g[1].isEmpty ? "Downloaded \(g[0])" : "Downloading \(g[0]) (\(size(g[1])))"
        }
        if let g = groups(#"^Using CPython (\S+)"#) { return "Using Python \(g[0])" }
        if line.hasPrefix("Creating virtual environment") { return "Making Hooke’s Python environment" }
        if let g = groups(#"^Prepared (\d+) packages?"#) { return "Prepared \(g[0]) package\(g[0] == "1" ? "" : "s")" }
        if let g = groups(#"^Installed (\d+) packages?"#) { return "Installed \(g[0]) package\(g[0] == "1" ? "" : "s")" }
        return nil
    }

    /// "about 310 MB": decimal megabytes as the workspace says sizes (size-words.ts), to the nearest 10 above 100 MB.
    static func megabytes(_ bytes: Int64) -> String {
        let mb = Double(bytes) / 1e6
        if mb >= 1000 { return gigabytes(bytes) }
        return "\(mb >= 100 ? Int((mb / 10).rounded()) * 10 : max(1, Int(mb.rounded()))) MB"
    }

    static func gigabytes(_ bytes: Int64) -> String { String(format: "%.1f GB", Double(bytes) / 1e9) }
}
