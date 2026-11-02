# Image intensity summary — review candidate

One deterministic, CPU-only catalog capability. Input: the explicitly selected
RGB8 image. Output: three rows with per-channel range, mean and fractions equal to
0 and 255. These are image-value diagnostics, not biological conclusions or a
calibrated test of acquisition saturation. There are no network, file, subprocess,
model or credential calls in the adapter.

The recipe pins Python 3.13.5 and NumPy 2.3.4. It allocates a one-channel Boolean
mask at a time, in addition to its admitted input. No image data travels in this
package. Code is Apache-2.0; NumPy retains its own BSD licence. No weights.

The repository tests compare a literal two-pixel reference and reject unsupported
data. They do not establish installed sandbox proof. This folder is outside the
active catalog and is not a runnable entry. Prepare, test and review its exact
package through the existing catalog lifecycle before activating it. Only the
owner can approve dependencies, resources, permissions and activation. Community
publication is a separate action.
