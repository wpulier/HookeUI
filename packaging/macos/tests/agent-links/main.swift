// What the Mac app and the `hooke` command share (HookeUI/AgentShared.swift; ADR 0026): only allowed pages open, a link
// opens only its own channel's app, and the state file reads back what was written. Run by `make native-test`.
import Foundation

var failures = 0
func check(_ ok: Bool, _ what: String) {
    if ok { print("ok: \(what)") } else { print("FAIL: \(what)"); failures += 1 }
}

let page = "#measurements/e1?session=2026-10-28.2&revision=0"
check(AgentLinks.isPage(page), "a result's measurements page may open")
for bad in ["javascript:alert(1)", "#settings", "#measurements/e1<script>", "https://evil.example/#measurements/e1", ""] {
    check(!AgentLinks.isPage(bad), "\(bad.isEmpty ? "an empty page" : bad) may not open")
}
let link = AgentLinks.link(scheme: "hookeui", page: page)!
check(link.hasPrefix("hookeui://open?page="), "a link is hookeui://open?page=…")
check(AgentLinks.page(from: URL(string: link)!, scheme: "hookeui") == page, "the link gives back its page")
check(AgentLinks.page(from: URL(string: link)!, scheme: "hookeui-beta") == nil, "Beta never opens Stable's link")
check(AgentLinks.page(from: URL(string: "hookeui://run?page=%23measurements/e1")!, scheme: "hookeui") == nil, "a link only opens")
check(AgentLinks.page(from: URL(string: "hookeui://open?page=%23settings")!, scheme: "hookeui") == nil, "a link to another page is ignored")
check(AgentLinks.link(scheme: "hookeui", page: "#settings") == nil, "no link is made to a page that may not open")

let folder = FileManager.default.temporaryDirectory.appendingPathComponent("agent-state-\(UUID().uuidString)")
let state = AgentState(channel: "beta", bundleId: "org.hookeui.HookeUI.beta", urlScheme: "hookeui-beta", version: "0.6.0",
                       build: "5000", phase: "ready", says: "Hooke is open.", port: 8870, pid: 42, at: 1)
try! state.write(to: folder)
check(AgentState.read(from: folder) == state, "the state file reads back what was written")
try! Data("{\"format\":1,\"phase\":\"dancing\"}".utf8).write(to: folder.appendingPathComponent(AgentState.file))
check(AgentState.read(from: folder) == nil, "an unknown or incomplete state reads as none")
try? FileManager.default.removeItem(at: folder)

if failures > 0 { print("\(failures) agent-link checks failed"); exit(1) }
print("all agent-link checks passed")
