"""The H14 tasks as data, copied from the frozen design (qualification/h14-study-design.md §4).

Each task has its cap, the groups whose `qualify-usable` child it covers (§4.4), its cache condition (§5), and the
goal the participant reads. A card states the goal in the researcher's terms and never names a menu, a control or a
shortcut (§4.1); `card()` fills in one variant's values. Caps here are the frozen values; the pilot may only raise
them, through an amendment and the binding manifest (§8), so the manifest's caps win when given.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

FORBIDDEN_IN_CARDS = re.compile(r"\b(menu|button|click|toolbar|shortcut|ctrl|cmd|command-|console|panel|tab|"
                                r"dock|widget|slider|checkbox|dropdown|right-click|layer list|ctrl\+|⌘)\b", re.I)


@dataclass(frozen=True)
class Task:
    id: str
    session: int
    cap_s: int | None          # None: written into the binding manifest (T14)
    groups: tuple[str, ...]
    cache: str
    start: str
    goal: str                  # participant wording; {placeholders} come from the variant's parameters
    comparison: bool = True


TASKS = (
    Task("T0", 1, 25 * 60, ("P8.install", "Q.clean-preparation"), "cold",
         "fresh macOS user account, neither app installed",
         "Install the app from the file in Downloads and get it ready to find nuclei with Cellpose-SAM. Then use it on "
         "the image check.tif in Downloads and keep the result. Say \"done\" when it is ready and the result is kept."),
    Task("T1", 1, 6 * 60, ("P1.axis-position", "P1.axis-order", "P1.calibrated-coordinates", "P1.playback"), "prepared-warm",
         "five-dimensional image open",
         "Show the image with {order} as the displayed axes. Play through time and stop at time point {t}. Go to the "
         "channel named \"{channel}\" and the depth where the brightest of the marked objects is. Tell us its pixel "
         "value and its position in micrometres."),
    Task("T2", 1, 6 * 60, ("P1.slab-projection", "P3.common-appearance", "P3.image", "P3.lifecycle", "P7.samples-commands"),
         "prepared-warm", "app open, nothing loaded",
         "Open the app's own \"{sample}\" sample data, then open five-d.tif. Show the maximum of {slab} depth planes "
         "centred on depth {z}. Give channel \"{c1}\" the display range {r1} and the colour {cmap1}; give channel "
         "\"{c2}\" the display range {r2} and the colour {cmap2}; hide channel \"{c3}\"."),
    Task("T3", 1, 8 * 60, ("P1.display-mode", "P2.camera", "P2.volume-modes", "P2.volume-parameters",
                           "P2.clipping-planes", "P2.mixed-picking"), "prepared-warm", "volume open in 2D",
         "Look at the volume in 3D as a maximum projection, then as an isosurface at threshold {threshold}. Cut it "
         "with a plane at depth {depth} so the part above is hidden. Then tell us the label number of the object under "
         "the red marker, by pointing at it in the 3D view."),
    Task("T4", 1, 8 * 60, ("P2.surface-render", "P3.surface", "P3.vectors", "P3.tracks", "P3.out-of-slice",
                           "P3.transforms", "P3.grid-linked-views"), "prepared-warm", "mixed scene open",
         "In this scene: which track is the parent of track {child} after the division? What value does the surface "
         "have at the marked vertex? How long is the marked arrow? Then make points from other depths visible at "
         "half their size, and show all the data side by side in a grid."),
    Task("T5", 1, 8 * 60, ("P5.lazy-open", "P5.multiscale", "P5.volume-level", "P5.fast-navigation", "P7.readers",
                           "W.ome-zarr"), "cold-os-cache", "app open, nothing loaded",
         "Open the large dataset big.ome.zarr. Go to the region marked in the overview, at full resolution, and tell "
         "us the pixel value at position x {x}, y {y} on depth {z}."),
    Task("T6", 1, 8 * 60, ("P3.points", "P3.shapes", "P4.point-edit", "P4.shape-create", "P4.shape-edit"),
         "prepared-warm", "image open",
         "Put a point on the centre of each of the {n} marked cells. Draw a rectangle around region A and a polygon "
         "around region B. Then put the polygon behind the rectangle."),
    Task("T7", 1, 10 * 60, ("P3.labels", "P4.label-tools", "P4.label-brush", "P4.label-fill-preserve",
                            "P4.label-history"), "prepared-warm", "labels open over the image",
         "Fix these nucleus labels. Split the merged nucleus marked M into two. Delete the false object marked F. "
         "Fill the hole in the nucleus marked H. Then erase the nucleus marked X on purpose, and take that back so "
         "it is as it was."),
    Task("T8", 2, 10 * 60, ("W.cellposesam", "P7.forms", "P7.dynamic-controls", "P7.background", "Q.operation-proof"),
         "prepared-warm", "image open, Cellpose-SAM prepared",
         "Find the nuclei in this image with Cellpose-SAM, using a diameter of {diameter} pixels and the "
         "\"{channel}\" channel. Keep the result."),
    Task("T9", 2, 8 * 60, ("P7.writers", "W.relations-calibration", "P5.incremental-workflow"), "prepared-warm",
         "reference labels open beside the image",
         "Measure each nucleus in these labels: its area in square micrometres and its mean intensity. Save the "
         "table as a CSV file called measurements.csv in the Results folder."),
    Task("T10", 2, 10 * 60, ("W.animation", "P7.custom-panel"), "prepared-warm", "3D+t image open",
         "Make a movie with three key views: {k1}; then {k2}; then {k3}. Use {steps} steps between key views. Save it "
         "as movie.mp4 in the Results folder."),
    Task("T11", 2, 6 * 60, ("P7.panel-lifecycle", "W.connected"), "prepared-warm", "this session's work, unsaved",
         "Erase the nucleus marked E1. Save all your work, quit the app, open it again, and erase the nucleus marked "
         "E2."),
    Task("T12", 2, 10 * 60, ("P6.console-session", "P6.arrays", "P6.bidirectional", "P6.callbacks", "P6.interrupt",
                             "P6.scripts-notebooks"), "prepared-warm", "image and labels open",
         "Using Python inside the app: add the maximum projection of the image along depth as new data. Change "
         "label {old} to {new} in the labels and see it change. Then run the file long_loop.py, stop it before it "
         "ends, and check that the variable `answer` you set first still holds {answer}."),
    Task("T13", 2, 8 * 60, ("P5.remote-recovery", "Q.failure-isolation"), "prepared-warm", "remote dataset open",
         "Keep working with the remote dataset. Tell us the pixel value at position x {x}, y {y} on depth {z}. "
         "If something goes wrong, get it working again yourself."),
    Task("T14", 2, None, ("P7.held-out",), "prepared-warm", "written into the binding manifest",
         "Written into the binding manifest when the held-out extension is chosen (H10)."),
    Task("U1", 2, None, ("P4.edit-conflicts",), "prepared-warm", "labels open; a second editor changes them",
         "Someone else changed these labels while you were editing them. Keep your own work, and decide which "
         "version to keep.", comparison=False),
    Task("U2", 2, None, ("W.semantic-refusal",), "prepared-warm", "probability map and image open",
         "Measure the mean intensity of each nucleus, using {wrong} as the intensity. If that can't be done, find "
         "out why, and measure using the right data instead.", comparison=False),
    Task("U3", 2, None, ("W.projects",), "prepared-warm", "trained project open",
         "Apply the trained model in this project to new.tif. Then update the model, and go back to the version "
         "you started with.", comparison=False),
    Task("U4", 2, None, ("W.partial-batches",), "prepared-warm", "a batch with failed items",
         "Some items in this batch failed. Run only the failed items again.", comparison=False),
    Task("U5", 2, None, ("P8.updates",), "prepared-warm", "an update is available for an installed package",
         "Update the package {package}. Find out what changed. Then go back to the version you had.", comparison=False),
    Task("U6", 2, None, ("P8.exchange",), "prepared-warm", "a project from another computer in Downloads",
         "Open the project in Downloads, which was made on another computer, and look at its data.", comparison=False),
    Task("U7", 2, None, ("Q.catalog-admission",), "prepared-warm", "app open",
         "Find out whether \"{entry}\" is qualified to use, and if it isn't, what it is missing.", comparison=False),
)
BY_ID = {t.id: t for t in TASKS}
U_CAP_S = 8 * 60  # Hooke-only tasks share one cap; the design leaves it to the binding manifest, this is the proposal


def card(task_id: str, params: dict) -> str:
    t = BY_ID[task_id]
    text = t.goal.format(**params)
    if FORBIDDEN_IN_CARDS.search(text) and task_id != "T12":   # T12's goal names Python, which is the task itself
        raise ValueError(f"{task_id}: a card names a control: {FORBIDDEN_IN_CARDS.search(text).group(0)!r}")
    return text


def coverage() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for t in TASKS:
        for g in t.groups:
            out.setdefault(g, []).append(t.id)
    return out
