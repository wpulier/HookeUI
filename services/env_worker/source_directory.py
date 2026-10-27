"""Versioned logical directory over selected immutable bytes; never a host path.

Adapters can open independent seekable entries or use the bounded read-only
mapping for packages such as Zarr. The shared source grant owns lifetime/budget.
"""
from collections.abc import MutableMapping
import copy
import io
import json
from .source_files import MAX_RANGE

FORMAT='source-directory-v1'
MAX_ENTRIES=4096
MAX_INDEX=1 << 20
MAX_VALUE=8 << 20


def index(value, size):
    if (type(value) is not dict or set(value)!={'format','entries'} or value['format']!=FORMAT
            or type(value['entries']) is not list or not 1<=len(value['entries'])<=MAX_ENTRIES
            or type(size) is not int or size<0):
        raise ValueError('A selected folder needs a supported, bounded entry index')
    if len(json.dumps(value,ensure_ascii=False).encode())>MAX_INDEX:
        raise ValueError('Folder index exceeds its metadata allowance')
    end=0;names=set()
    for row in value['entries']:
        if type(row) is not dict or set(row)!={'path','offset','size'}:
            raise ValueError('Folder entries need a relative name and exact byte range')
        path=row['path']
        if (type(path) is not str or not 0<len(path)<=512 or '\\' in path or ':' in path
                or any(ord(c)<32 or ord(c)==127 for c in path)
                or any(part in ('','.','..') for part in path.split('/'))
                or path in names):
            raise ValueError('Folder entry paths must be distinct, relative and unambiguous')
        if (type(row['offset']) is not int or row['offset']!=end
                or type(row['size']) is not int or row['size']<0 or end+row['size']>size):
            raise ValueError('Folder entry ranges must exactly partition the retained bytes')
        names.add(path);end+=row['size']
    for path in names:
        # Check prefixes against actual files, without retaining every prefix.
        if any(path[:at] in names for at,char in enumerate(path) if char=='/'):
            raise ValueError('A folder entry cannot also be a directory')
    if end!=size:
        raise ValueError('Folder index is incomplete or exceeds its metadata allowance')
    return copy.deepcopy(value)


class Entry(io.RawIOBase):
    def __init__(self,source,row,*,view=False):
        super().__init__();self._source=source;self._row=row;self._position=0;self._view=view
    @property
    def name(self):return self._row['path']
    @property
    def size(self):return self._row['size']
    def readable(self):return not self.closed and not self._source.closed
    def seekable(self):return self.readable()
    def writable(self):return False
    def tell(self):self._checkClosed();return self._position
    def seek(self,offset,whence=0):
        self._checkClosed()
        if type(offset) is not int or type(whence) is not int or whence not in (0,1,2):raise ValueError('Invalid folder entry seek')
        target=offset+{0:0,1:self._position,2:self.size}[whence]
        if not 0<=target<=self.size:raise ValueError('Seek outside the selected folder entry')
        self._position=target;return target
    def read(self,size=-1):
        self._checkClosed()
        if type(size) is not int or size < -1:raise ValueError('Invalid folder entry read')
        length=self.size-self._position if size==-1 else min(size,self.size-self._position)
        if length>MAX_RANGE:raise ValueError('Read folder entries in bounded pieces or use BufferedReader')
        # One lock protects the shared seek cursor and cumulative grant budget.
        with self._source._budget.lock:
            self._source.seek(self._row['offset']+self._position)
            value=self._source.read(length,**({'view':True} if self._view else {}))
            self._position+=len(value);return value
    def readinto(self,buffer):
        view=memoryview(buffer).cast('B')
        if view.readonly:raise TypeError('Read destination must be writable')
        body=self.read(min(len(view),MAX_RANGE));view[:len(body)]=body;return len(body)


class SourceDirectory(MutableMapping):
    """Mapping[str, bytes] with explicit independent streams for larger entries.

Mapping reads are capped at 8 MiB per value and the original shared grant budget;
there is no eager directory materialization or implicit cache.
"""
    def __init__(self,source,description):
        self._source=source
        self._entries={row['path']:row for row in index(description,source.size)['entries']}
    @property
    def name(self):return self._source.name
    @property
    def regional(self):return self._source.regional
    @property
    def reference(self):return self._source.reference
    @property
    def closed(self):return self._source.closed
    def __iter__(self):return iter(self._entries)
    def __len__(self):return len(self._entries)
    def __contains__(self,key):return key in self._entries
    def open(self,key,*,view=False):
        self._source._checkClosed()
        if type(view) is not bool or view and not self._source._spec.get('regional',False):
            raise ValueError('This selected folder has no declared regional read grant')
        return Entry(self._source,self._entries[key],view=view)
    def __getitem__(self,key):
        row=self._entries[key]
        if row['size']>MAX_VALUE:raise ValueError('This entry needs bounded streaming access; use source.open(name)')
        with self.open(key) as entry, io.BufferedReader(entry) as stream:return stream.read()
    def __setitem__(self,key,value):raise PermissionError('Selected folder entries are immutable')
    def __delitem__(self,key):raise PermissionError('Selected folder entries are immutable')
    def close(self):self._source.close()
