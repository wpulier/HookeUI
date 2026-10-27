import AppKit

/// Quitting when `applicationShouldTerminate` answered `.terminateLater`: AppKit then waits for
/// `reply(toApplicationShouldTerminate:)` in a run loop nested inside `terminate`. When that `terminate` was called from a
/// main-queue block (a check's completion: Restart Now, the workspace's Restart to update), the main dispatch queue
/// cannot run again until that block returns, so an answer sent with `DispatchQueue.main.async` never arrives and the app
/// stays on "Closing your workspace" (the 0.4.1 rehearsal, build 4695). A run-loop block in the common modes runs inside
/// AppKit's wait whichever way the quit began. `make native-test` checks both ways with a real NSApplication
/// (packaging/macos/tests/termination).
enum Termination {
    /// Answers the waiting quit, from any thread.
    static func reply(_ proceed: Bool) {
        onMainRunLoop { NSApp.reply(toApplicationShouldTerminate: proceed) }
    }

    /// Starts a quit after the block running now returns, so AppKit's wait is not nested inside it.
    static func terminate() {
        onMainRunLoop { NSApp.terminate(nil) }
    }

    private static func onMainRunLoop(_ block: @escaping () -> Void) {
        let main = CFRunLoopGetMain()
        CFRunLoopPerformBlock(main, CFRunLoopMode.commonModes.rawValue, block)
        CFRunLoopWakeUp(main)
    }
}
