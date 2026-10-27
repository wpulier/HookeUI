import Foundation
import Network
import WebKit

/// Keeps the app's local-network operation alive while Blackmagic is selected.
/// The camera bridge remains the sole owner of camera discovery results/control/media.
final class CameraNetwork: NSObject, WKScriptMessageHandlerWithReply {
    private let origin: URL
    private let cameraPort: Int
    private var browser: NWBrowser?
    private var state = "idle"
    private var closed = false
    init(origin: URL, cameraPort: Int) { self.origin = origin; self.cameraPort = cameraPort }

    private func stop() { browser?.cancel(); browser = nil; state = "idle" }
    func close() { closed = true; stop() }

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler reply: @escaping (Any?, String?) -> Void) {
        let source = message.frameInfo.request.url
        let workspace = source?.port == origin.port
        let camera = source?.port == cameraPort && source?.path.hasPrefix("/camera/") == true
        guard !closed, message.frameInfo.isMainFrame, source?.scheme == origin.scheme,
              source?.host == origin.host, workspace || camera,
              let body = message.body as? [String: Any], body["op"] as? String == "observe",
              let needed = body["needed"] as? Bool else {
            reply(nil, "Camera access is only available in this workspace."); return
        }
        if !needed { stop() }
        else if browser == nil {
            state = "checking"
            let probe = NWBrowser(for: .bonjour(type: "_http._tcp", domain: "local."), using: .tcp)
            browser = probe
            probe.stateUpdateHandler = { [weak self, weak probe] value in
                guard let self, let probe, self.browser === probe else { return }
                switch value {
                case .ready: self.state = "ready"
                case .waiting(let error), .failed(let error):
                    if case .dns(let code) = error, code == -65570 || code == -65555 { self.state = "denied" }
                    else { self.state = "unavailable" }
                case .cancelled: self.state = "idle"
                default: self.state = "checking"
                }
                Log.shared.line("camera local-network check: \(self.state)")
            }
            probe.browseResultsChangedHandler = { results, _ in
                Log.shared.line("camera local-network services observed: \(results.count)")
            }
            probe.start(queue: .main)
        }
        reply(["state": state], nil)
    }
}
