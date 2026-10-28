// hooke: the command a researcher's own coding agent runs to use the Hooke app it ships in (ADR 0026;
// services/orchestrator/door/AGENTS.md). It lives in the app bundle (Contents/Helpers/hooke) and talks only to that
// app, so Stable and Beta never mix. It holds no scientific logic and no list of capabilities: everything it can do it
// asks the app's door for. Compiled with ../HookeUI/AgentShared.swift.

import CommonCrypto
import Foundation
import Security

let interfaceVersion = 1  // the door's interface this command speaks (door/api.py INTERFACE)
let cliVersion = 1        // what the door's status calls minCli

// MARK: - Output

var jsonOutput = false
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
    let bundleId: String
    let supportFolder: String
    let urlScheme: String

    static func mine() -> HookeApp {
        let me = URL(fileURLWithPath: CommandLine.arguments[0]).resolvingSymlinksInPath()
        let contents = me.deletingLastPathComponent().deletingLastPathComponent()
        let plist = contents.appendingPathComponent("Info.plist")
        if let data = try? Data(contentsOf: plist),
           let info = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] {
            return HookeApp(bundle: contents.deletingLastPathComponent(),
                            channel: info["HookeUIChannel"] as? String ?? "stable",
                            bundleId: info["CFBundleIdentifier"] as? String ?? "org.hookeui.HookeUI",
                            supportFolder: info["HookeUISupportFolder"] as? String ?? "HookeUI",
                            urlScheme: info["HookeUIURLScheme"] as? String ?? "hookeui")
        }
        // A source checkout's copy (development): HOOKE_DOOR_URL names its door, and nothing is launched.
        return HookeApp(bundle: nil, channel: ProcessInfo.processInfo.environment["HOOKE_CHANNEL"] ?? "development",
                        bundleId: "", supportFolder: "", urlScheme: "hookeui")
    }

    var support: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/\(supportFolder)") }
    var keychainService: String { "org.hookeui.agent.\(channel)" }
    var skillSource: URL? {
        if let override = ProcessInfo.processInfo.environment["HOOKE_SKILL_SOURCE"] { return URL(fileURLWithPath: override) }
        return bundle?.appendingPathComponent("Contents/Resources/app/.agents/skills/hooke")
    }
}

let app = HookeApp.mine()

func state() -> AgentState? {
    guard app.bundle != nil, let s = AgentState.read(from: app.support) else { return nil }
    // A state file left by a copy that's no longer running says nothing about now.
    if s.phase != "stopped" && kill(s.pid, 0) != 0 { return nil }
    return s
}

func doorURL() -> URL? {
    if let override = ProcessInfo.processInfo.environment["HOOKE_DOOR_URL"] { return URL(string: override) }
    guard let s = state(), s.phase == "ready", let port = s.port else { return nil }
    return URL(string: "http://127.0.0.1:\(port)")
}

func bringForward() {
    guard !app.bundleId.isEmpty else { return }
    _ = try? Process.run(URL(fileURLWithPath: "/usr/bin/open"), arguments: ["-b", app.bundleId])
}

/// Opens Hooke if it isn't running and waits until its workspace is ready, saying each step once.
func waitUntilReady(timeout: TimeInterval = 1800) -> URL {
    if let url = doorURL() { return url }
    guard app.bundle != nil else { fail("No Hooke app here, and HOOKE_DOOR_URL isn't set.", next: "install Hooke from hookeui.org/download") }
    if state() == nil || state()?.phase == "stopped" || state()?.phase == "failed" {
        say("Opening \(app.bundle!.deletingPathExtension().lastPathComponent)…")
        bringForward()
    }
    let started = Date()
    var last = ""
    while Date().timeIntervalSince(started) < timeout {
        if let url = doorURL() { if !last.isEmpty { say("Hooke is ready.") }; return url }
        if let s = state(), s.says != last {
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

func refused(_ reply: Reply) -> Never {
    let says = reply.body["says"] as? String ?? (reply.status == 0 ? "Hooke didn't answer." : "Hooke refused (HTTP \(reply.status)).")
    fail(says, next: reply.body["next"] as? String ?? "")
}

// MARK: - The token, kept in the Keychain

func keychain(_ query: [String: Any]) -> [String: Any] {
    [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: app.keychainService].merging(query) { $1 }
}

func saveToken(_ token: String, agent: String) {
    SecItemDelete(keychain([kSecAttrAccount as String: agent]) as CFDictionary)
    let status = SecItemAdd(keychain([kSecAttrAccount as String: agent, kSecValueData as String: Data(token.utf8),
                                      kSecAttrLabel as String: "Hooke agent connection (\(agent))"]) as CFDictionary, nil)
    if status != errSecSuccess { fail("The connection couldn't be kept in your Keychain (\(status)).", next: "run hooke connect again") }
}

func token(agent: String?) -> (agent: String, token: String)? {
    // The file-based Keychain returns no secrets for "all matches": list the connected agents, then read one.
    var accounts: [String] = []
    if let agent { accounts = [agent] } else {
        var found: CFTypeRef?
        let listing = keychain([kSecReturnAttributes as String: true, kSecMatchLimit as String: kSecMatchLimitAll])
        if SecItemCopyMatching(listing as CFDictionary, &found) == errSecSuccess, let rows = found as? [[String: Any]] {
            accounts = rows.compactMap { $0[kSecAttrAccount as String] as? String }
        }
        if accounts.count > 1 {
            fail("More than one agent is connected to this Hooke: \(accounts.joined(separator: ", ")).", next: "say which with --agent")
        }
    }
    guard let account = accounts.first else { return nil }
    var found: CFTypeRef?
    let one = keychain([kSecAttrAccount as String: account, kSecReturnData as String: true, kSecMatchLimit as String: kSecMatchLimitOne])
    guard SecItemCopyMatching(one as CFDictionary, &found) == errSecSuccess, let data = found as? Data else { return nil }
    return (agent: account, token: String(decoding: data, as: UTF8.self))
}

func connected(_ agent: String?) -> (URL, String) {
    let base = waitUntilReady()
    guard let found = token(agent: agent) else { fail("This agent isn't connected to Hooke.", next: "run hooke connect --agent <claude-code|codex|cursor>") }
    return (base, found.token)
}

// MARK: - Arguments

var args = Array(CommandLine.arguments.dropFirst())
func option(_ name: String) -> String? {
    guard let i = args.firstIndex(of: "--\(name)"), i + 1 < args.count else { return nil }
    let value = args[i + 1]
    args.removeSubrange(i...(i + 1))
    return value
}
func flag(_ name: String) -> Bool {
    guard let i = args.firstIndex(of: "--\(name)") else { return false }
    args.remove(at: i)
    return true
}
if let channel = option("channel"), channel != app.channel {
    fail("This hooke belongs to Hooke's \(app.channel) channel, not \(channel).",
         next: "run the hooke inside the \(channel) app, under …/Contents/Helpers/hooke")
}
jsonOutput = flag("json")
let agentOption = option("agent") ?? ProcessInfo.processInfo.environment["HOOKE_AGENT"]

let labels = ["claude-code": "Claude Code", "codex": "Codex", "cursor": "Cursor", "other": "An agent"]
let skillFolders = ["claude-code": ".claude/skills", "codex": ".agents/skills", "cursor": ".cursor/skills", "other": ".agents/skills"]

// MARK: - Commands

func installSkill(agent: String) -> String {
    guard let source = app.skillSource, FileManager.default.fileExists(atPath: source.appendingPathComponent("SKILL.md").path) else {
        fail("This Hooke has no skill to install.", next: "update Hooke")
    }
    let target = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(skillFolders[agent] ?? ".agents/skills").appendingPathComponent("hooke")
    try? FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
    let staged = target.deletingLastPathComponent().appendingPathComponent(".hooke-\(UUID().uuidString)")
    do {
        try FileManager.default.copyItem(at: source, to: staged)
        if FileManager.default.fileExists(atPath: target.path) { _ = try FileManager.default.replaceItemAt(target, withItemAt: staged) }
        else { try FileManager.default.moveItem(at: staged, to: target) }
    } catch {
        try? FileManager.default.removeItem(at: staged)
        fail("The Hooke skill couldn't be installed at \(target.path): \(error.localizedDescription)")
    }
    return target.path
}

func connect() {
    guard let agent = agentOption, labels[agent] != nil else {
        fail("Say which agent this is.", next: "run hooke connect --agent claude-code (or codex, cursor, other)")
    }
    let label = option("label") ?? labels[agent]!
    let base = waitUntilReady()
    var bytes = [UInt8](repeating: 0, count: 32)
    guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { fail("No randomness for the secret.") }
    let secret = bytes.map { String(format: "%02x", $0) }.joined()
    let hash = Data(secret.utf8).sha256Hex
    let asked = request("POST", "api/agent/v1/connect", body: ["label": label, "kind": agent, "secretSha256": hash], base: base)
    guard asked.status == 202, let pending = asked.body["pending"] as? [String: Any], let pid = pending["id"] as? String,
          let code = pending["code"] as? String else { refused(asked) }
    say("Confirm \(label) in the Hooke window. It shows the code \(code).")
    bringForward()
    let started = Date()
    while Date().timeIntervalSince(started) < 600 {
        let claim = request("POST", "api/agent/v1/connect/\(pid)/claim", body: ["secret": secret], base: base)
        if claim.status == 200, let token = claim.body["token"] as? String, let connection = claim.body["connection"] as? [String: Any] {
            saveToken(token, agent: agent)
            let skill = flag("no-skill") ? nil : installSkill(agent: agent)
            if jsonOutput { printJSON(["ok": true, "connection": connection, "skill": skill as Any]); return }
            say("Connected: \(label) can use Hooke (\(connection["id"] ?? "")).")
            if let skill { say("The Hooke skill is installed at \(skill); new sessions of \(label) will know how to use Hooke.") }
            say("Next: hooke example")
            return
        }
        if claim.status != 202 { refused(claim) }
        Thread.sleep(forTimeInterval: 1)
    }
    fail("Nobody confirmed the connection in Hooke in ten minutes.", next: "run hooke connect again")
}

func status() {
    guard let s = state() ?? (app.bundle == nil ? nil : AgentState.read(from: app.support)) else {
        if app.bundle == nil, let base = doorURL() { return statusOf(base) }
        fail("Hooke isn't running.", next: "run hooke status --wait to open it")
    }
    if flag("wait") || s.phase == "ready" {
        let base = waitUntilReady()
        return statusOf(base)
    }
    if jsonOutput { printJSON(["ok": true, "phase": s.phase, "says": s.says, "version": s.version, "channel": s.channel]); return }
    say("Hooke \(s.version) (\(s.channel)): \(s.says)")
}

func statusOf(_ base: URL) {
    guard let found = token(agent: agentOption) else {
        if jsonOutput { printJSON(["ok": true, "phase": "ready", "connected": false]); return }
        say("Hooke is open. This agent isn't connected yet: run hooke connect --agent <claude-code|codex|cursor>.")
        return
    }
    let reply = request("GET", "api/agent/v1/status", token: found.token, base: base)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    let appInfo = reply.body["app"] as? [String: Any] ?? [:]
    let iface = reply.body["interface"] as? [String: Any] ?? [:]
    let connection = reply.body["connection"] as? [String: Any] ?? [:]
    say("Hooke \(appInfo["version"] as? String ?? "(source)") (\(appInfo["channel"] ?? app.channel)) is open; connected as \(connection["label"] ?? found.agent).")
    if let minimum = iface["minCli"] as? Int, minimum > cliVersion { say("This hooke is older than Hooke asks for: update Hooke.") }
    if let version = iface["version"] as? Int, version != interfaceVersion { say("Hooke's door is version \(version); this hooke speaks \(interfaceVersion).") }
}

func tools() {
    let (base, token) = connected(agentOption)
    let words = args.joined(separator: " ")
    let reply = request("GET", "api/agent/v1/tools",
                        query: [URLQueryItem(name: "perPage", value: "100")] + (words.isEmpty ? [] : [URLQueryItem(name: "q", value: words)]),
                        token: token, base: base)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    for tool in reply.body["tools"] as? [[String: Any]] ?? [] {
        let state = tool["state"] as? String ?? ""
        let next = tool["next"] as? String
        say("\(tool["id"] ?? "")  \(tool["name"] ?? ""): \(tool["task"] ?? "")  [\(state)\(next.map { ": " + $0 } ?? "")]")
    }
}

func follow(_ operation: [String: Any], base: URL, token: String) -> [String: Any] {
    guard let id = operation["id"] as? String else { return operation }
    var last = ""
    var current = operation
    while true {
        let progress = (current["progress"] as? [String: Any])?["says"] as? String ?? ""
        let stateNow = current["state"] as? String ?? ""
        let line = progress.isEmpty ? stateNow : progress
        if line != last && !jsonOutput { say("… \(line)") ; last = line }
        if ["succeeded", "failed", "cancelled", "interrupted"].contains(stateNow) { return current }
        Thread.sleep(forTimeInterval: 0.5)
        let reply = request("GET", "api/agent/v1/operations/\(id)", token: token, base: base)
        guard reply.status == 200, let next = reply.body["operation"] as? [String: Any] else { refused(reply) }
        current = next
    }
}

func report(_ operation: [String: Any]) {
    let outcome = operation["outcome"] as? [String: Any] ?? [:]
    let page = outcome["page"] as? String
    let link = page.flatMap { AgentLinks.link(scheme: app.urlScheme, page: $0) }
    if jsonOutput {
        var out = operation
        if let link { out["openInHooke"] = link }
        printJSON(["ok": operation["state"] as? String == "succeeded", "operation": out])
    } else {
        say("\(operation["state"] ?? ""): \(outcome["says"] as? String ?? "")")
        if let next = outcome["next"] as? String { say("Next: \(next)") }
        if let shown = outcome["shownInHooke"] as? Bool { say(shown ? "Hooke shows it." : "It's saved, but no Hooke window showed it yet.") }
        if let link { say("Open in Hooke: \(link)") }
        say("Operation \(operation["id"] ?? "") · request key \(operation["requestKey"] ?? "") (use the same key to find this again, never to run it twice)")
    }
    exit(operation["state"] as? String == "succeeded" ? 0 : 1)
}

func call(_ tool: String, arguments: [String: Any]) {
    let (base, token) = connected(agentOption)
    let key = option("key") ?? "\(tool)-\(UUID().uuidString.lowercased())"
    let reply = request("POST", "api/agent/v1/operations", body: ["tool": tool, "args": arguments, "requestKey": key], token: token, base: base)
    guard reply.status == 200 || reply.status == 202, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    if reply.body["made"] as? Bool == false && !jsonOutput { say("That request key was already used for this request: following it, not running it again.") }
    report(follow(operation, base: base, token: token))
}

func operationCommand() {
    let (base, token) = connected(agentOption)
    guard let id = args.first else { fail("Say which operation.", next: "hooke operations lists them") }
    let reply = request("GET", "api/agent/v1/operations/\(id)", token: token, base: base)
    guard reply.status == 200, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    report(flag("follow") ? follow(operation, base: base, token: token) : operation)
}

func operations() {
    let (base, token) = connected(agentOption)
    let reply = request("GET", "api/agent/v1/operations", token: token, base: base)
    guard reply.status == 200 else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    for op in reply.body["operations"] as? [[String: Any]] ?? [] {
        say("\(op["id"] ?? "")  \(op["tool"] ?? "")  \(op["state"] ?? "")  key \(op["requestKey"] ?? "")")
    }
    if let unreadable = reply.body["unreadable"] as? [String], !unreadable.isEmpty { say("Unreadable records: \(unreadable.joined(separator: ", "))") }
}

func cancel() {
    let (base, token) = connected(agentOption)
    guard let id = args.first else { fail("Say which operation.", next: "hooke operations lists them") }
    let reply = request("POST", "api/agent/v1/operations/\(id)/cancel", token: token, base: base)
    guard reply.status == 200, let operation = reply.body["operation"] as? [String: Any] else { refused(reply) }
    if jsonOutput { printJSON(reply.body); return }
    say(operation["state"] as? String == "cancelRequested" ? "Asked it to stop; it's stopped once hooke operation \(id) says cancelled."
        : "It already ended: \(operation["state"] ?? "").")
}

func openPage() {
    let (base, token) = connected(agentOption)
    guard let page = args.first else { bringForward(); say("Brought Hooke forward."); return }
    let reply = request("POST", "api/agent/v1/open", body: ["page": page], token: token, base: base)
    guard reply.status == 200 else { refused(reply) }
    bringForward()
    if jsonOutput { printJSON(reply.body); return }
    say(reply.body["says"] as? String ?? "")
}

let usage = """
hooke: use the Hooke app this command ships in, from your coding agent.

  hooke status [--wait]                 Is Hooke open, and is this agent connected? --wait opens it.
  hooke connect --agent <kind>          Connect (claude-code, codex, cursor, other); confirm once in Hooke.
  hooke skill update --agent <kind>     Refresh the Hooke skill; changes no connection.
  hooke tools [words]                   What Hooke can do here, and what each needs.
  hooke example [--key K]               Run the guided example; prints the result and an Open in Hooke link.
  hooke call <tool> ['{json}'] [--key K]  Start work and follow it to its end.
  hooke operations | operation <id> [--follow] | cancel <id>
  hooke open [page]                     Show a page (an operation's) in Hooke, or bring Hooke forward.

  --json for machine-readable output. --agent (or HOOKE_AGENT) picks the connection when several exist.
"""

guard let command = args.first else { say(usage); exit(0) }
args.removeFirst()
switch command {
case "status": status()
case "connect": connect()
case "skill":
    guard args.first == "update" else { fail("Did you mean hooke skill update --agent <kind>?") }
    guard let agent = agentOption, labels[agent] != nil else { fail("Say which agent's skill.", next: "hooke skill update --agent claude-code") }
    let path = installSkill(agent: agent)
    if jsonOutput { printJSON(["ok": true, "skill": path]) } else { say("The Hooke skill is up to date at \(path).") }
case "tools": tools()
case "example": call("example", arguments: [:])
case "call":
    guard let tool = args.first else { fail("Say which tool.", next: "hooke tools lists them") }
    args.removeFirst()
    var arguments: [String: Any] = [:]
    if let raw = args.first {
        guard let parsed = try? JSONSerialization.jsonObject(with: Data(raw.utf8)) as? [String: Any] else { fail("The arguments are one JSON object.") }
        arguments = parsed
    }
    call(tool, arguments: arguments)
case "operations": operations()
case "operation": operationCommand()
case "cancel": cancel()
case "open": openPage()
case "help", "--help", "-h": say(usage)
case "version", "--version": say("hooke \(cliVersion) (door interface \(interfaceVersion), \(app.channel) channel)")
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
