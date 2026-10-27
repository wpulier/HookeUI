"""Actual ome-zarr-py reader over public selected-folder grants.

This integration owns NGFF interpretation. Hooke owns immutable storage and
runtime admission. The local demonstration supports bounded NGFF 0.4 images;
plate/label/remote/large-data modes remain separate, unqualified work.
"""
import io
import math
from fsspec.spec import AbstractFileSystem
from ome_zarr.io import ZarrLocation
from ome_zarr.format import FormatV04
from ome_zarr.reader import Reader
from zarr.storage import FSStore


class SelectedFiles(AbstractFileSystem):
    protocol='hooke-selected'
    cachable=False
    def __init__(self,source):super().__init__();self.source=source
    def _key(self,path):
        if not path.startswith('selected/'):raise FileNotFoundError(path)
        key=path[len('selected/'):]
        if key not in self.source:raise FileNotFoundError(path)
        return key
    def cat_file(self,path,start=None,end=None,**kwargs):
        key=self._key(path)
        with self.source.open(key) as raw:
            low=0 if start is None else start;high=raw.size if end is None else end
            if not 0<=low<=high<=raw.size or high-low>8<<20:raise ValueError('Selected entry read exceeds the integration allowance')
            raw.seek(low)
            with io.BufferedReader(raw) as stream:return stream.read(high-low)
    def _open(self,path,mode='rb',**kwargs):
        if mode!='rb':raise PermissionError('Selected scientific sources are immutable')
        return self.source.open(self._key(path))
    def exists(self,path,**kwargs):return path=='selected' or path.removeprefix('selected/') in self.source or self.isdir(path)
    def isdir(self,path):return any(('selected/'+key).startswith(path.rstrip('/')+'/') for key in self.source)
    def isfile(self,path):return path.startswith('selected/') and path[len('selected/'):] in self.source
    def ls(self,path,detail=True,**kwargs):
        prefix=path.rstrip('/')+'/';rows={}
        for key in self.source:
            full='selected/'+key
            if not full.startswith(prefix):continue
            rest=full[len(prefix):];name=prefix+rest.split('/')[0]
            rows[name]={'name':name,'type':'directory' if '/' in rest else 'file','size':0}
        return list(rows.values()) if detail else list(rows)


class SelectedLocation(ZarrLocation):
    def create(self,path):
        # Child traversal keeps the same selected source grant; no global fsspec
        # registration, filesystem reopening or inferred network authority.
        return SelectedLocation(FSStore(self.subpath(path),fs=self.store.fs,mode='r',check=False,create=False),fmt=FormatV04())


def read_images(source):
    location=SelectedLocation(FSStore('selected',fs=SelectedFiles(source),mode='r',check=False,create=False),fmt=FormatV04())
    if location.root_attrs.get('multiscales',[{}])[0].get('version')!='0.4':
        raise ValueError('This candidate supports NGFF 0.4 image roots only')
    nodes=list(Reader(location)())
    images=[node for node in nodes if node.data]
    if len(images)!=1:raise ValueError('Select one NGFF image; plates and associated labels need their declared workflow')
    node=images[0]
    if sum(math.prod(level.shape)*level.dtype.itemsize for level in node.data)>32<<20:
        raise ValueError('This demonstration supports at most 32 MiB decoded; large-data qualification is still open')
    node.metadata['selectedRootTransforms']=location.root_attrs['multiscales'][0].get('coordinateTransformations',[])
    return node


def open_host(context):
    from magicgui.widgets import Container
    node=read_images(context.files['source'])
    # Explicit bounded loading for this first connected workflow. No claim that
    # this path qualifies lazy processing or larger-than-memory publication.
    metadata=node.metadata
    axes=metadata.get('axes',[])
    transforms=metadata.get('coordinateTransformations',[])
    if len(transforms)!=len(node.data):raise ValueError('Each image level needs its declared NGFF transform')
    for n,(level,chain) in enumerate(zip(node.data,transforms)):
        scale=[1.0]*level.ndim;translate=[0.0]*level.ndim
        for transform in [*chain,*metadata['selectedRootTransforms']]:
            if transform.get('type')=='scale':
                factor=transform['scale']
                if len(factor)!=level.ndim or any(not math.isfinite(x) or x<=0 for x in factor):raise ValueError('Invalid NGFF scale')
                translate=[a*b for a,b in zip(translate,factor)];scale=[a*b for a,b in zip(scale,factor)]
            elif transform.get('type')=='translation':
                delta=transform['translation']
                if len(delta)!=level.ndim or any(not math.isfinite(x) for x in delta):raise ValueError('Invalid NGFF translation')
                translate=[a+b for a,b in zip(translate,delta)]
            else:raise ValueError('This candidate needs an explicit supported NGFF coordinate transform')
        layer=context.viewer.add_image(level.compute(scheduler='synchronous'),
            name=f"{metadata.get('name') or 'OME-Zarr image'} · resolution {n}",
            scale=scale,translate=translate,axis_labels=tuple(axis['name'] for axis in axes),
            units=tuple(axis.get('unit') for axis in axes),visible=n==0)
        context.bind_view(layer=layer,key=f'selected-ome-zarr-{n}')
    return Container(widgets=[])
