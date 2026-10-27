// MicroscopeMind Camera: the iPhone's picture through Continuity Camera, captured by a real app.
//
// macOS wakes the iPhone's Continuity Camera only for an app with its own identity (a bundle ID). A bare
// command-line capture (FFmpeg) is an anonymous client ("missing bundleID" in ContinuityCaptureAgent's log):
// it gets frames only while something else keeps the phone's session awake, and nothing when the session
// has gone to sleep (2026-10-11: FFmpeg 0 frames in 40 s, this app its first frame in 3.3 s, side by side).
//
// The bridge launches this app through LaunchServices (`open`, so it keeps its identity and its own camera
// permission) with its stdout on a pipe, and reads JPEG frames from it exactly as it read FFmpeg's:
//   capture --device "iPhoneWild Camera" --size 1280x720 --fps 30 --quality 0.8
// It exits when the device is gone, when its reader goes away (a broken pipe), or on SIGTERM.
import AVFoundation
import VideoToolbox
import Foundation

signal(SIGPIPE, SIG_IGN)  // a closed pipe surfaces as a write error, handled below

func argument(_ name: String, _ fallback: String) -> String {
    let args = CommandLine.arguments
    if let i = args.firstIndex(of: name), i + 1 < args.count { return args[i + 1] }
    return fallback
}

let deviceName = argument("--device", "iPhoneWild Camera")
let size = argument("--size", "1280x720").split(separator: "x").compactMap { Int($0) }
let fps = Double(argument("--fps", "30")) ?? 30
let quality = Double(argument("--quality", "0.8")) ?? 0.8
let stderr = FileHandle.standardError
func say(_ text: String) { stderr.write(("[capture] " + text + "\n").data(using: .utf8)!) }

// JPEG on the Mac's hardware encoder (VideoToolbox): a software encode of each frame held the first version to
// about 11 fps.
final class Writer: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    var session: VTCompressionSession?
    var width: Int32 = 0, height: Int32 = 0
    let out = FileHandle.standardOutput
    var frames = 0
    let started = Date()
    // At most one frame encoding or being written: a frame that arrives meanwhile is dropped, so frames never wait
    // in line here when the bridge or the pipe is slow (the audit of 2026-10-12: newest only, end to end).
    let gate = NSLock()
    var inFlight = false

    func encoder(for pixels: CVPixelBuffer) -> VTCompressionSession? {
        let w = Int32(CVPixelBufferGetWidth(pixels)), h = Int32(CVPixelBufferGetHeight(pixels))
        if let session = session, w == width, h == height { return session }
        if let old = session { VTCompressionSessionInvalidate(old) }
        var made: VTCompressionSession?
        VTCompressionSessionCreate(allocator: nil, width: w, height: h, codecType: kCMVideoCodecType_JPEG,
                                   encoderSpecification: nil, imageBufferAttributes: nil, compressedDataAllocator: nil,
                                   outputCallback: nil, refcon: nil, compressionSessionOut: &made)
        guard let created = made else { return nil }
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_Quality, value: NSNumber(value: quality))
        VTSessionSetProperty(created, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        session = created; width = w; height = h
        return created
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer), let encoder = encoder(for: pixels) else { return }
        gate.lock()
        if inFlight { gate.unlock(); return }
        inFlight = true
        gate.unlock()
        let status = VTCompressionSessionEncodeFrame(encoder, imageBuffer: pixels,
                                                     presentationTimeStamp: CMSampleBufferGetPresentationTimeStamp(sampleBuffer),
                                                     duration: .invalid, frameProperties: nil, infoFlagsOut: nil) { status, _, encoded in
            defer { self.gate.lock(); self.inFlight = false; self.gate.unlock() }
            guard status == noErr, let encoded = encoded, let block = CMSampleBufferGetDataBuffer(encoded) else { return }
            var length = 0
            var pointer: UnsafeMutablePointer<CChar>?
            guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil, totalLengthOut: &length,
                                              dataPointerOut: &pointer) == noErr, let bytes = pointer else { return }
            let jpeg = Data(bytes: bytes, count: length)
            do {
                try self.out.write(contentsOf: jpeg)
            } catch {
                say("the reader went away; stopping")
                exit(0)
            }
            self.frames += 1
            if self.frames == 1 { say(String(format: "first frame after %.2f s", Date().timeIntervalSince(self.started))) }
        }
        if status != noErr { gate.lock(); inFlight = false; gate.unlock() }  // not encoding: its handler won't run
    }
}

let discovery = AVCaptureDevice.DiscoverySession(deviceTypes: [.continuityCamera, .external, .builtInWideAngleCamera],
                                                mediaType: .video, position: .unspecified)
guard let device = discovery.devices.first(where: { $0.localizedName == deviceName }) else {
    say("no camera called \(deviceName); seen: \(discovery.devices.map { $0.localizedName }.joined(separator: ", "))")
    exit(3)
}
if AVCaptureDevice.authorizationStatus(for: .video) == .denied {
    say("camera access is denied for MicroscopeMind Camera: allow it in System Settings > Privacy & Security > Camera")
    exit(4)
}
let session = AVCaptureSession()
session.sessionPreset = (size.count == 2 && size[0] == 1280 && size[1] == 720) ? .hd1280x720 : .high
do {
    session.addInput(try AVCaptureDeviceInput(device: device))
} catch {
    say("could not open \(deviceName): \(error.localizedDescription)")
    exit(5)
}
let output = AVCaptureVideoDataOutput()
output.alwaysDiscardsLateVideoFrames = true  // the newest picture, never a queue of old ones
// The size the bridge asks for, always: the preset is only a request, and after a Continuity reset the phone sent
// 1920x1080 (2026-10-12), 2.25x the pixels the whole app is built for, and the studio fell behind (19.5 of 30 fps).
// The output scales in hardware, keeping the aspect ratio.
var videoSettings: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange]
if size.count == 2 {
    videoSettings[kCVPixelBufferWidthKey as String] = size[0]
    videoSettings[kCVPixelBufferHeightKey as String] = size[1]
}
output.videoSettings = videoSettings
let writer = Writer()
output.setSampleBufferDelegate(writer, queue: DispatchQueue(label: "frames", qos: .userInteractive))
session.addOutput(output)
// The frame rate the bridge asks for, exactly (the session's preset alone ran the phone at about 40 fps).
func pinFrameRate() {
    guard (try? device.lockForConfiguration()) != nil else { return }
    let duration = CMTime(value: 1, timescale: CMTimeScale(fps))
    if device.activeFormat.videoSupportedFrameRateRanges.contains(where: { $0.minFrameRate <= fps && fps <= $0.maxFrameRate }) {
        device.activeVideoMinFrameDuration = duration
        device.activeVideoMaxFrameDuration = duration
    }
    device.unlockForConfiguration()
}
NotificationCenter.default.addObserver(forName: AVCaptureDevice.wasDisconnectedNotification, object: device, queue: nil) { _ in
    say("\(deviceName) disconnected")
    exit(6)
}
signal(SIGTERM) { _ in exit(0) }
session.startRunning()
pinFrameRate()
say("capturing \(deviceName)")
RunLoop.main.run()
