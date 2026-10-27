"""Actual ome-zarr-py reader over public selected-folder grants.

This integration owns NGFF interpretation. Hooke owns immutable storage and
runtime admission. This candidate supports uncompressed NGFF 0.4 images with regional decoding
and aligned grayscale pyramids, plus explicit bounded standalone, single-resolution Labels. Linked/plate/
multiscale-label/remote modes and full large-data qualification remain open.
"""
import io
import json
import numpy as np
import math
from functools import lru_cache
from fsspec.spec import AbstractFileSystem
from ome_zarr.io import ZarrLocation
from ome_zarr.format import FormatV04
from ome_zarr.reader import Reader
from zarr.storage import FSStore


class UnsupportedInput(ValueError):
    """An expected preflight refusal with authored recovery, never a decoder error."""
    def __init__(self, reason, next_step):
        super().__init__(reason)
        self.next_step=next_step


class SelectedFiles(AbstractFileSystem):
    protocol='hooke-selected'
    cachable=False
    def __init__(self,source):
        super().__init__();self.source=source
        # Adjacent decoded regions can share one immutable stored chunk. Bound
        # this cache to four entries of at most 8 MiB; keep it local to the grant.
        self._read=lru_cache(maxsize=4)(self._read) if getattr(source,'regional',False) else self._read
    def _key(self,path):
        if not path.startswith('selected/'):raise FileNotFoundError(path)
        key=path[len('selected/'):]
        if key not in self.source:raise FileNotFoundError(path)
        return key
    def cat_file(self,path,start=None,end=None,**kwargs):
        return self._read(self._key(path),start,end)
    def _read(self,key,start,end):
        with self.source.open(key, **({'view':True} if getattr(self.source,'regional',False) else {})) as raw:
            low=0 if start is None else start;high=raw.size if end is None else end
            if not 0<=low<=high<=raw.size or high-low>8<<20:raise ValueError('Selected entry read exceeds the integration allowance')
            raw.seek(low)
            with io.BufferedReader(raw) as stream:return stream.read(high-low)
    def _open(self,path,mode='rb',**kwargs):
        if mode!='rb':raise PermissionError('Selected scientific sources are immutable')
        return self.source.open(self._key(path), **({'view':True} if getattr(self.source,'regional',False) else {}))
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


def metadata(source,key):
    with source.open(key) as raw:
        if raw.size>1<<20:raise ValueError('NGFF metadata exceeds the bounded allowance')
        with io.BufferedReader(raw) as stream:body=stream.read()
    try:value=json.loads(body)
    except (ValueError,RecursionError) as error:raise ValueError('Invalid NGFF metadata') from error
    if type(value) is not dict:raise ValueError('NGFF metadata needs an object')
    return value


def label_root(source, attrs, image):
    """NGFF 0.4 image-label is an explicit object-ID declaration, never inferred from dtype.

    This first standalone scope omits links, custom colors/properties and label
    hierarchies rather than silently dropping them or traversing another root.
    """
    if 'image-label' not in attrs:return False
    label=attrs['image-label'];axes=image.get('axes');datasets=image.get('datasets')
    reason=None
    if (type(label) is not dict or label.get('version')!='0.4'
            or set(label)-{'version','colors','properties'}
            or label.get('colors',[])!=[] or label.get('properties',[])!=[]
            or any(key in attrs for key in ('labels','plate','well','omero'))
            or any(key.startswith('labels/') for key in source)):
        reason='Labels require standalone image-label version 0.4 without links, colors or properties.'
    elif type(datasets) is not list or len(datasets)!=1:
        reason='Multiscale Labels are not supported by this reader yet.'
    elif (type(axes) is not list or len(axes) not in (2,3)
            or any(type(axis) is not dict or axis.get('type')!='space' for axis in axes)
            or [axis.get('name') for axis in axes] not in (['y','x'],['z','y','x'])):
        reason='Standalone Labels require ordered spatial y/x or z/y/x axes.'
    else:
        # NGFF 0.4 requires one scale followed by an optional translation per
        # dataset; optional root transforms compose afterward in open_host.
        for chain in [datasets[0].get('coordinateTransformations') if type(datasets[0]) is dict else None,
                      *([image['coordinateTransformations']] if 'coordinateTransformations' in image else [])]:
            if (type(chain) is not list or any(type(item) is not dict for item in chain)
                    or [item.get('type') for item in chain] not in (['scale'],['scale','translation'])):
                reason='Labels require explicit NGFF scale and optional following translation.'
    if reason:raise UnsupportedInput(reason,
        'Select a standalone uncompressed label root with supported metadata; keep the original.')
    return True


def declared_image(source, *, maximum_image_bytes=1<<30):
    """Check bounded native layouts before constructing upstream codecs or lazy tasks."""
    if metadata(source,'.zgroup').get('zarr_format')!=2:raise ValueError('This reader requires Zarr format 2')
    attrs=metadata(source,'.zattrs');images=attrs.get('multiscales')
    if type(images) is not list or len(images)!=1 or type(images[0]) is not dict or images[0].get('version')!='0.4':
        raise ValueError('Select one NGFF 0.4 image root; other roots need their declared workflow')
    image=images[0];datasets=image.get('datasets');axes=image.get('axes')
    if type(datasets) is not list or not 1<=len(datasets)<=16 or type(axes) is not list:
        raise ValueError('This reader needs explicit image levels and axes')
    labels=label_root(source,attrs,image)
    if len(datasets)>8:
        raise UnsupportedInput('This reader can retain at most eight image levels together.',
            'Export a copy with at most eight levels; keep the original pyramid.')
    layouts=[];total=0;paths=set()
    for dataset in datasets:
        path=dataset.get('path') if type(dataset) is dict else None
        if (type(path) is not str or not path or any(c in path for c in ('\\',':'))
                or any(part in ('','.','..') for part in path.split('/')) or path in paths):
            raise ValueError('NGFF levels need distinct relative selected-folder paths')
        paths.add(path);array=metadata(source,path+'/.zarray')
        # Shape metadata does not bound upstream codecs' actual allocations.
        # Refuse before constructing Reader/Zarr codecs until a bounded decoder exists.
        if array.get('compressor') is not None:
            raise UnsupportedInput('This reader supports uncompressed NGFF chunks only.',
                'Export an uncompressed copy and keep the original. Bounded compressed input support is not ready.')
        shape,chunks=array.get('shape'),array.get('chunks')
        try:dtype=np.dtype(array.get('dtype'))
        except (TypeError,ValueError) as error:raise ValueError('Unsupported native dtype') from error
        if (array.get('zarr_format')!=2 or dtype.kind not in 'biuf' or dtype.itemsize>8
                or array.get('dtype') is None or array.get('order') not in ('C','F')
                or array.get('filters') not in (None,[])):
            raise ValueError('This reader requires scalar numeric Zarr arrays without filters')
        if (type(shape) is not list or not 2<=len(shape)<=5 or len(shape)!=len(axes)
                or type(chunks) is not list or len(chunks)!=len(shape)
                or any(type(n) is not int or n<=0 for n in [*shape,*chunks])):
            raise ValueError('NGFF shape, chunks and axes must agree')
        if labels and dtype.kind not in 'iu':
            raise UnsupportedInput('NGFF Labels require integer object IDs.',
                'Select an integer label root; do not reinterpret intensity values as labels.')
        total+=math.prod(shape)*dtype.itemsize
        if total>(32<<20 if labels else maximum_image_bytes):
            raise UnsupportedInput('The decoded input exceeds this reader’s current retention allowance.',
                'Choose a smaller image or crop; keep the original.' if labels or maximum_image_bytes>1<<30 else
                'Update to a host with streamed image publication, or choose a smaller image; keep the original.')
        if math.prod(chunks)*dtype.itemsize>8<<20:
            raise UnsupportedInput('A stored chunk exceeds the bounded decoder allowance.',
                'Rechunk an uncompressed copy to at most 8 MiB per chunk and keep the original.')
        layouts.append((tuple(shape),dtype.str))
    return image,layouts,labels


def read_images(source, *, maximum_image_bytes=1<<30):
    declared,layouts,labels=declared_image(source,maximum_image_bytes=maximum_image_bytes)
    location=SelectedLocation(FSStore('selected',fs=SelectedFiles(source),mode='r',check=False,create=False),fmt=FormatV04())
    if location.root_attrs.get('multiscales',[{}])[0].get('version')!='0.4':
        raise ValueError('This candidate supports NGFF 0.4 image roots only')
    nodes=list(Reader(location)())
    images=[node for node in nodes if node.data]
    if len(images)!=1:raise ValueError('Select one NGFF image; plates and associated labels need their declared workflow')
    node=images[0]
    if len(node.data)!=len(layouts) or any(level.shape!=shape or level.dtype.str!=dtype
            for level,(shape,dtype) in zip(node.data,layouts)):
        raise ValueError('The upstream reader changed declared native shape, dtype or byte order')
    node.metadata['selectedLayerType']='labels' if labels else 'image'
    node.metadata['axes']=declared['axes']
    node.metadata['coordinateTransformations']=[dataset.get('coordinateTransformations',[]) for dataset in declared['datasets']]
    node.metadata['selectedRootTransforms']=declared.get('coordinateTransformations',[])
    return node


def open_host(context):
    from magicgui.widgets import Container
    # Older regional hosts can open large data but cannot retain it. Negotiate
    # the shared Save contract before constructing upstream arrays or layers.
    capability=getattr(context,'streamed_image_publication',None)
    maximum=1<<30
    if (type(capability) is dict and type(capability.get('version')) is int
            and capability['version']==1 and type(capability.get('maximum_bytes')) is int
            and capability['maximum_bytes']>0):
        maximum=min(capability['maximum_bytes'],64<<30)
    try:node=read_images(context.files['source'],maximum_image_bytes=maximum)
    except UnsupportedInput as refusal:
        context.refuse_input(str(refusal),refusal.next_step)
    metadata=node.metadata
    labels=metadata.get('selectedLayerType')=='labels'
    lazy=not labels and sum(math.prod(level.shape)*level.dtype.itemsize for level in node.data)>32<<20
    axes=metadata.get('axes',[])
    if (not 2<=len(axes)<=5 or any(type(axis) is not dict or type(axis.get('name')) is not str
            or not axis['name'] or axis.get('type') not in ('space','time','channel')
            or (axis.get('unit') is not None and type(axis['unit']) is not str) for axis in axes)
            or len({axis['name'] for axis in axes})!=len(axes)):
        raise ValueError('The reader needs distinct explicitly named NGFF axes')
    transforms=metadata.get('coordinateTransformations',[])
    if len(transforms)!=len(node.data):raise ValueError('Each image level needs its declared NGFF transform')
    geometry=[]
    for n,(level,chain) in enumerate(zip(node.data,transforms)):
        if level.ndim!=len(axes):raise ValueError('Native dimensions disagree with declared axes')
        scale=[1.0]*level.ndim;translate=[0.0]*level.ndim
        for transform in [*chain,*metadata['selectedRootTransforms']]:
            if transform.get('type')=='scale':
                factor=transform['scale']
                if len(factor)!=level.ndim or any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in factor):raise ValueError('Invalid NGFF scale')
                translate=[a*b for a,b in zip(translate,factor)];scale=[a*b for a,b in zip(scale,factor)]
            elif transform.get('type')=='translation':
                delta=transform['translation']
                if len(delta)!=level.ndim or any(type(x) not in (int,float) or not math.isfinite(x) for x in delta):raise ValueError('Invalid NGFF translation')
                translate=[a+b for a,b in zip(translate,delta)]
            else:raise ValueError('This candidate needs an explicit supported NGFF coordinate transform')
        if any(not math.isfinite(x) or x<=0 for x in scale) or any(not math.isfinite(x) for x in translate):
            raise ValueError('Composed NGFF calibration is not finite and invertible')
        geometry.append(dict(scale=scale,translate=translate,
            axis_labels=tuple(axis['name'] for axis in axes),
            units=tuple(axis.get('unit') for axis in axes)))
    pyramid=len(node.data)>1
    if pyramid:
        # One napari multiscale layer uses the finest affine and shape ratios.
        # Refuse different physical grids instead of silently replacing the NGFF
        # calibration with inferred downsampling or flattening levels into images.
        finest=node.data[0];base=geometry[0];previous=finest.shape
        aligned=len(node.data)<=8 and not labels
        for index,(level,settings) in enumerate(zip(node.data,geometry)):
            aligned=aligned and level.dtype.str==finest.dtype.str and all(
                size<=prior for size,prior in zip(level.shape,previous))
            aligned=aligned and (index==0 or math.prod(level.shape)<math.prod(previous))
            aligned=aligned and settings['translate']==base['translate'] and all(
                math.isclose(actual,scale*full/size,rel_tol=1e-12,abs_tol=0.)
                for actual,scale,full,size in zip(settings['scale'],base['scale'],finest.shape,level.shape))
            previous=level.shape
        if not aligned:
            context.refuse_input('This pyramid does not use one aligned, finest-first grayscale grid.',
                'Select or export an aligned pyramid with matching dtype, origin and shape-ratio scales, '
                'with at most eight levels; keep the original.')
    prepared=[]
    for level in node.data:
        values=(context.decode_image(input='source',shape=level.shape,dtype=level.dtype.str,
            axes=tuple(axis['name'] for axis in axes),
            decode=lambda slices, level=level: level[slices].compute(scheduler='synchronous'))
            if lazy else level.compute(scheduler='synchronous'))
        if values.shape!=level.shape or values.dtype.str!=level.dtype.str:
            raise ValueError('The upstream reader changed native shape, dtype or byte order')
        if labels and np.any(values < 0):
            context.refuse_input('NGFF Labels require nonnegative object IDs.',
                'Select a nonnegative integer label root; keep the original.')
        prepared.append(values)
    # Validate all levels before one publication. Large immutable levels retain
    # the same declared decoder path used by single-image native Save.
    settings=dict(geometry[0],name=metadata.get('name') or 'OME-Zarr image',visible=True)
    if labels:
        layer=context.viewer.add_labels(prepared[0],**settings)
    else:
        layer=context.viewer.add_image(prepared if pyramid else prepared[0],
            rgb=False,multiscale=pyramid,**settings)
    context.bind_view(layer=layer,key='selected-ome-zarr-0')
    return Container(widgets=[])
