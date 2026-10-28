import Foundation

// What the Mac app and the `hooke` command share (ADR 0026; services/orchestrator/door/AGENTS.md): the state file the
// app writes as it starts, the pages an agent may open in Hooke, and the "Open in Hooke" links. Compiled into both.

/// Where this copy of Hooke is, written by the app to its support folder (one per channel) and read by `hooke`.
struct AgentState: Codable, Equatable {
    var format = 1
    var channel: String
    var bundleId: String
    var urlScheme: String
    var version: String
    var build: String
    /// asking (the first launch's download waits for a click), downloading, starting, ready, failed, stopped.
    var phase: String
    var says: String
    var port: Int?
    var pid: Int32
    var at: Double

    static let file = "agent-state.json"
    static let phases: Set<String> = ["asking", "downloading", "starting", "ready", "failed", "stopped"]

    func write(to folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .prettyPrinted]
        try encoder.encode(self).write(to: folder.appendingPathComponent(AgentState.file), options: .atomic)
    }

    static func read(from folder: URL) -> AgentState? {
        guard let data = try? Data(contentsOf: folder.appendingPathComponent(file)),
              let state = try? JSONDecoder().decode(AgentState.self, from: data), state.format == 1,
              phases.contains(state.phase) else { return nil }
        return state
    }
}

enum AgentLinks {
    /// The pages an agent may open in the Hooke window: the same list as the door's (door/api.py PAGE).
    static func isPage(_ page: String) -> Bool {
        page.range(of: #"^#(measurements|models|toolbox|assets|sessions)(/[A-Za-z0-9_.-]+)?(\?[A-Za-z0-9=&._%-]*)?$"#,
                   options: .regularExpression) != nil
    }

    /// "Open in Hooke": this channel's scheme, opening only the page it names.
    static func link(scheme: String, page: String) -> String? {
        guard isPage(page) else { return nil }
        var parts = URLComponents()
        parts.scheme = scheme
        parts.host = "open"
        parts.queryItems = [URLQueryItem(name: "page", value: page)]
        return parts.string
    }

    /// The page a link names, or nil for anything that isn't this channel's open link to an allowed page.
    static func page(from url: URL, scheme: String) -> String? {
        guard url.scheme == scheme, url.host == "open",
              let page = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "page" })?.value,
              isPage(page) else { return nil }
        return page
    }
}
