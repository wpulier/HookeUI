#@ String bundleDir
#@ String reportPath
#@ Boolean (value=false) showWindows

// Independent consumer: no Studio imports, analysis rerun, global Results table or ROI Manager.
// Run with the recorded generation's Fiji: --console --run verify_exchange.groovy
// "bundleDir='/absolute/bundle',reportPath='/absolute/report.json',showWindows=true"
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import ij.IJ
import ij.ImagePlus
import ij.measure.Calibration
import ij.measure.Measurements
import ij.measure.ResultsTable
import ij.plugin.filter.Analyzer
import ij.plugin.filter.ThresholdToSelection
import ij.process.ImageProcessor
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.MessageDigest
import java.awt.GraphicsEnvironment

def sha = { byte[] bytes -> MessageDigest.getInstance('SHA-256').digest(bytes).encodeHex().toString() }
def require = { boolean yes, String why -> if (!yes) throw new IllegalArgumentException(why) }
def report = [ok: false, imagej: IJ.getVersion(), java: System.getProperty('java.version'),
              javaVendor: System.getProperty('java.vendor'), gui: !GraphicsEnvironment.isHeadless() && showWindows,
              verifier: 'verify_exchange.groovy/1', tolerance: 1e-6]
try {
    def dir = new File(bundleDir)
    def manifest = new JsonSlurper().parse(new File(dir, 'manifest.json'))
    require(manifest.schema == 'micromind-particle-exchange/1', 'Unsupported exchange schema')
    require(manifest.sourceFile in ['source.png', 'source.jpg'], 'Unsupported source filename')
    require(manifest.files.keySet() == [manifest.sourceFile, 'labels.tif', 'measurements.csv'].toSet(), 'Missing file hashes')
    manifest.files.each { name, hash -> require(sha(new File(dir, name).bytes) == hash, name + ' checksum differs') }
    require(manifest.execution instanceof String && manifest.execution && manifest.revision >= 0, 'Missing provenance')
    ImagePlus labels = IJ.openImage(new File(dir, 'labels.tif').absolutePath)
    ImagePlus source = IJ.openImage(new File(dir, manifest.sourceFile).absolutePath)
    require(labels != null && source != null, 'Image could not be opened')
    require(labels.type == ImagePlus.GRAY16 && source.type == ImagePlus.GRAY8, 'Incorrect label or source dtype')
    require(labels.stackSize == 1 && source.stackSize == 1, 'Only a single YX plane is supported')
    require(labels.width == source.width && labels.height == source.height, 'Image dimensions differ')
    require([source.width, source.height] == [manifest.source.width, manifest.source.height], 'Manifest dimensions differ')
    require(manifest.labels.units == 'pixels' && manifest.labels.background == 0 && manifest.source.units == 'pixels', 'Units/background differ')
    require(sha(new File(dir, manifest.sourceFile).bytes) == manifest.source.sha256, 'Original source bytes differ')
    require(sha(source.getProcessor().getPixels() as byte[]) == manifest.source.pixelSha256, 'Original source pixel values differ')
    short[] values = labels.getProcessor().getPixels() as short[]
    def packed = ByteBuffer.allocate(values.length * 2).order(ByteOrder.LITTLE_ENDIAN)
    def present = new HashSet<Integer>()
    values.each { short value -> packed.putShort(value); if ((value & 0xffff) != 0) present.add(value & 0xffff) }
    require(sha(packed.array()) == manifest.labels.pixelSha256, 'Exact label pixels differ')
    def mapping = manifest.members.collectEntries { [(it.label as int): it.memberID] }
    require(mapping.size() == manifest.members.size() && mapping.values().toSet().size() == mapping.size(), 'Duplicate member mapping')
    require(mapping.keySet() == present && mapping.keySet().every { it > 0 && it <= 65535 }, 'Missing or unexpected labels')
    ResultsTable exported = ResultsTable.open(new File(dir, 'measurements.csv').absolutePath)
    require(exported.size() == mapping.size(), 'CSV row count differs')
    ResultsTable measured = new ResultsTable()
    measured.setPrecision(12)
    Calibration pixels = new Calibration()
    pixels.setUnit('pixel')
    source.setCalibration(pixels)
    labels.setCalibration(pixels.copy())
    def seen = new HashSet<Integer>()
    def observations = []
    for (int i = 0; i < exported.size(); i++) {
        double number = exported.getValue('label', i)
        int n = number as int
        require(number == n && !seen.contains(n) && mapping.containsKey(n), 'Duplicate or invalid CSV label')
        seen.add(n)
        String member = exported.getStringValue('memberID', i)
        require(member == mapping[n], 'CSV member identity differs')
        def mask = labels.getProcessor().duplicate()
        mask.setThreshold(n, n, ImageProcessor.NO_LUT_UPDATE)
        def roi = new ThresholdToSelection().convert(mask)
        require(roi != null, 'Label has no region')
        source.setRoi(roi)
        new Analyzer(source, Measurements.AREA | Measurements.CENTROID | Measurements.MEAN, measured).measure()
        def row = [memberID: member, label: n, areaPx2: measured.getValue('Area', i),
                   centroidX: measured.getValue('X', i), centroidY: measured.getValue('Y', i),
                   meanIntensity: measured.getValue('Mean', i)]
        require(row.areaPx2 == exported.getValue('areaPx2', i), 'Occupied area differs')
        ['centroidX', 'centroidY', 'meanIntensity'].each { key ->
            double expected = exported.getValue(key, i)
            require(Double.isFinite(expected) && Math.abs(row[key] - expected) <= 1e-6, key + ' differs')
        }
        measured.setValue('memberID', i, member)
        measured.setValue('label', i, n)
        observations << row
    }
    source.deleteRoi()
    report += [ok: true, session: manifest.session, evidence: manifest.evidence, revision: manifest.revision,
               execution: manifest.execution, sourceSha256: manifest.source.sha256,
               labelsPixelSha256: manifest.labels.pixelSha256, width: source.width, height: source.height,
               count: measured.size(), units: 'pixels', rows: observations,
               definitions: 'ImageJ Analyzer AREA/CENTROID/MEAN on ROIs reconstructed from exact labels; private ResultsTable']
    if (showWindows) {
        require(!GraphicsEnvironment.isHeadless(), 'A GUI was requested but Fiji is headless')
        source.setTitle('Exchange original source'); source.show()
        labels.setTitle('Exchange labels (uint16)'); labels.show()
        exported.show('Exchange exported measurements')
        measured.show('Exchange independently measured')
    }
} catch (Throwable failure) {
    report.ok = false; report.error = failure.class.simpleName + ': ' + failure.message
}
new File(reportPath).text = JsonOutput.prettyPrint(JsonOutput.toJson(report))
println('EXCHANGE ' + JsonOutput.toJson(report))
