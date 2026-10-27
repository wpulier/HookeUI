// A placeholder app icon (an eyepiece with a few cells), until the Hooke icon is ready.
//   swift packaging/macos/placeholder-icon.swift OUT.png
import AppKit

let size = 1024.0
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let body = NSRect(x: 100, y: 100, width: 824, height: 824)
NSColor(white: 0, alpha: 0.25).setFill()
NSBezierPath(roundedRect: body.offsetBy(dx: 0, dy: -10), xRadius: 185, yRadius: 185).fill()
NSGradient(starting: NSColor(red: 0.10, green: 0.22, blue: 0.26, alpha: 1),
           ending: NSColor(red: 0.03, green: 0.08, blue: 0.10, alpha: 1))!
    .draw(in: NSBezierPath(roundedRect: body, xRadius: 185, yRadius: 185), angle: -90)
let disc = NSRect(x: 232, y: 232, width: 560, height: 560)
NSColor(red: 0.93, green: 0.89, blue: 0.78, alpha: 1).setFill()
NSBezierPath(ovalIn: disc).fill()
NSColor(red: 0.60, green: 0.80, blue: 0.80, alpha: 1).setStroke()
let ring = NSBezierPath(ovalIn: disc.insetBy(dx: -18, dy: -18))
ring.lineWidth = 22
ring.stroke()
for (x, y, r) in [(420.0, 560.0, 70.0), (590.0, 470.0, 55.0), (470.0, 390.0, 42.0), (610.0, 620.0, 34.0)] {
    let cell = NSBezierPath(ovalIn: NSRect(x: x - r, y: y - r, width: 2 * r, height: 2 * r))
    NSColor(red: 0.72, green: 0.42, blue: 0.55, alpha: 0.55).setFill()
    cell.fill()
    NSColor(red: 0.45, green: 0.18, blue: 0.32, alpha: 0.9).setStroke()
    cell.lineWidth = 8
    cell.stroke()
}
image.unlockFocus()
let rep = NSBitmapImageRep(data: image.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
