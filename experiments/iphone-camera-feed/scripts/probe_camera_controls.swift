import AVFoundation
import Foundation

let targetName = CommandLine.arguments.dropFirst().first ?? "iPhoneWild Camera"
let discovery = AVCaptureDevice.DiscoverySession(
    deviceTypes: [.external, .builtInWideAngleCamera],
    mediaType: .video,
    position: .unspecified
)
let devices = discovery.devices

guard let device = devices.first(where: { $0.localizedName == targetName }) else {
    print("target_found=false")
    print("target_name=\(targetName)")
    print("available_video_devices=\(devices.map { $0.localizedName }.joined(separator: ","))")
    exit(1)
}

func bool(_ value: Bool) -> String {
    value ? "true" : "false"
}

print("target_found=true")
print("name=\(device.localizedName)")
print("unique_id=\(device.uniqueID)")
print("model_id=\(device.modelID)")
print("device_type=\(device.deviceType.rawValue)")
print("position=\(device.position.rawValue)")
print("connected=\(bool(device.isConnected))")
print("suspended=\(bool(device.isSuspended))")

print("focus_locked_supported=\(bool(device.isFocusModeSupported(.locked)))")
print("focus_auto_supported=\(bool(device.isFocusModeSupported(.autoFocus)))")
print("focus_continuous_supported=\(bool(device.isFocusModeSupported(.continuousAutoFocus)))")
print("focus_point_supported=\(bool(device.isFocusPointOfInterestSupported))")
// Whether macOS reports the phone's own autofocus at all (the coach otherwise tells it from the
// eyepiece disc's size: services/orchestrator/coach.py). Run while the bridge is stopped.
print("focus_mode=\(device.focusMode.rawValue)")
print("adjusting_focus=\(bool(device.isAdjustingFocus))")

print("exposure_locked_supported=\(bool(device.isExposureModeSupported(.locked)))")
print("exposure_auto_supported=\(bool(device.isExposureModeSupported(.autoExpose)))")
print("exposure_continuous_supported=\(bool(device.isExposureModeSupported(.continuousAutoExposure)))")
print("exposure_custom_supported=\(bool(device.isExposureModeSupported(.custom)))")
print("exposure_point_supported=\(bool(device.isExposurePointOfInterestSupported))")
print("iso_range_available=false")
print("exposure_duration_range_available=false")

print("white_balance_locked_supported=\(bool(device.isWhiteBalanceModeSupported(.locked)))")
print("white_balance_continuous_supported=\(bool(device.isWhiteBalanceModeSupported(.continuousAutoWhiteBalance)))")

print("formats=\(device.formats.count)")
