import AppKit

// The launch screen (packaging/macos/README.md "What happens on a Mac"). Its words and stages are SetupWords.swift's;
// this file only draws them, so `make native-test` can draw it too (packaging/macos/tests/setup-words).

enum SetupPhase: Int {
    case prepare, download, initialize, open, failed, stopping

    init(_ stage: SetupStage) {
        switch stage {
        case .setUp: self = .prepare
        case .download: self = .download
        case .getReady: self = .initialize
        case .open: self = .open
        }
    }

    /// The stage of the strip a step's words belong to (failed and stopping have none).
    var stage: SetupStage? {
        switch self {
        case .prepare: return .setUp
        case .download: return .download
        case .initialize: return .getReady
        case .open: return .open
        case .failed, .stopping: return nil
        }
    }
}

/// Native setup presentation. Stages are the launch's plan, ticked only when their step reports it ran; never an
/// estimated percentage.
final class SetupView: NSView {
    let title = NSTextField(wrappingLabelWithString: SetupWords.openTitle)
    let detail = NSTextField(wrappingLabelWithString: "")
    let spinner = NSProgressIndicator()
    let buttons = NSStackView()
    private let stages = NSStackView()
    private(set) var strip = SetupStrip(SetupPlan(SetupFacts(runtimeInstalled: true, earlierRuntime: false, pythonReady: true,
                                                             otherPython: false, warmed: true)), version: "")
    private var phase = SetupPhase.open
    private let note = NSTextField(labelWithString: "A workspace for discovery.")
    private let accent = NSColor(srgbRed: 0.66, green: 0.68, blue: 1, alpha: 1)

    init() {
        super.init(frame: NSRect(x: 0, y: 0, width: 900, height: 600))
        wantsLayer = true
        let icon = NSImageView(image: NSImage(named: "AppIcon") ?? NSApp.applicationIconImage)
        icon.imageScaling = .scaleProportionallyUpOrDown
        icon.setAccessibilityElement(false)
        icon.widthAnchor.constraint(equalToConstant: 100).isActive = true
        icon.heightAnchor.constraint(equalToConstant: 100).isActive = true
        let wordmark = NSTextField(labelWithString: "H O O K E")
        wordmark.font = .systemFont(ofSize: 11, weight: .semibold)
        wordmark.textColor = NSColor(srgbRed: 0.7, green: 0.71, blue: 0.78, alpha: 1)
        title.font = .systemFont(ofSize: 32, weight: .medium)
        title.textColor = NSColor(srgbRed: 0.94, green: 0.94, blue: 0.97, alpha: 1)
        title.alignment = .center
        title.preferredMaxLayoutWidth = 620
        detail.font = .systemFont(ofSize: 14)
        detail.textColor = NSColor(srgbRed: 0.67, green: 0.68, blue: 0.73, alpha: 1)
        detail.alignment = .center
        detail.preferredMaxLayoutWidth = 520
        detail.isSelectable = true
        detail.maximumNumberOfLines = 0
        spinner.style = .spinning
        spinner.controlSize = .small
        spinner.setAccessibilityLabel(SetupWords.openTitle)
        spinner.startAnimation(nil)
        stages.orientation = .horizontal
        stages.spacing = 22
        note.font = .systemFont(ofSize: 11)
        note.textColor = NSColor(srgbRed: 0.55, green: 0.57, blue: 0.63, alpha: 1)
        buttons.orientation = .horizontal
        buttons.spacing = 12
        let stack = NSStackView(views: [icon, wordmark, title, detail, stages, spinner, buttons])
        stack.orientation = .vertical
        stack.alignment = .centerX
        stack.spacing = 18
        stack.setCustomSpacing(30, after: wordmark)
        stack.setCustomSpacing(32, after: detail)
        stack.translatesAutoresizingMaskIntoConstraints = false
        note.translatesAutoresizingMaskIntoConstraints = false
        addSubview(stack)
        addSubview(note)
        NSLayoutConstraint.activate([
            stack.centerXAnchor.constraint(equalTo: centerXAnchor),
            stack.centerYAnchor.constraint(equalTo: centerYAnchor, constant: -12),
            stack.widthAnchor.constraint(equalTo: widthAnchor, constant: -96),
            title.widthAnchor.constraint(lessThanOrEqualToConstant: 620),
            detail.widthAnchor.constraint(lessThanOrEqualToConstant: 520),
            detail.widthAnchor.constraint(lessThanOrEqualTo: stack.widthAnchor),
            note.centerXAnchor.constraint(equalTo: centerXAnchor),
            note.bottomAnchor.constraint(equalTo: bottomAnchor, constant: -28),
        ])
        show(SetupWords.openTitle, "", .open)
    }

    required init?(coder: NSCoder) { nil }

    override func draw(_ dirtyRect: NSRect) {
        NSColor(srgbRed: 0.045, green: 0.049, blue: 0.065, alpha: 1).setFill()
        bounds.fill()
        if !NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast {
            let glow = NSGradient(starting: NSColor(srgbRed: 0.13, green: 0.14, blue: 0.27, alpha: 1),
                                  ending: NSColor(srgbRed: 0.045, green: 0.049, blue: 0.065, alpha: 1))!
            glow.draw(fromCenter: NSPoint(x: bounds.midX, y: bounds.midY + 130), radius: 0,
                      toCenter: NSPoint(x: bounds.midX, y: bounds.midY + 130), radius: 400, options: [.drawsAfterEndingLocation])
        }
    }

    /// A new launch's stages (begin, and Try again): only what this launch plans to do.
    func plan(_ plan: SetupPlan, version: String) {
        strip = SetupStrip(plan, version: version)
        drawStages()
    }

    /// A step's words. A step's own phase makes its stage the current one; it never ticks another.
    func show(_ heading: String, _ text: String, _ phase: SetupPhase) {
        self.phase = phase
        title.stringValue = heading
        detail.stringValue = text
        if let stage = phase.stage { strip.start(stage) }
        drawStages()
        spinner.setAccessibilityLabel(heading)
        spinner.isHidden = phase == .failed || NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        note.stringValue = phase == .failed ? "Your saved work stays on this Mac." : "A workspace for discovery."
        needsDisplay = true
    }

    /// The first launch's question, before anything starts: the planned stages, none of them running, and its two
    /// buttons (Return accepts; Quit quits).
    func ask(_ consent: SetupWords.Consent, target: AnyObject, accept: Selector) {
        phase = .open
        title.stringValue = consent.title
        detail.stringValue = consent.text
        drawStages()
        spinner.isHidden = true
        note.stringValue = "A workspace for discovery."
        buttons.arrangedSubviews.forEach { $0.removeFromSuperview() }
        let yes = NSButton(title: consent.accept, target: target, action: accept)
        yes.keyEquivalent = "\r"
        let quit = NSButton(title: consent.quit, target: NSApp, action: #selector(NSApplication.terminate(_:)))
        [quit, yes].forEach { buttons.addArrangedSubview($0) }
        needsDisplay = true
    }

    /// A step ended: its stage is ticked when it ran, and removed when it had nothing to do.
    func finish(_ phase: SetupPhase, ran: Bool) {
        guard let stage = phase.stage else { return }
        strip.finish(stage, ran: ran)
        drawStages()
    }

    private func drawStages() {
        stages.isHidden = phase == .failed || phase == .stopping || !strip.shown
        stages.arrangedSubviews.forEach { $0.removeFromSuperview() }
        for label in strip.labels {
            let field = NSTextField(labelWithString: label.text)
            field.textColor = label.current ? accent : NSColor(srgbRed: 0.59, green: 0.60, blue: 0.66, alpha: 1)
            field.font = .systemFont(ofSize: 12, weight: label.current ? .semibold : .regular)
            field.setAccessibilityLabel(label.accessibility)
            stages.addArrangedSubview(field)
        }
    }
}
