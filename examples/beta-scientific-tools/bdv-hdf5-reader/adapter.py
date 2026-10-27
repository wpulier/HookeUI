"""BigDataViewer HDF5 folder reader (Huygens and Fiji BigDataViewer exports) over a selected-folder grant.

This integration owns the BDV interpretation (bdv.py, beside it). Hooke owns immutable storage and runtime admission.
Pyramids are added as lazy multiscale images; level 0 is the one any measurement takes.
A single-resolution dataset is decoded before layer publication, within a 64 MiB aggregate bound, so its native images
can use Hooke's existing Save path. This does not implement pyramid persistence. Not claimed: real Huygens exports, registration transforms beyond the voxel
size (kept as metadata), or more than 64 MiB through the source-read allowance.
"""
import importlib.util
import math
from pathlib import Path

_spec = importlib.util.spec_from_file_location("hooke_bdv", Path(__file__).with_name("bdv.py"))
bdv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bdv)


def open_host(context):
    from magicgui.widgets import Container

    source = context.files["source"]
    described = bdv.describe(source)
    close, levels = bdv.open_levels(source, described)
    closed = False

    def release():
        nonlocal closed
        if not closed:
            closed = True
            close()

    try:
        # Only a genuinely single-resolution dataset takes the bounded native
        # Save path. Pyramids keep their lazy layers; this is not a pyramid writer.
        single = all(len(pyramid) == 1 for pyramid in levels.values())
        prepared = {}
        if single:
            if any(len(pyramid[0]["data"].shape) != 3
                   or any(size <= 0 for size in pyramid[0]["data"].shape)
                   or pyramid[0]["data"].dtype.kind not in "biuf"
                   or pyramid[0]["data"].dtype.itemsize not in (1, 2, 4, 8)
                   for pyramid in levels.values()):
                context.refuse_input(
                    "Single-resolution BDV saving requires nonempty 3D fixed-width numeric pixels.",
                    "Choose a scalar numeric BDV export; variable-length or structured pixels cannot be saved here.")
            total = sum(math.prod(pyramid[0]["data"].shape) * pyramid[0]["data"].dtype.itemsize
                        for pyramid in levels.values())
            if total > 64 << 20:
                context.refuse_input(
                    "Single-resolution BDV images exceed this reader's 64 MiB decoded allowance.",
                    "Choose a smaller single-resolution export; keep the original. Large-data saving is not available yet.")
            # Validate every native array before publishing any layer. Reading
            # through the selected-folder grant preserves accepted source identity.
            for key, pyramid in levels.items():
                source = pyramid[0]["data"]
                data = source[...]
                if data.shape != source.shape or data.dtype.str != source.dtype.str:
                    raise ValueError("BDV reading changed the declared shape, dtype or byte order")
                prepared[key] = data
            release()
        names = {s["id"]: s["name"] for s in described["setups"]}
        for (t, setup), pyramid in levels.items():
            data = prepared[(t, setup)] if single else [one["data"] for one in pyramid]
            layer = context.viewer.add_image(data, multiscale=not single, rgb=False,
                                             name=f"{names[setup]} · t{t}", scale=pyramid[0]["scale"],
                                             axis_labels=("z", "y", "x"), units=(pyramid[0]["unit"],) * 3,
                                             metadata={"bdv": {"xml": described["xml"], "hdf5": described["hdf5"],
                                                               "setup": setup, "timepoint": t,
                                                               "registration": described["registrations"].get((t, setup)),
                                                               "calibration": bdv.calibration(pyramid[0]),
                                                               "measuredLevel": 0}})
            context.bind_view(layer=layer, key=f"bdv-s{setup:02d}-t{t:05d}")
        container = Container(widgets=[])
        container.native.destroyed.connect(lambda *_: release())
        return container
    except BaseException:
        release()
        raise
