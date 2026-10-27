# Three particles

A synthetic, reproducible input for the first scientific journey: two dark
circles and a dark rectangle on a light background. These are drawn shapes,
not cells. This example checks data handling and agreement with Fiji; it cannot
establish biological accuracy.

From the repository root, with `uv` and Python 3.13 available:

```sh
uv run --locked --project services python examples/three-particles/example.py generate "$HOME/MicroscopeMind/examples/three-particles"
uv run --locked --project services python examples/three-particles/example.py check "$HOME/MicroscopeMind/examples/three-particles"
```

The first command uses the dependencies pinned by `services/uv.lock`. It may
download Python packages on first use; it needs no model, account, private asset,
camera or running Studio. It creates `particles-fixture.png` and a
`provenance.json` beside it. Generation requires an empty destination and never
replaces existing files. Choose another empty folder to regenerate.

Open the PNG in Preview, a browser or another ordinary image reader. It is
96 × 96 pixels, single-plane 8-bit grayscale, with background intensity 220
and shape intensity 40. Coordinates below are zero-based `(x, y)` pixel indices:

| Shape | Geometry | Occupied pixels |
| --- | --- | ---: |
| Large circle | Center (25, 25), radius 10 | 317 |
| Small circle | Center (65, 60), radius 7 | 149 |
| Rectangle | (70, 10) through (85, 22), inclusive; 16 × 13 | 208 |

There are three disconnected foreground components. Units are pixels; there
is no physical calibration. The counts in the table describe the generated
pixels, not an assertion that every analysis method uses the same area or
centroid definition.

`check` verifies the exact encoded file, decoded pixel hash, dimensions, dtype
and accompanying recipe record. Missing or changed files produce a nonzero
exit and a recovery instruction. It does not rewrite them. If generation
reports a different file hash, restore the locked dependency environment and
keep the failure; do not replace the reference hashes just to make it pass.

The [manifest](manifest.json) pins file/pixel hashes, the drawing recipe and its
original Git commit, author attribution and reference dependency versions.
Each generated provenance record also captures the generator's own SHA-256
and the actual Python, NumPy and OpenCV versions used. The unchanged drawing
recipe comes from the E3 Fiji binding's `fixture` function at
`19f8bdf078dad6c5dd4ae50924ebec028aa58fca`; the command can run without that
branch or Fiji installed.

Publication terms remain pending the founder's licence decision. The example
contains no third-party source image or private recording. Git attribution is
an authorship record, not independent rights clearance; the generator's
dependencies retain their own terms. The manifest grants no licence.

For the later Studio journey, open this PNG as an ordinary picture and retain
its original pixels. Its expected Fiji reference uses 8-bit conversion,
`Default` threshold and Analyze Particles minimum size 20 px², expecting three
particles. That execution, correction, restart/reopening and export round trip
have separate acceptance checks; this generator does not certify them. Follow
the Studio walkthrough for that journey and
its dated acceptance receipt.
