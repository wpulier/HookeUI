import AppKit
import WebKit
import Sparkle

/// Sparkle owns release admission, downloads and installation. Studio only presents its state.
final class Updates: NSObject, SPUUpdaterDelegate, SPUStandardUserDriverDelegate, WKScriptMessageHandlerWithReply {
    weak var webView: WKWebView?
    var origin: URL?
    var check: (() -> Void)?
    var installing = false
    /// The version Sparkle is installing (willInstallUpdate): the restart sheet names it, and while it waits for a
    /// restart (the person chose Later) the workspace says it is downloaded.
    private(set) var downloadedVersion: String?
    /// Sparkle asked Hooke to quit so it can install and relaunch (Install and Relaunch): the next quit request is that
    /// restart, not a plain quit.
    private var restartRequested = false
    private var ready = false
    private var available: String?
    private var problem: String?
    var supportsGentleScheduledUpdateReminders: Bool { true }

    /// Whether the quit request now is Sparkle's restart; asked once per request.
    func takeRestartRequest() -> Bool {
        defer { restartRequested = false }
        return restartRequested
    }

    func feedURLString(for updater: SPUUpdater) -> String? {
        UserDefaults.standard.string(forKey: "HookeUIFeedURL")
    }
    func standardUserDriverShouldHandleShowingScheduledUpdate(_ update: SUAppcastItem, andInImmediateFocus immediateFocus: Bool) -> Bool {
        // Fall back to Sparkle if the workspace isn't able to present the reminder.
        !ready || webView?.url?.host != origin?.host || webView?.url?.port != origin?.port
    }
    func standardUserDriverWillHandleShowingUpdate(_ handleShowingUpdate: Bool, forUpdate update: SUAppcastItem, state: SPUUserUpdateState) {
        // Sparkle showing its own window for the update it is installing: its Install and Relaunch asks to quit again.
        if installing && handleShowingUpdate { restartRequested = true }
        available = update.displayVersionString; problem = nil; publish()
    }
    func standardUserDriverWillFinishUpdateSession() {
        // Dismissing the sheet defers installation; the available update remains discoverable.
        publish()
    }
    func updater(_ updater: SPUUpdater, willInstallUpdate item: SUAppcastItem) {
        installing = true
        restartRequested = true
        downloadedVersion = item.displayVersionString
        Log.shared.line("installing update \(item.displayVersionString) after user approval")
        UserDefaults.standard.set(["version": item.displayVersionString, "notes": item.itemDescription ?? ""], forKey: "HookeUIWhatsNew")
        publish()
    }
    func updater(_ updater: SPUUpdater, didAbortWithError error: Error) {
        installing = false
        restartRequested = false
        downloadedVersion = nil
        if (error as NSError).domain == SUSparkleErrorDomain && (error as NSError).code == SUError.noUpdateError.rawValue { available = nil; problem = nil; publish(); return }
        problem = "The update check did not finish. Check your connection and try again."
        Log.shared.line("update check: \(error.localizedDescription)"); publish()
    }
    /// What the workspace shows (apps/studio/src/app-updates.ts): `downloadedVersion` while an update waits for the
    /// restart, so the rail says Restart to update rather than Review.
    private var state: [String: Any] {
        ["currentVersion": Bundle.main.shortVersion, "availableVersion": available as Any? ?? NSNull(),
         "downloadedVersion": (installing ? downloadedVersion : nil) as Any? ?? NSNull(),
         "error": problem as Any? ?? NSNull()]
    }
    func publish() {
        guard let data = try? JSONSerialization.data(withJSONObject: state), let json = String(data: data, encoding: .utf8) else { return }
        webView?.evaluateJavaScript("window.dispatchEvent(new CustomEvent('hooke-update-state', {detail: \(json)}))", completionHandler: nil)
    }
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler reply: @escaping (Any?, String?) -> Void) {
        let source = message.frameInfo.request.url
        guard let origin, message.frameInfo.isMainFrame, source?.scheme == origin.scheme,
              source?.host == origin.host, source?.port == origin.port,
              let body = message.body as? [String: Any], let op = body["op"] as? String,
              ["status", "check"].contains(op) else { reply(nil, "Updates are only available in this workspace."); return }
        ready = true
        reply(state, nil)
        if op == "check" { DispatchQueue.main.async { self.check?() } }
    }
    /// What restarting or quitting would stop (UpdateWords.swift), read from this window's workspace.
    lazy var reads = RestartReads(webView: { [weak self] in self?.webView }, origin: { [weak self] in self?.origin })
}
