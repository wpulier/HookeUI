"""Regional private Labels over an exact retained input, with bounded copy-on-write.

Reads use the ordinary native cache/grants. Changed chunks are immutable byte
strings: a gesture snapshot retains identities, not another whole volume. Native
napari still owns painting and undo atoms; only its array indexing is adapted.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import product
import hashlib
import math
from types import SimpleNamespace

import numpy as np

from .lazy_arrays import LazyNative, _normalize, MaterializationRefused

DIRTY_BYTES = 64 << 20
WRITE_BYTES = 16 << 20
INDEX_BYTES = 16 << 20


class DirtyBudget:
    """Current dirty maps, not total resident memory, shared across regional Labels.

    One serialized gesture may also retain its starting map (at most64MiB),
    and an assignment may prepare at most16MiB of replacements plus one1MiB
    chunk buffer. A Save freezes at most64MiB across its layers. Native undo,
    the rollback copy of that undo, cache, displayed slices and process overhead
    have separate existing limits; the process guard still enforces the whole.
    """
    def __init__(self, limit=DIRTY_BYTES):
        self.limit,self.used=limit,0

    def replace(self, old, new):
        if self.used-old+new>self.limit:
            raise MaterializationRefused('Regional Labels changes exceed their memory allowance; save and reopen before editing more')
        self.used+=new-old


@dataclass(frozen=True)
class Snapshot:
    owner:object
    chunks:dict


class LazyLabels(LazyNative):
    def __init__(self, source, cache, budget, *, ident):
        if source.chunks is None or np.dtype(source.dtype).kind not in 'iu':
            raise ValueError('Regional Labels require an exact integer source with retained chunk boundaries')
        super().__init__(source,cache,chunks=source.chunks,ident=ident)
        self._budget=budget
        self._dirty={}
        self._original_hashes={}
        self._reference=source.reference

    @property
    def flags(self):return SimpleNamespace(writeable=True)

    @property
    def dirty_bytes(self):return sum(len(body) for body in self._dirty.values())

    def max(self,*args,**kwargs):
        raise MaterializationRefused('Choose an explicit label ID for regional Labels; finding the whole-volume maximum is not a bounded edit')

    def _shape(self,index):return tuple(min(c,n-i*c) for i,c,n in zip(index,self.chunks,self.shape))

    def _chunk(self,index):
        if index in self._dirty:
            return np.frombuffer(self._dirty[index],dtype=self.dtype).reshape(self._shape(index))
        original=super()._chunk(index)
        self._original_hashes.setdefault(index,hashlib.sha256(original.tobytes(order='C')).digest())
        return original

    def snapshot(self):return Snapshot(self,dict(self._dirty))

    def restore(self,snapshot):
        if type(snapshot) is not Snapshot or snapshot.owner is not self:
            raise ValueError('A regional Labels rollback belongs to its original working array')
        self._replace(dict(snapshot.chunks))

    def _replace(self,chunks):
        self._budget.replace(self.dirty_bytes,sum(len(body) for body in chunks.values()))
        self._dirty=chunks

    def fingerprint(self):
        digest=hashlib.sha256()
        for index,body in sorted(self._dirty.items()):
            digest.update(repr(index).encode());digest.update(hashlib.sha256(body).digest())
        return digest.hexdigest()

    def _coordinates(self,key):
        if not isinstance(key,tuple) or not any(isinstance(v,np.ndarray) for v in key):return None
        if len(key)!=self.ndim or any(type(v) is not np.ndarray or v.dtype.kind not in 'iu' or v.ndim!=1 for v in key):
            raise MaterializationRefused('Regional Labels coordinate edits need one bounded integer vector per axis')
        if len({v.size for v in key})!=1 or key[0].size>65536 or sum(v.nbytes for v in key)>INDEX_BYTES:
            raise MaterializationRefused('This coordinate edit exceeds its bounded index allowance')
        if any(np.any(v<0) or np.any(v>=n) for v,n in zip(key,self.shape)):
            raise IndexError('Regional Labels coordinates lie outside the accepted array')
        return key

    def __getitem__(self,key):
        coords=self._coordinates(key)
        if coords is None:return super().__getitem__(key)
        # Group bounded coordinates by source chunk, avoiding a bounding volume
        # spanning widely separated points. No dense whole-volume mask is made.
        result=np.empty(coords[0].size,dtype=self.dtype)
        for index,positions in self._groups(coords).items():
            local=tuple(v[positions]-i*c for v,i,c in zip(coords,index,self.chunks))
            result[positions]=self._chunk(index)[local]
        return result

    def _groups(self,coords):
        grouped={}
        for position,index in enumerate(zip(*(v//c for v,c in zip(coords,self.chunks)))):
            grouped.setdefault(tuple(int(i) for i in index),[]).append(position)
        if sum(math.prod(self._shape(i))*self.dtype.itemsize for i in grouped)>WRITE_BYTES:
            raise MaterializationRefused('This regional Labels gesture touches too many chunks; use a smaller gesture')
        return grouped

    def __setitem__(self,key,value):
        coords=self._coordinates(key)
        if coords is not None:
            grouped=self._groups(coords)
            incoming=np.broadcast_to(np.asarray(value,dtype=self.dtype),(coords[0].size,))
            def edit(index,block):
                positions=grouped[index]
                local=tuple(v[positions]-i*c for v,i,c in zip(coords,index,self.chunks))
                block[local]=incoming[positions]
            indices=grouped
        else:
            parts=_normalize(key,self.shape)
            if any(step!=1 for _,_,step,_ in parts):
                raise MaterializationRefused('Regional Labels writes require contiguous slices')
            dimensions=tuple(stop-start for start,stop,_,integer in parts if not integer)
            if math.prod(dimensions)*self.dtype.itemsize>WRITE_BYTES:
                raise MaterializationRefused('This regional Labels write exceeds its gesture allowance')
            incoming=np.broadcast_to(np.asarray(value,dtype=self.dtype),dimensions)
            for axis,(*_,integer) in enumerate(parts):
                if integer:incoming=np.expand_dims(incoming,axis)
            indices=list(product(*(range(start//c,-(-stop//c)) for (start,stop,_,_),c in zip(parts,self.chunks))))
            def edit(index,block):
                dst=[];src=[]
                for (start,stop,_,_),i,c in zip(parts,index,self.chunks):
                    lo,hi=max(start,i*c),min(stop,(i+1)*c)
                    dst.append(slice(lo-i*c,hi-i*c));src.append(slice(lo-start,hi-start))
                block[tuple(dst)]=incoming[tuple(src)]
        touched=sum(math.prod(self._shape(i))*self.dtype.itemsize for i in indices)
        if touched>WRITE_BYTES:
            raise MaterializationRefused('This regional Labels gesture touches too many chunks; use a smaller gesture')
        # Reserve worst-case growth before reading/copying any affected pixels.
        additional=sum(math.prod(self._shape(i))*self.dtype.itemsize for i in indices if i not in self._dirty)
        if self._budget.used+additional>self._budget.limit:
            raise MaterializationRefused('Regional Labels changes exceed their memory allowance; save and reopen before editing more')
        changed=dict(self._dirty)
        for index in indices:
            block=self._chunk(index).copy();edit(index,block)
            body=block.tobytes(order='C')
            if hashlib.sha256(body).digest()==self._original_hashes[index]:changed.pop(index,None)
            else:changed[index]=body
        self._replace(changed)

    @contextmanager
    def staged_chunks(self,grant,verify,*,budget):
        from .native_edits import BaseChunk,chunk
        from .scientific_layer_exports import single_threaded
        if (grant.reference!=self._reference or grant.shape!=self.shape or grant.dtype!=self.dtype.str
                or grant.chunks!=self.chunks):
            raise ValueError('Regional Labels Save needs the exact accepted base and chunk layout')
        single_threaded();verify()
        frozen=self.snapshot()
        budget.take(self.dirty_bytes)
        def chunks():
            for index in product(*(range(n) for n in grant.grid)):
                body=frozen.chunks.get(index)
                shape,_=chunk(index,grant.shape,grant.dtype,grant.chunks,grant.grid)
                yield index,BaseChunk() if body is None else np.frombuffer(body,dtype=grant.dtype).reshape(shape)
        yield chunks()
