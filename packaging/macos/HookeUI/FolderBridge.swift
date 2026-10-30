import AppKit
import WebKit

/// Native selection transport only; the existing import endpoint owns acceptance.
final class FolderBridge: NSObject, WKScriptMessageHandlerWithReply {
    private struct Lease { let snapshot: FolderSnapshot; var reading = false; var touched = Date() }
    private let origin: URL
    private let queue = DispatchQueue(label: "org.hookeui.selected-folder", qos: .userInitiated)
    private var leases: [String: Lease] = [:]
    private var choosing = false, closing = false
    private var panel: NSOpenPanel?
    private var capture: FolderCaptureControl?
    private var timer: Timer?
    init(origin: URL) {
        self.origin = origin
        super.init()
        timer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { [weak self] _ in
            self?.leases = self?.leases.filter { $0.value.reading || Date().timeIntervalSince($0.value.touched) < 60 } ?? [:]
        }
    }
    func close() { closing = true; panel?.cancel(nil); panel = nil; capture?.cancel(); timer?.invalidate(); timer = nil; leases.removeAll() }
    deinit { timer?.invalidate() }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage,
                               replyHandler reply: @escaping (Any?, String?) -> Void) {
        let source = message.frameInfo.request.url
        guard !closing, message.frameInfo.isMainFrame, source?.scheme == origin.scheme,
              source?.host == origin.host, source?.port == origin.port,
              let body = message.body as? [String: Any], let operation = body["op"] as? String else {
            reply(nil, "Folder selection is only available in this workspace."); return
        }
        if operation == "cancel" {
            panel?.cancel(nil); capture?.cancel(); reply(true, nil); return
        }
        if operation == "choose" || operation == "choose-project" {
            guard !choosing, leases.count < 4 else { reply(nil, "Finish the current folder selection, then try again."); return }
            choosing = true
            let panel = NSOpenPanel()
            self.panel = panel
            panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.allowsMultipleSelection = false
            panel.prompt = "Choose folder"
            panel.message = operation == "choose-project"
                ? "Choose the Hooke Project folder containing project.json. Opening verifies its retained evidence."
                : "Include all files in this folder, including hidden metadata. Links are not followed."
            panel.begin { [weak self] result in
                guard let self else { reply(nil, "Workspace closed."); return }
                self.panel = nil
                guard !self.closing, result == .OK, let selected = panel.url else { self.choosing = false; reply(NSNull(), nil); return }
                if operation == "choose-project" {
                    self.choosing = false
                    reply(selected.path, nil)
                    return
                }
                let control = FolderCaptureControl(); self.capture = control
                self.queue.async {
                    let captured = Result { try FolderSnapshot.capture(selected, control: control) }
                    DispatchQueue.main.async {
                        self.choosing = false; self.capture = nil
                        guard !self.closing else { reply(nil, "Workspace closed."); return }
                        switch captured {
                        case .success(let snapshot):
                            let token = UUID().uuidString
                            self.leases[token] = Lease(snapshot: snapshot)
                            reply(["format": "hooke-folder-selection-v2", "token": token, "name": snapshot.name,
                                   "size": snapshot.size, "count": snapshot.count, "bytes": snapshot.bytes], nil)
                        case .failure(let error):
                            reply(nil, (error as? FolderSelectionError)?.errorDescription ?? "The selected folder could not be read. Choose it again.")
                        }
                    }
                }
            }
            return
        }
        guard let token = body["token"] as? String, var lease = leases[token] else { reply(nil, "Folder selection expired. Choose it again."); return }
        if operation == "release" { leases.removeValue(forKey: token); reply(true, nil); return }
        guard operation == "read", !lease.reading else { reply(nil, "Folder selection is already being read."); return }
        guard let number = body["offset"] as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              number.doubleValue.isFinite, number.doubleValue.rounded() == number.doubleValue,
              number.doubleValue >= 0, number.doubleValue < Double(lease.snapshot.bytes) else {
            reply(nil, "Folder read needs its exact bounded offset."); return
        }
        let offset = number.intValue
        lease.reading = true; lease.touched = Date(); leases[token] = lease
        queue.async {
            let result = Result { try lease.snapshot.read(offset: offset)?.base64EncodedString() }
            DispatchQueue.main.async {
                guard !self.closing, self.leases[token] != nil else { reply(nil, "Folder selection was released."); return }
                self.leases[token]?.reading = false; self.leases[token]?.touched = Date()
                switch result {
                case .success(let data):
                    if data == nil { self.leases.removeValue(forKey: token) }
                    reply(data ?? "", nil)
                case .failure:
                    self.leases.removeValue(forKey: token); reply(nil, "Folder selection could not be read. Choose it again.")
                }
            }
        }
    }
}
