// The app's Fiji worker (services/fiji_worker; orchestrator/fiji_bindings.py): reviewed bindings only, headless, one
// request per line of JSON on stdin, one reply per line on stdout. Run by the managed Fiji of a generation
// (orchestrator/fiji_generations.py): `fiji --headless --console --run server.groovy`.
//
// Analyze Particles (binding "fiji-analyze-particles"): the image is passed explicitly (never WindowManager), and the
// particles come back through the image's Overlay, never the ROI Manager window (PyImageJ's documented headless limits).
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import ij.IJ
import ij.ImagePlus
import ij.measure.Measurements
import ij.measure.ResultsTable
import ij.plugin.filter.ParticleAnalyzer
import ij.process.ImageProcessor
import ij.process.ImageStatistics
import ij.gui.Roi
import ij.measure.Calibration
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.zip.DeflaterOutputStream

// ParticleAnalyzer's native mask passed to drawParticle is the same mask used for its statistics,
// including excluded holes. An outer ROI polygon alone is not that mask. No pixel-coordinate conversion.
class PixelParticles extends ParticleAnalyzer {
    short[] labels
    int width, height
    List found = []
    PixelParticles(int options, ResultsTable table, double min, double max, int w, int h) {
        super(options, Measurements.AREA | Measurements.CENTROID | Measurements.MEAN, table, min, max, 0.0d, 1.0d)
        width = w; height = h; labels = new short[w*h]
    }
    @Override
    protected void drawParticle(ImageProcessor drawIP, Roi roi, ImageStatistics stats, ImageProcessor mask) {
        int label = found.size() + 1
        if (label > 65535) throw new IllegalStateException("Too many particles for uint16 labels")
        def bounds = roi.getBounds()
        if (bounds.x < 0 || bounds.y < 0 || bounds.x + bounds.width > width || bounds.y + bounds.height > height)
            throw new IllegalStateException("Particle outside image grid")
        if (mask != null && (mask.getWidth() != bounds.width || mask.getHeight() != bounds.height))
            throw new IllegalStateException("Particle mask dimensions differ")
        int occupied = 0
        for (int y = 0; y < bounds.height; y++) for (int x = 0; x < bounds.width; x++) {
            if (mask != null && mask.get(x,y) == 0) continue
            int at = (bounds.y+y)*width + bounds.x+x
            if (labels[at] != 0) throw new IllegalStateException("Overlapping particle masks")
            labels[at] = (short)label; occupied++
        }
        if (occupied != stats.pixelCount) throw new IllegalStateException("Particle area differs from its native mask")
        def poly = roi.getFloatPolygon()
        def pts = (0..<poly.npoints).collect { [poly.xpoints[it] as double, poly.ypoints[it] as double] }
        found << [label: label, area: stats.area, cx: stats.xCentroid, cy: stats.yCentroid, mean: stats.mean, polygon: pts]
        super.drawParticle(drawIP, roi, stats, mask)
    }
}

def particles(Map req) {
    ImagePlus imp = IJ.openImage(req.image as String)
    if (imp == null) throw new IllegalArgumentException("the image could not be opened")
    if (imp.getStackSize() != 1 || imp.getWidth() * (long)imp.getHeight() > 16000000L)
        throw new IllegalArgumentException("Expected one image plane up to 16 million pixels")
    imp.setCalibration(new Calibration()) // input/crop pixel units, never metadata/global physical units
    if (imp.getType() != ImagePlus.GRAY8) IJ.run(imp, "8-bit", "")
    ImageProcessor ip = imp.getProcessor()
    if (req.threshold instanceof List) {
        ip.setThreshold(req.threshold[0] as double, req.threshold[1] as double, ImageProcessor.NO_LUT_UPDATE)
    } else {
        ip.setAutoThreshold((req.method ?: "Default") + (req.dark ? " dark" : ""))
    }
    double lo = ip.getMinThreshold(), hi = ip.getMaxThreshold()
    int options = ParticleAnalyzer.OVERLAY | ParticleAnalyzer.SHOW_NONE
    if (req.exclude_edges) options |= ParticleAnalyzer.EXCLUDE_EDGE_PARTICLES
    ResultsTable rt = new ResultsTable()
    PixelParticles pa = new PixelParticles(options, rt,
            (req.min_size ?: 0) as double, (req.max_size ?: Double.POSITIVE_INFINITY) as double, imp.getWidth(), imp.getHeight())
    pa.setHideOutputImage(true)
    if (!pa.analyze(imp, ip)) throw new IllegalStateException("Analyze Particles did not run")
    if (pa.found.size() != rt.size()) throw new IllegalStateException("Incomplete particle masks")
    ByteBuffer raw = ByteBuffer.allocate(pa.labels.length * 2).order(ByteOrder.LITTLE_ENDIAN)
    raw.asShortBuffer().put(pa.labels)
    ByteArrayOutputStream compressed = new ByteArrayOutputStream()
    DeflaterOutputStream deflater = new DeflaterOutputStream(compressed)
    deflater.write(raw.array()); deflater.close()
    return [ok: true, particles: pa.found, threshold: [lo, hi], width: imp.getWidth(), height: imp.getHeight(),
            maskContract: "imagej-particle-pixels-v1",
            labels: [dtype: "<u2", shape: [imp.getHeight(), imp.getWidth()], base64: Base64.getEncoder().encodeToString(compressed.toByteArray())],
            runtime: [imagej: IJ.getVersion(), java: System.getProperty("java.version"), javaVendor: System.getProperty("java.vendor")]]
}

def reader = new BufferedReader(new InputStreamReader(System.in))
println(JsonOutput.toJson([ok: true, ready: IJ.getVersion()])); System.out.flush()
String line
while ((line = reader.readLine()) != null) {
    def reply
    try {
        def req = new JsonSlurper().parseText(line) as Map
        if (req.op == "particles") reply = particles(req)
        else if (req.op == "ping") reply = [ok: true, version: IJ.getVersion()]
        else reply = [ok: false, error: "unknown op " + req.op]
    } catch (Throwable e) {
        reply = [ok: false, error: e.getClass().getSimpleName() + ": " + e.getMessage()]
    }
    println("@@" + JsonOutput.toJson(reply)); System.out.flush()
}
