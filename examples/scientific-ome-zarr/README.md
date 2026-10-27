# Selected-folder OME-Zarr integration candidate

`adapter.py` uses actual **ome-zarr 0.10.3**, **Zarr 2.18.7**, and their fsspec/Dask
interfaces. The shared Hooke directory grant contains exact retained entries and
supports seekable streams; this integration owns NGFF interpretation. It needs no
core OME-Zarr branch, host path, extraction directory or network permission.

Declare a private `scientific-host-v1` member with:

```toml
reader = { format = 2, kind = "directory", input = "source", extensions = [".zarr"], maximum_bytes = 67108864 }
```

Pin dependencies with hashes using the existing preparation tools, alongside the
pinned napari runtime. `adapter:open_host` is the factory. This source example is
not an installed/prepared portable package or independent qualification.

Current bounds: one NGFF 0.4 image root, 32 MiB total decoded values, original
64-MiB file-grant budget. Each resolution becomes a separately named image layer
with its own declared scale/translation/axis units; only the highest resolution is
initially visible. It explicitly loads these small demonstration arrays. It does
**not** claim automatic multiscale navigation, plates, associated labels, remote
sources, large-data processing or all OME-Zarr required modes.

Local evidence: actual upstream libraries read a generated asymmetric uint16
3×31×47 image and a second resolution through the production retained store and
public range grants, preserving values, axes and transforms. The separate NumPy
sequence test uses the same interface. Packaged native UI, accepted output and
connected-workflow qualification remain open.

References: [NGFF 0.4](https://ngff.openmicroscopy.org/0.4/) and
[Zarr 2.18.7 storage](https://github.com/zarr-developers/zarr-python/blob/v2.18.7/zarr/storage.py).
