// Independent reference: unmodified ParticleAnalyzer's count-mask output, private ResultsTable.
// Deliberately does not load the production worker or fill its polygons.
#@ String imagePath
#@ String outputDir
#@ Double minSize
import groovy.json.JsonOutput
import ij.IJ
import ij.ImagePlus
import ij.measure.Calibration
import ij.measure.Measurements
import ij.measure.ResultsTable
import ij.plugin.filter.ParticleAnalyzer
import ij.io.FileSaver
import ij.process.ShortProcessor

try {
    ImagePlus image = IJ.openImage(imagePath)
    if (image == null || image.getStackSize() != 1) throw new IllegalArgumentException("Expected one input plane")
    image.setCalibration(new Calibration())
    if (image.getType() != ImagePlus.GRAY8) IJ.run(image, "8-bit", "")
    image.getProcessor().setAutoThreshold("Default")
    ResultsTable table = new ResultsTable()
    ParticleAnalyzer reference = new ParticleAnalyzer(ParticleAnalyzer.SHOW_ROI_MASKS,
        Measurements.AREA | Measurements.CENTROID | Measurements.MEAN, table, minSize, Double.POSITIVE_INFINITY, 0.0d, 1.0d)
    reference.setHideOutputImage(true)
    if (!reference.analyze(image, image.getProcessor())) throw new IllegalStateException("Reference did not run")
    if (table.size() > 65535) throw new IllegalStateException("Too many reference labels")
    ImagePlus labels = reference.getOutputImage()
    if (labels == null && table.size() == 0) labels = new ImagePlus("empty labels", new ShortProcessor(image.getWidth(), image.getHeight()))
    if (labels == null || !new FileSaver(labels).saveAsTiff(new File(outputDir, "reference-labels.tif").getPath()))
        throw new IllegalStateException("Reference masks could not be saved")
    def particles = []
    for (int i=0; i<table.size(); i++) particles << [label: i+1, area: table.getValue("Area", i),
        cx: table.getValue("X", i), cy: table.getValue("Y", i), mean: table.getValue("Mean", i)]
    new File(outputDir, "reference.json").text = JsonOutput.toJson([ok:true, particles:particles,
        width:image.getWidth(), height:image.getHeight(), imagej:IJ.getVersion()])
} catch (Throwable error) {
    new File(outputDir, "reference.json").text = JsonOutput.toJson([ok:false, error:error.toString()])
}
