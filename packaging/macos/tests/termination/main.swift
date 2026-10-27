// Checks HookeUI/Termination.swift with a real NSApplication (make native-test): a quit that waits for its answer
// (.terminateLater) is answered and the app quits, whichever way the quit began. One case per run, since quitting ends
// the process:
//   nested    terminate inside a main-queue block, as Restart Now did after its check in 0.4.1 (4695)
//   deferred  Termination.terminate() inside a main-queue block, as Restart Now does now
//   event     terminate from a timer, as ⌘Q and the Quit item do
//   old       the 4695 answer (DispatchQueue.main.async) to a nested terminate: must hang, so the check above can fail
// A run that quits prints "ok: <case>" and exits 0; a run whose quit is never answered exits 3 after 5 s.
import AppKit

let name = CommandLine.arguments.dropFirst().first ?? ""
guard ["nested", "deferred", "event", "old"].contains(name) else {
    FileHandle.standardError.write("usage: termination nested|deferred|event|old\n".data(using: .utf8)!)
    exit(2)
}

final class Delegate: NSObject, NSApplicationDelegate {
    var asked = 0

    func applicationDidFinishLaunching(_ notification: Notification) {
        switch name {
        case "nested", "old": DispatchQueue.main.async { NSApp.terminate(nil) }
        case "deferred": DispatchQueue.main.async { Termination.terminate() }
        default: Timer.scheduledTimer(withTimeInterval: 0.05, repeats: false) { _ in NSApp.terminate(nil) }
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        asked += 1
        // The app's stopForTermination: its services stop off the main thread, then the answer goes back.
        DispatchQueue.global().asyncAfter(deadline: .now() + 0.2) {
            if name == "old" {
                DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) }
            } else {
                Termination.reply(true)
            }
        }
        return .terminateLater
    }

    func applicationWillTerminate(_ notification: Notification) {
        print("ok: \(name): the waiting quit was answered and the app quit (asked \(asked) time\(asked == 1 ? "" : "s"))")
        fflush(stdout)
    }
}

Thread.detachNewThread {
    Thread.sleep(forTimeInterval: 5)
    print("hung: \(name): the waiting quit was never answered")
    fflush(stdout)
    exit(3)
}

let app = NSApplication.shared
app.setActivationPolicy(.prohibited)
let delegate = Delegate()
app.delegate = delegate
app.run()
