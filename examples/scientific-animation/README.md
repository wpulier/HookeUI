# Private napari-animation integration candidate

This is an unfinished integration using the existing `scientific-host-v1` context,
native-panel and tool-document interfaces. It is outside catalog discovery and
does not establish readiness, a completed package journey or H12 acceptance.

`adapter.py` uses napari-animation **0.0.10**'s actual `AnimationWidget`, keyframe
list, easing and interpolation. A package-local widget subclass scopes the slider's
signal block while changing its frame range: the pinned upstream callback otherwise
leaves signals blocked after editing Steps. It preserves an already blocked caller
and does not alter interpolation or keyboard-focus policy. Its explicit data-only document records keyframe
order, names, timing, easing, viewer settings, selection and the next name counter.
Scientific arrays remain separate retained resources. Unknown state types refuse
saving instead of being omitted. Opening a saved document attaches to accepted
layers; it does not rerun a segmentation or deserialize Python state.

Current bounds are 128 keyframes, 4,096 movie frames and the host's existing 64 KiB
tool-document / 64 MiB native-layer allowances. The source adapter reads native
inputs through bounded regions and preserves declared axes/calibration. The current
candidate takes intensity/class-label/instance-label arrays. It is not a lazy-data
integration, and names must match the layers referred to by each keyframe.

The upstream filesystem dialog directs researchers to Hooke's Export section after
Save tool and all layers has retained the editable document.
`context.bind_sequence(version=1, name=..., count=..., select=...)` delegates frame
selection to the actual upstream interpolation; Hooke exports an accepted tool
revision in a separate confined worker. The default managed mode streams lossless uncompressed ARGB MOV and checks its
decoded pixels before publication; legacy qtrle requests remain readable. Saving keyframes and
exporting a movie are separate explicit actions. The packaged reset-VM repeat captured two keyframes, saved/reopened the editable
tool and exported a movie that played in QuickTime, including an offline repeat.
This developer-operated fixture case does not qualify all animation modes or
independent researcher usability.
Do not advertise this integration as operational or green until managed export,
complete interactions and independent package qualification pass.

Preparation must use the pinned napari reference
`ebfc4a0a1e5386f41582761204f416fffe6cd0b6`, its confined source-build receipt, and a
complete hashed runtime lock. The distribution rehearsal used the separately retained portable hashed bundle
through ordinary import/preparation controls on a reset Mac VM. The bundle and
receipts remain private evidence; this example directory alone is not that package.
Independent and other-platform reproduction remain required. Unit codec fixtures do not prove upstream behavior;
record actual confined execution and workspace evidence separately.

Upstream: https://github.com/napari/napari-animation (BSD-3-Clause). This adapter
calls the installed implementation; it does not replace its scientific/rendering
algorithm. Pinned dependency and redistribution rights remain part of preparation.
