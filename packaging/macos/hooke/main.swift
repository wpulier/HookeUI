// hooke: the command a researcher's own coding agent runs to use the Hooke app it ships in (ADR 0026;
// services/orchestrator/door/AGENTS.md). It lives in the app bundle (Contents/Helpers/hooke) and talks only to that
// app, so Stable and Beta never mix. It holds no scientific logic and no list of capabilities: everything it can do it
// asks the app's door for. Compiled with ../HookeUI/AgentShared.swift.

import CommonCrypto
import Darwin
import Foundation
import Security

let interfaceVersion = 1  // the door's interface this command speaks (door/api.py INTERFACE)
let cliVersion = 1        // what the door's status calls minCli

// MARK: - Arguments: options anywhere, then the command and its words

var words: [String] = []
var options: [String: String] = [:]
var flags: Set<String> = []
let valued: Set<String> = ["agent", "channel", "key", "label", "project"]
let switches: Set<String> = ["json", "wait", "follow", "no-skill", "help"]
do {
    // An option it doesn't know is refused, never ignored: a mistyped --key would otherwise start a fresh request.
    func refuse(_ says: String) -> Never { FileHandle.standardError.write("hooke: \(says)\n  Next: hooke help\n".data(using: .utf8)!); exit(2) }
    var rest = Array(CommandLine.arguments.dropFirst())
    while !rest.isEmpty {
        let arg = rest.removeFirst()
        guard arg.hasPrefix("--") else { words.append(arg); continue }
        var name = String(arg.dropFirst(2))
        var inline: String?
        if let equals = name.firstIndex(of: "=") { inline = String(name[name.index(after: equals)...]); name = String(name[..<equals]) }
        if valued.contains(name) {
            if let inline { options[name] = inline; continue }
            guard !rest.isEmpty else { refuse("--\(name) needs a value") }
            options[name] = rest.removeFirst()
        } else if switches.contains(name) && inline == nil {
            flags.insert(name)
        } else {
            refuse("there is no option --\(name)")
        }
    }
}
let jsonOutput = flags.contains("json")

// MARK: - Output

func say(_ line: String) { FileHandle.standardOutput.write((line + "\n").data(using: .utf8)!) }
func fail(_ says: String, next: String = "", code: Int32 = 1) -> Never {
    if jsonOutput {
        let body: [String: Any] = ["ok": false, "says": says, "next": next]
        if let data = try? JSONSerialization.data(withJSONObject: body, options: [.sortedKeys]) { say(String(decoding: data, as: UTF8.self)) }
    } else {
        FileHandle.standardError.write(("hooke: \(says)" + (next.isEmpty ? "" : "\n  Next: \(next)") + "\n").data(using: .utf8)!)
    }
    exit(code)
}
func printJSON(_ value: Any) {
    guard let data = try? JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys]) else { return }
    say(String(decoding: data, as: UTF8.self))
}

// MARK: - Which app

/// The app this command belongs to, from its own Info.plist: .../X.app/Contents/Helpers/hooke.
struct HookeApp {
    let bundle: URL?
    let channel: String
    let supportFolder: String
    let urlScheme: String

    static func mine() -> HookeApp {
        // Its own executable's real path (not argv[0], which names whatever was typed or found on PATH).
        let me = (Bundle.main.executableURL ?? URL(fileURLWithPath: CommandLine.arguments[0])).resolvingSymlinksInPath()
        let contents = me.deletingLastPathComponent().deletingLastPathComponent()
        if contents.lastPathComponent == "Contents",
           let data = try? Data(contentsOf: contents.appendingPathComponent("Info.plist")),
           let info = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] {
            return HookeApp(bundle: contents.deletingLastPathComponent(),
                            channel: info["HookeUIChannel"] as? String ?? "stable",
                            supportFolder: info["HookeUISupportFolder"] as? String ?? "HookeUI",
                            urlScheme: info["HookeUIURLScheme"] as? String ?? "hookeui")
        }
        // A copy built outside an app (development): HOOKE_DOOR_URL names a source checkout's door; nothing launches.
        return HookeApp(bundle: nil, channel: ProcessInfo.processInfo.environment["HOOKE_CHANNEL"] ?? "development",
                        supportFolder: "", urlScheme: "hookeui")
    }

    var support: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/\(supportFolder)") }
    var keychainService: String { "org.hookeui.agent.\(channel)" }
    var skillSource: URL? {
        if bundle == nil, let override = ProcessInfo.processInfo.environment["HOOKE_SKILL_SOURCE"] { return URL(fileURLWithPath: override) }
        return bundle?.appendingPathComponent("Contents/Resources/app/.agents/skills/hooke")
    }
}

let app = HookeApp.mine()
if let channel = options["channel"], channel != app.channel {
    fail("This hooke belongs to Hooke's \(app.channel) channel, not \(channel).",
         next: "run the hooke inside the \(channel) app: …/Contents/Helpers/hooke")
}

/// The executable a pid is running, so a state file left by a copy that's gone (its pid reused) says nothing.
func executable(of pid: Int32) -> String? {
    var buffer = [CChar](repeating: 0, count: 4096)
    guard proc_pidpath(pid, &buffer, UInt32(buffer.count)) > 0 else { return nil }
    return String(cString: buffer)
}

func state() -> AgentState? {
    guard let bundle = app.bundle, let s = AgentState.read(from: app.support) else { return nil }
    if s.phase == "stopped" || s.phase == "updating" { return s }
    guard let running = executable(of: s.pid), running.hasPrefix(bundle.resolvingSymlinksInPath().path + "/") else { return nil }
    return s
}

/// A source checkout's door, only for a copy built outside an app (found from its own executable's real path), and only
/// on this Mac's loopback. A copy inside an app reads its door only from its own app's state file.
func developmentDoor() -> URL? {
    guard app.bundle == nil, let raw = ProcessInfo.processInfo.environment["HOOKE_DOOR_URL"], let url = URL(string: raw),
          url.scheme == "http", ["127.0.0.1", "localhost", "::1"].contains(url.host ?? "") else { return nil }
    return url
}

func doorURL() -> URL? {
    if app.bundle == nil { return developmentDoor() }
    guard let s = state(), s.phase == "ready", let port = s.port else { return nil }
    return URL(string: "http://127.0.0.1:\(port)")
}

/// Opens this copy of Hooke by its own path (not by bundle id, which LaunchServices may map to another copy).
func launch() {
    guard let bundle = app.bundle else { return }
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/usr/bin/open")
    process.arguments = [bundle.path]
    do { try process.run(); process.waitUntilExit() } catch {
        fail("Hooke couldn't be opened: \(error.localizedDescription)", next: "open \(bundle.path) yourself, then run this again")
    }
    if process.terminationStatus != 0 {
        fail("macOS couldn't open \(bundle.path).", next: "open it once from Finder (macOS may ask to confirm), then run this again")
    }
}

/// Waits until Hooke's workspace is ready, opening it when it isn't running, saying each step once. An update in
/// progress is waited for, never opened over.
func waitUntilReady(timeout: TimeInterval = 1800) -> URL {
    if let url = doorURL() { return url }
    guard app.bundle != nil else {
        fail("No Hooke app here, and no HOOKE_DOOR_URL for a source checkout.", next: "install Hooke from hookeui.org/download")
    }
    let started = Date()
    var last = ""
    var launched = false
    while Date().timeIntervalSince(started) < timeout {
        if let url = doorURL() { if !last.isEmpty { say("Hooke is ready.") }; return url }
        let s = state()
        let updating = s?.phase == "updating" && Date().timeIntervalSince1970 - (s?.at ?? 0) < 600
        if !launched && !updating && (s == nil || s?.phase == "stopped" || s?.phase == "updating") {
            say("Opening \(app.bundle!.deletingPathExtension().lastPathComponent)…")
            launch()
            launched = true
        }
        if let s, s.says != last {
            last = s.says
            say(s.phase == "asking" ? "Waiting for you: \(s.says)" : s.says)
            if s.phase == "failed" { fail("Hooke couldn't open: \(s.says)", next: "use Try again in the Hooke window, then run this again") }
        }
        Thread.sleep(forTimeInterval: 1)
    }
    fail("Hooke didn't finish opening in time.", next: "check the Hooke window, then run this again")
}

// MARK: - The door

struct Reply { let status: Int; let body: [String: Any] }

func request(_ method: String, _ path: String, query: [URLQueryItem] = [], body: [String: Any]? = nil, token: String? = nil,
             base: URL) -> Reply {
    var parts = URLComponents(url: base.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
    if !query.isEmpty { parts.queryItems = query }
    var req = URLRequest(url: parts.url!)
    req.httpMethod = method
    req.timeoutInterval = 30
    req.setValue("1", forHTTPHeaderField: "Hooke-Contract")
    if let token { req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    if let body {
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try? JSONSerialization.data(withJSONObject: body)
    }
    let done = DispatchSemaphore(value: 0)
    var result = Reply(status: 0, body: [:])
    URLSession.shared.dataTask(with: req) { data, response, error in
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let json = (data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]) ?? [:]
        result = Reply(status: status, body: error != nil && json.isEmpty ? ["says": error!.localizedDescription] : json)
        done.signal()
    }.resume()
    done.wait()
    return result
}

/// A door call that rides out a restart or an update: when Hooke doesn't answer, it waits for it to be ready again (its
/// port may change) and asks once more. Retrying is safe: every effect carries a request key.
func door(_ method: String, _ path: String, query: [URLQueryItem] = [], body: [String: Any]? = nil, token: String?) -> Reply {
    var warned = false
    for _ in 0..<4 {
        let base = waitUntilReady()
        let reply = request(method, path, query: query, body: body, token: token, base: base)
        if reply.status != 0 { return reply }
        if !warned && !jsonOutput { say("Hooke isn't answering (it may be restarting or updating); waiting…"); warned = true }
        Thread.sleep(forTimeInterval: 2)
    }
    return Reply(status: 0, body: ["says": "Hooke didn't answer.", "next": "check the Hooke window, then run this again"])
}

func refused(_ reply: Reply) -> Never {
    let says = reply.body["says"] as? String ?? (reply.status == 0 ? "Hooke didn't answer." : "Hooke refused (HTTP \(reply.status)).")
    fail(says, next: reply.body["next"] as? String ?? "")
}

// MARK: - Which agent, and its token in the Keychain

let labels = ["claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "other": "An agent"]
let skillFolders = ["claude-code": ".claude/skills", "codex": ".agents/skills", "cursor": ".cursor/skills", "other": ".agents/skills"]

/// Every command names its agent, so one agent never acts with another's connection (its work would be recorded as
/// the other's).
func agent() -> String {
    guard let agent = options["agent"] ?? ProcessInfo.processInfo.environment["HOOKE_AGENT"], labels[agent] != nil else {
        fail("Say which agent you are.", next: "pass --agent claude-code (or codex, cursor, other)")
    }
    return agent
}

func keychain(_ query: [String: Any]) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: app.keychainService].merging(query) { $1 }
}

func saveToken(_ token: String, agent: String) {
    SecItemDelete(keychain([kSecAttrAccount as String: agent]) as CFDictionary)
    let status = SecItemAdd(keychain([kSecAttrAccount as String: agent, kSecValueData as String: Data(token.utf8),
                                      kSecAttrLabel as String: "Hooke agent connection (\(agent))"]) as CFDictionary, nil)
    if status != errSecSuccess { fail("The connection couldn't be kept in your Keychain (\(status)).", next: "run hooke connect again") }
}

func token(agent: String) -> String? {
    var found: CFTypeRef?
    let one = keychain([kSecAttrAccount as String: agent, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne])
    guard SecItemCopyMatching(one as CFDictionary, &found) == errSecSuccess, let data = found as? Data else { return nil }
    return String(decoding: data, as: UTF8.self)
}

func connected() -> String {
    let me = agent()
    guard let token = token(agent: me) else { fail("\(labels[me]!) isn't connected to Hooke.", next: "run hooke connect --agent \(me)") }
    return token
}

// MARK: - The skill

let skillMarker = ".installed-by-hooke"

func skillTarget(_ agent: String) -> URL {
    FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(skillFolders[agent] ?? ".agents/skills")
        .appendingPathComponent("hooke")
}

func skillVersion(at folder: URL) -> Int? {
    guard let text = try? String(contentsOf: folder.appendingPathComponent("SKILL.md"), encoding: .utf8),
          let line = text.split(separator: "\n").first(where: { $0.trimmingCharacters(in: .whitespaces).hasPrefix("version:") }) else { return nil }
    return Int(line.split(separator: ":").last?.trimmingCharacters(in: CharacterSet(charactersIn: " \"'")) ?? "")
}

/// Installs or refreshes the Hooke skill. It never touches a connection, and it never replaces a folder named hooke
/// that Hooke didn't install.
func installSkill(agent: String) -> String {
    guard let source = app.skillSource, FileManager.default.fileExists(atPath: source.appendingPathComponent("SKILL.md").path) else {
        fail("This Hooke has no skill to install.", next: "update Hooke")
    }
    let fm = FileManager.default
    let target = skillTarget(agent)
    if fm.fileExists(atPath: target.path) && !fm.fileExists(atPath: target.appendingPathComponent(skillMarker).path) {
        fail("There's already a skill folder at \(target.path) that Hooke didn't install, so it was left as it is.",
             next: "move it aside, then run hooke skill update --agent \(agent)")
    }
    try? fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
    let staged = target.deletingLastPathComponent().appendingPathComponent(".hooke-\(UUID().uuidString)")
    do {
        try fm.copyItem(at: source, to: staged)
        try Data("Installed by \(app.bundle?.path ?? "a development hooke") (\(app.channel)). hooke skill update refreshes it.\n".utf8)
            .write(to: staged.appendingPathComponent(skillMarker))
        if fm.fileExists(atPath: target.path) { _ = try fm.replaceItemAt(target, withItemAt: staged) }
        else { try fm.moveItem(at: staged, to: target) }
    } catch {
        try? fm.removeItem(at: staged)
        fail("The Hooke skill couldn't be installed at \(target.path): \(error.localizedDescription)")
    }
    return target.path
}

// MARK: - Commands

func connect() {
    let me = agent()
    let label = options["label"] ?? labels[me]!
    var bytes = [UInt8](repeating: 0, count: 32)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { fail("No randomness for the secret.") }
    let secret = bytes.map { String(format: "%02x", $0) }.joined()
    let asked = door("POST", "api/agent/v1/connect", body: ["label": label, "kind": me, "secretSha256": Data(secret.utf8).sha256Hex], token: nil)
    guard asked.status == 202, let pending = asked.body["pending"] as? [String: Any], let pid = pending["id"] as? String,
          let code = pending["code"] as? String else { refused(asked) }
    say("Confirm \(label) in Hooke's dialog. It shows the code \(code).")
    let started = Date()
    while Date().timeIntervalSince(started) < 600 {
        let claim = door("POST", "api/agent/v1/connect/\(pid)/claim", body: ["secret": secret], token: nil)
        if claim.status == 200, let token = claim.body["token"] as? String, let connection = claim.body["connection"] as? [String: Any] {
            saveToken(token, agent: me)
            let skill = flags.contains("no-skill") ? nil : installSkill(agent: me)
            if jsonOutput { printJSON(["ok": true, "connection": connection, "skill": skill as Any]); return }
            say("Connected: \(label) can use Hooke (\(connection["id"] ?? "")).")
            if let skill { say("The Hooke skill is installed at \(skill); new sessions of \(label) will know how to use Hooke.") }
            say("Next: hooke example --agent \(me)")
            return
        }
        if claim.status != 202 { refused(claim) }
        Thread.sleep(forTimeInterval: 1)
    }
    fail("Nobody confirmed the connection in Hooke in ten minutes.", next: "run hooke connect --agent \(me) again")
}

func status() {
    if !flags.contains("wait") && app.bundle != nil && doorURL() == nil {
        // Not open (or still opening): say where it is without opening it.
        if let s = state(), s.phase != "stopped" {
            if jsonOutput { printJSON(["ok": true, "phase": s.phase, "says": s.says, "version": s.version, "channel": s.channel]); return }
            say("Hooke \(s.version) (\(s.channel)): \(s.says)")
            return
        }
        if jsonOutput { printJSON(["ok": true, "phase": "stopped", "says": "Hooke isn't running."]); return }
        say("Hooke isn't running. hooke status --wait opens it.")
        return
    }
    let me = options["agent"] ?? ProcessInfo.processInfo.environment["HOOKE_AGENT"]
    guard let me, labels[me] != nil, let token = token(agent: me) else {
        _ = waitUntilReady()
        if jsonOutput { printJSON(["ok": true, "phase": "ready", "connected": false]); return }
        say("Hooke is open. To connect: hooke connect --agent <claude-code|codex|cursor|other>.")
        return
    }
    let reply = door("GET", "api/agent/v1/status", token: token)
    guard reply.status == 200 else { refused(reply) }
    var body = reply.body
    let iface = body["interface"] as? [String: Any] ?? [:]
    let installed = skillVersion(at: skillTarget(me))
    let skillOld = (iface["minSkill"] as? Int).map { (installed ?? 0) < $0 } ?? false
    body["skill"] = ["installed": installed as Any, "outdated": skillOld]
    if jsonOutput { printJSON(body); return }
    let appInfo = body["app"] as? [String: Any] ?? [:]
    let connection = body["connection"] as? [String: Any] ?? [:]
    say("Hooke \(appInfo["version"] as? String ?? "(source)") (\(appInfo["channel"] ?? app.channel)) is open; connected as \(connection["label"] ?? me).")
    if let minimum = iface["minCli"] as? Int, minimum > cliVersion { say("This hooke is older than Hooke asks for: update Hooke.") }
    if let version = iface["version"] as? Int, version != interfaceVersion { say("Hooke's door is version \(version); this hooke speaks \(interfaceVersion).") }
    if skillOld { say("The Hooke skill is out of date: hooke skill update --agent \(me).") }
}

func tools() {
    let token = connected()
    let query = words.joined(separator: " ")
    let reply = door("GET", "api/agent/v1/tools",
                     query: [URLQueryItem(name: "perPage", value: "100")] + (query.isEmpty ? [] : [URLQueryItem(name: "q", value: query)]),
                     token: token)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    for tool in reply.body["tools"] as? [[String: Any]] ?? [] {
        let next = tool["next"] as? String
        say("\(tool["id"] ?? "")  \(tool["name"] ?? ""): \(tool["task"] ?? "")  [\(tool["state"] ?? "")\(next.map { ": " + $0 } ?? "")]")
    }
}

/// Follows work to its end, through restarts: every poll finds Hooke again, and a restart's `interrupted` is said.
func follow(_ operation: [String: Any], token: String) -> [String: Any] {
    guard let id = operation["id"] as? String else { return operation }
    var last = ""
    var current = operation
    while true {
        let progress = (current["progress"] as? [String: Any])?["says"] as? String ?? ""
        let stateNow = current["state"] as? String ?? ""
        let line = progress.isEmpty ? stateNow : progress
        if line != last && !jsonOutput { say("… \(line)"); last = line }
        if ["succeeded", "failed", "cancelled", "interrupted"].contains(stateNow) { return current }
        Thread.sleep(forTimeInterval: 0.5)
        let reply = door("GET", "api/agent/v1/operations/\(id)", token: token)
        guard reply.status == 200, let next = reply.body["operation"] as? [String: Any] else { refused(reply) }
        current = next
    }
}

func report(_ operation: [String: Any]) -> Never {
    let outcome = operation["outcome"] as? [String: Any] ?? [:]
    let link = (outcome["page"] as? String).flatMap { AgentLinks.link(scheme: app.urlScheme, page: $0) }
    if jsonOutput {
        var out = operation
        if let link { out["openInHooke"] = link }
        printJSON(["ok": operation["state"] as? String == "succeeded", "operation": out])
    } else {
        say("\(operation["state"] ?? ""): \(outcome["says"] as? String ?? "")")
        if let next = outcome["next"] as? String { say("Next: \(next)") }
        if let shown = outcome["shownInHooke"] as? Bool { say(shown ? "Hooke shows it." : "It's saved, but no Hooke window showed it yet.") }
        if let link { say("Open in Hooke: \(link)") }
        if operation["tool"] as? String == "open", let project = outcome["project"] as? String {
            say("Your Project: \(project). Run capabilities there with: hooke run <capability> --project \(project)")
        }
        say("Operation \(operation["id"] ?? "") · request key \(operation["requestKey"] ?? "") (the same key finds this again; it never runs it twice)")
    }
    exit(operation["state"] as? String == "succeeded" ? 0 : 1)
}

func call(_ tool: String, arguments: [String: Any]) -> Never {
    let token = connected()
    let key = options["key"] ?? "\(tool)-\(UUID().uuidString.lowercased())"
    var body: [String: Any] = ["tool": tool, "args": arguments, "requestKey": key]
    if let project = options["project"] { body["project"] = project }
    let reply = door("POST", "api/agent/v1/operations", body: body, token: token)
    guard reply.status == 200 || reply.status == 202, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    if reply.body["made"] as? Bool == false && !jsonOutput { say("That request key was already used for this request: following it, not running it again.") }
    report(follow(operation, token: token))
}

func operationCommand() -> Never {
    let token = connected()
    guard let id = words.first else { fail("Say which operation.", next: "hooke operations lists them") }
    let reply = door("GET", "api/agent/v1/operations/\(id)", token: token)
    guard reply.status == 200, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    report(flags.contains("follow") ? follow(operation, token: token) : operation)
}

func operations() {
    let token = connected()
    let reply = door("GET", "api/agent/v1/operations", token: token)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    for op in reply.body["operations"] as? [[String: Any]] ?? [] {
        say("\(op["id"] ?? "")  \(op["tool"] ?? "")  \(op["state"] ?? "")  key \(op["requestKey"] ?? "")")
    }
    if let unreadable = reply.body["unreadable"] as? [String], !unreadable.isEmpty { say("Unreadable records: \(unreadable.joined(separator: ", "))") }
}

func cancel() {
    let token = connected()
    guard let id = words.first else { fail("Say which operation.", next: "hooke operations lists them") }
    let reply = door("POST", "api/agent/v1/operations/\(id)/cancel", token: token)
    guard reply.status == 200, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    say(operation["state"] as? String == "cancelRequested" ? "Asked it to stop; it has stopped once hooke operation \(id) says cancelled."
        : "It already ended: \(operation["state"] ?? "").")
}

func openPage() {
    let token = connected()
    guard let target = words.first else { _ = waitUntilReady(); launch(); say("Brought Hooke forward."); return }
    if !target.hasPrefix("#") {
        // A file of the researcher's: into Hooke's library, opened in a Project of the agent's own.
        let path = URL(fileURLWithPath: (target as NSString).expandingTildeInPath).standardizedFileURL.path
        call("open", arguments: ["file": path])
    }
    let page = target
    let reply = door("POST", "api/agent/v1/open", body: ["page": page], token: token)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    say(reply.body["says"] as? String ?? "")
}

let usage = """
hooke: use the Hooke app this command ships in, from your coding agent. Every command takes --agent <kind>
(claude-code, codex, cursor, other), or the HOOKE_AGENT environment variable.

  hooke status [--wait]                  Is Hooke open, and are you connected? --wait opens it and waits.
  hooke connect                          Connect; the researcher confirms once in Hooke's dialog.
  hooke skill update                     Refresh the Hooke skill; changes no connection.
  hooke tools [words]                    What Hooke can do here, and what each needs.
  hooke example [--key K]                Run the guided example; prints the result and an Open in Hooke link.
  hooke open <file>                      Add a file to Hooke and open it in a Project of your own.
  hooke run <capability> ['{json}'] --project P [--key K]
                                         Run any capability hooke tools lists, in your Project P (from open).
  hooke operations | operation <id> [--follow] | cancel <id>
  hooke open <page> | open               Show a page (an operation's) in Hooke, or bring Hooke forward.

  --json for machine-readable output.
"""

guard let command = words.first, !flags.contains("help") else { say(usage); exit(0) }
words.removeFirst()
switch command {
case "status": status()
case "connect": connect()
case "skill":
    guard words.first == "update" else { fail("Did you mean hooke skill update --agent <kind>?") }
    let path = installSkill(agent: agent())
    if jsonOutput { printJSON(["ok": true, "skill": path]) } else { say("The Hooke skill is up to date at \(path).") }
case "tools": tools()
case "example": call("example", arguments: [:])
case "call", "run":
    guard let tool = words.first else { fail("Say which tool.", next: "hooke tools lists them") }
    var arguments: [String: Any] = [:]
    if words.count > 1 {
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(words[1].utf8)) as? [String: Any] else {
            fail("The arguments are one JSON object.", next: "for example: hooke call \(tool) '{}' --agent claude-code")
        }
        arguments = parsed
    }
    call(tool, arguments: arguments)
case "operations": operations()
case "operation": operationCommand()
case "cancel": cancel()
case "open": openPage()
case "help": say(usage)
case "version": say("hooke \(cliVersion) (door interface \(interfaceVersion), \(app.channel) channel)")
default: fail("Unknown command \(command).", next: "hooke help")
}

// MARK: - SHA-256

extension Data {
    var sha256Hex: String {
        var digest = [UInt8](repeating: 0, count: Int(CC_SHA256_DIGEST_LENGTH))
        withUnsafeBytes { _ = CC_SHA256($0.baseAddress, CC_LONG(count), &digest) }
        return digest.map { String(format: "%02x", $0) }.joined()
    }
}
