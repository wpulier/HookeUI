// Fiji functions as catalog folders (orchestrator/fiji_runner.py; ADR 0024's one door): one headless Fiji of the
// generation in use, one request per line of JSON on stdin, one reply per line on stdout ("@@" + JSON). Kept apart from
// server.groovy (the reviewed Analyze Particles binding), so adding a function never changes that binding's proof.
//
// A function is an ImageJ command with its options as the macro recorder writes them, or a folder's own Groovy script
// (its sha256 checked before it is run), on an image passed explicitly: never WindowManager, a fresh image each call,
// the system Results table cleared before and read after, so nothing of one call reaches the next.
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
import ij.IJ
import ij.ImagePlus
import ij.Menus
import ij.measure.Calibration
import ij.measure.ResultsTable
import java.security.MessageDigest

def sha256(File f) {
    MessageDigest.getInstance("SHA-256").digest(f.bytes).collect { String.format("%02x", it) }.join()
}

def table() {
    ResultsTable rt = ResultsTable.getResultsTable()
    if (rt == null) return [columns: [], rows: []]
    def headings = rt.getHeadings().findAll { it != " " && it != "Label" } as List
    def rows = (0..<rt.size()).collect { i -> headings.collect { h -> rt.getValue(h, i) } }
    return [columns: headings.collect { [name: it] }, rows: rows]
}

def run(Map req) {
    ImagePlus imp = IJ.openImage(req.image as String)
    if (imp == null) throw new IllegalArgumentException("the image could not be opened")
    if (imp.getStackSize() != 1 || imp.getWidth() * (long)imp.getHeight() > 16000000L)
        throw new IllegalArgumentException("Expected one image plane up to 16 million pixels")
    imp.setCalibration(new Calibration()) // the picture's own pixels, never another image's units
    ResultsTable.getResultsTable()?.reset()
    if (req.script) {
        File script = new File(req.script as String)
        if (sha256(script) != req.scriptSha256) throw new SecurityException("its script changed since it was proven")
        def binding = new Binding([imp: imp, params: req.params ?: [:], IJ: IJ])
        def made = new GroovyShell(binding).evaluate(script)
        if (made instanceof ImagePlus) imp = made
    } else {
        IJ.run(imp, req.command as String, (req.options ?: "") as String)
    }
    if (req.output == "table.objects") return [ok: true, kind: "table"] + table()
    IJ.saveAs(imp, "PNG", req.out as String)
    return [ok: true, kind: "picture", path: req.out, width: imp.getWidth(), height: imp.getHeight()]
}

def reader = new BufferedReader(new InputStreamReader(System.in))
println(JsonOutput.toJson([ok: true, ready: IJ.getVersion()])); System.out.flush()
String line
while ((line = reader.readLine()) != null) {
    def reply
    try {
        def req = new JsonSlurper().parseText(line) as Map
        if (req.op == "run") reply = run(req)
        else if (req.op == "has_command") reply = [ok: true, found: Menus.getCommands()?.containsKey(req.command as String) ?: false]
        else if (req.op == "ping") reply = [ok: true, version: IJ.getVersion()]
        else reply = [ok: false, error: "unknown op " + req.op]
    } catch (Throwable e) {
        reply = [ok: false, error: e.getClass().getSimpleName() + ": " + e.getMessage()]
    }
    println("@@" + JsonOutput.toJson(reply)); System.out.flush()
}
System.exit(0) // stdin closed: the app is done with this worker
