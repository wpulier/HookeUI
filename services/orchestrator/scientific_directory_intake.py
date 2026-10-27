"""Atomic selected-folder intake on the existing immutable file publication path.

A stack (napari's Open Files as Stack) is the same retained collection of flat file names in the order the
researcher gave, plus `state.sourceStack`: the new axis they named and the count. The files must share one
file type, so one reader can open every plane; nothing is decoded here.
"""
import json
import re
from env_worker.source_directory import index, MAX_INDEX
from .scientific_file_intake import FileIntake
from .scientific_projects import InvalidProject


AXIS=re.compile(r'[a-z][a-z0-9_]{0,15}')
STACK_FORMAT=1


def _suffix(name):
    """The file type after the last dot; a leading dot (.hidden) or a trailing dot is no type. Studio agrees."""
    return name.rsplit('.',1)[-1].lower() if '.' in name.lstrip('.') else ''  # 'b.' gives '', no type


def stack_record(axis,entries):
    """The stack a researcher asked for: flat names in their order, one file type, a named new axis."""
    rows=entries['entries']
    if type(axis) is not str or not AXIS.fullmatch(axis):
        raise InvalidProject('Name the stack’s new axis with lowercase letters, such as z, t or c')
    if len(rows)<2:raise InvalidProject('A stack needs at least two files; import a single file as a file')
    if any('/' in row['path'] for row in rows):raise InvalidProject('A stack holds files, not folders; choose the files themselves')
    kinds={}
    for row in rows:kinds.setdefault(_suffix(row['path']),row['path'])
    if '' in kinds:raise InvalidProject(f'“{kinds[""]}” has no file type; a stack’s files must share one file type')
    if len(kinds)>1:
        first,second=list(kinds.values())[:2]
        raise InvalidProject(f'A stack’s files must share one file type; “{first}” and “{second}” differ')
    return {'format':STACK_FORMAT,'axis':axis,'count':len(rows)}


def stack_of(state):
    """The validated stack of a source document, or None."""
    value=state.get('sourceStack')
    if value is None:return None
    directory=state.get('sourceDirectory')
    if type(value) is not dict or set(value)!={'format','axis','count'} or type(directory) is not dict \
            or type(directory.get('entries')) is not list:
        raise InvalidProject('A stack must keep its axis and its exact files')
    if stack_record(value['axis'],directory)!=value:raise InvalidProject('A stack must keep its axis and its exact files')
    return value


class DirectoryIntake(FileIntake):
    def __init__(self,*args,entries,**kwargs):
        try:self.entries=index(entries,kwargs['size'])
        except ValueError as error:raise InvalidProject(str(error)) from error
        super().__init__(*args,**kwargs)
    def document(self,ref):
        document=super().document(ref)
        document['kind']='source-directory'
        document['state']['sourceDirectory']=self.entries
        return document


class StackIntake(DirectoryIntake):
    def __init__(self,*args,axis,entries,**kwargs):
        try:checked=index(entries,kwargs['size'])
        except ValueError as error:raise InvalidProject(str(error)) from error
        self.stack=stack_record(axis,checked)  # refused before any staging exists
        super().__init__(*args,entries=entries,**kwargs)
    def document(self,ref):
        document=super().document(ref)
        document['state']['sourceStack']=dict(self.stack)
        return document


class DirectoryTransfer:
    """Four-byte big-endian index length, JSON index, then concatenated entry bytes.

No JSON body or complete source is buffered. FileIntake owns staging, exact
length verification, publication/retry and cleanup after index admission.
"""
    intake=DirectoryIntake
    def __init__(self,*args,**kwargs):
        self.args,self.kwargs=args,kwargs;self.header=bytearray();self.length=None;self.owner=None;self.closed=False
    def write(self,body):
        if self.closed:raise InvalidProject('This folder import is closed')
        view=memoryview(body)
        while view:
            if self.owner is not None:
                self.owner.write(bytes(view));return
            need=(4 if self.length is None else self.length)-len(self.header)
            count=min(need,len(view));self.header.extend(view[:count]);view=view[count:]
            if len(self.header)<(4 if self.length is None else self.length):return
            if self.length is None:
                self.length=int.from_bytes(self.header,'big');self.header.clear()
                if not 1<=self.length<=MAX_INDEX:raise InvalidProject('The folder index exceeds its allowance')
            else:
                try:entries=json.loads(self.header)
                except (ValueError,RecursionError) as error:raise InvalidProject('The folder index is invalid: '+str(error)) from error
                try:self.owner=self.intake(*self.args,entries=entries,**self.kwargs)
                except InvalidProject:raise
                except (ValueError,RecursionError) as error:raise InvalidProject('The folder index is invalid: '+str(error)) from error
                self.header.clear()
    def publish(self,stopping=lambda:False):
        if self.owner is None:raise InvalidProject('The folder transfer is incomplete')
        return self.owner.publish(stopping)
    def close(self):
        self.closed=True;self.header.clear()
        return self.owner.close() if self.owner is not None else True


class StackTransfer(DirectoryTransfer):
    """The folder framing, with the stack's axis captured before any byte arrives."""
    intake=StackIntake
