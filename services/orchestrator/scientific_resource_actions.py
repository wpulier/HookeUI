"""Bounded operations on exact accepted arrays, shared by workspace and callers.

The viewer never supplies pixels or object identities. Measurements retain their
input reference in the same document; exports include native values and metadata.
"""
import csv
import hashlib
import io
import itertools
import math
import shutil
import tempfile
import time
import zipfile

import numpy as np

from .scientific_projects import InvalidProject, _json

from . import scientific_tables as tables

FORMAT = 'scientific-object-measurements-v1'  # retained JSON rows of revisions measured before typed tables
MEASUREMENTS = 'scientific-object-measurements-v2'  # points at the typed 'objects' table of the same revision
TABLE = 'objects'
WORKING_BYTES = 32 << 20
MAX_OBJECTS = 10000


def measurement_plan(source):
    """Describe this accepted array's measurement using metadata only; never read its values.

    The same columns and physical conversion drive execution, so discovery cannot promise
    a different operation. Value-dependent checks (negative labels/object count) still run.
    """
    from .calibrated_area import element_size, unit
    # IDs may repeat across time or channels. Do not silently merge those objects.
    available = (source.descriptor.get('semantic') == 'instance-labels'
                 and np.dtype(source.dtype).kind in 'ui'
                 and set(source.axes) in ({'y', 'x'}, {'z', 'y', 'x'}))
    plan = {'format': 'scientific-object-measurement-plan-v1', 'available': available,
            'reason': '', 'method': '', 'columns': [], 'physicalSize': None, 'calibrationReason': ''}
    if not available:
        plan['reason'] = ('Choose 2D or 3D spatial individual-object labels. Time and channel arrays need '
                          'an explicit selection; class labels need an explicit conversion')
        return plan
    plan['method'] = ('Count every array element with the same label as one object, including disconnected parts; '
                      'report its mean array indices as its center. Background 0 is excluded. '
                      'Labels must be nonnegative; at most 10,000 objects. Calibrated object sizes must be positive and finite. '
                      'No intensity measurements or segmentation rerun.')
    count_unit = 'pixel' if len(source.shape) == 2 else 'voxel'
    columns = [{'name': 'object_id', 'dtype': '<u8', 'role': 'key',
                'description': 'Label value of the object in the measured labels'},
               {'name': 'voxel_count', 'dtype': '<i8', 'role': 'measurement', 'unit': count_unit,
                'description': 'Number of array elements with this label: a count, not a calibrated area or volume'}]
    columns += [{'name': f'centroid_{axis}_px', 'dtype': '<f8', 'role': 'measurement', 'unit': 'pixel',
                 'description': f'Mean array index along {axis}; no calibration applied'} for axis in source.axes]
    size, why = element_size(source.descriptor.get('calibration'), list(source.axes))
    if size is not None:
        name = 'area_um2' if len(source.shape) == 2 else 'volume_um3'
        columns.append({'name': name, 'dtype': '<f8', 'role': 'measurement', 'unit': unit(len(source.shape)),
                        'description': "Object size from its element count and the labels' own axis-aligned calibration "
                                       f'({size:.6g} {unit(len(source.shape))} per element)'})
        plan['physicalSize'] = {'column': name, 'perElement': size}
    else:
        columns[1]['description'] += f' ({why})'
        plan['calibrationReason'] = why
    plan['columns'] = columns
    return plan


def selected(store, project, session, revision, key):
    saved=store.read(project,session,revision=revision)
    view=saved.document['state'].get('scientificView',{})
    inputs=view.get('inputs',{})
    if type(key) is not str or key not in inputs:
        raise InvalidProject('Choose a retained array from this saved revision')
    reference=inputs[key]
    if reference not in saved.document['resources']:
        raise InvalidProject('This array is not retained by the selected document')
    # Measurement and export read bounded chunk regions; the object budget and the export's disk
    # admission bound their work, not the array's logical size.
    from .admission import native_source
    source, _, captured = native_source(store, {'kind':'array','session':session,'id':project,
        'revision':revision,'component':reference['component']}, session)
    if captured['resource'] != reference:
        raise InvalidProject('The selected array does not match its accepted component')
    return saved,reference,source


def inventory(store,project,session,revision):
    saved=store.read(project,session,revision=revision)
    view=saved.document['state'].get('scientificView',{})
    names={row.get('input'):row.get('metadata',{}).get('name') for row in view.get('layers',[]) if type(row) is dict}
    rows=[]
    for key in view.get('inputs',{}):
        _,reference,source=selected(store,project,session,revision,key)
        plan = measurement_plan(source)
        rows.append({'key':key,'name':names.get(key) or key,'shape':list(source.shape),
                     'dtype':source.dtype,'axes':list(source.axes),'semantic':source.descriptor.get('semantic'),
                     'canMeasure':plan['available'],'measurementPlan':plan,'reference':reference})
    return {'source':{'id':project,'session':session,'revision':revision,'digest':saved.digest},'arrays':rows}


MAX_REREADS = 4  # times native export may read one stored chunk while writing C order


def _checked(source,region,data):
    expected=tuple(s.stop-s.start for s in region)
    if type(data) is not np.ndarray or data.shape!=expected or data.dtype.str!=source.dtype:
        raise InvalidProject('The accepted array returned inconsistent native values')
    return data


def blocks(source,check):
    """Each stored chunk exactly once, with its index origin, for order-independent work.

    A source without a chunk layout is read in bounded rows instead.
    """
    if source.chunks is None:
        yield from chunks(source,check)
        return
    grid=[(n+c-1)//c for n,c in zip(source.shape,source.chunks)]
    for index in itertools.product(*(range(n) for n in grid)):
        check()
        region=tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(index,source.chunks,source.shape))
        yield tuple(r.start for r in region),_checked(source,region,source.read(region))


def band_plan(source):
    """Rows per C-order export band and how often each stored chunk is then read, or None.

    A band holds whole rows of the trailing axes within the working allowance, and its height divides
    the chunk height, so every chunk is read the same number of times: once when a chunk-row fits.
    """
    if source.chunks is None or not source.shape:
        return None
    row=math.prod(source.shape[1:])*np.dtype(source.dtype).itemsize
    tallest=min(source.chunks[0],WORKING_BYTES//row) if row else source.chunks[0]
    if tallest<1:
        return None
    reads=-(-source.chunks[0]//tallest)
    while source.chunks[0]%reads:reads+=1
    return source.chunks[0]//reads,reads


def bands(source,check,height):
    rest=[(n+c-1)//c for n,c in zip(source.shape[1:],source.chunks[1:])]
    for start in range(0,source.shape[0],height):
        rows=min(height,source.shape[0]-start)
        band=np.empty((rows,*source.shape[1:]),dtype=source.dtype)
        for index in itertools.product(*(range(n) for n in rest)):
            check()
            inner=tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(index,source.chunks[1:],source.shape[1:]))
            region=(slice(start,start+rows),*inner)
            band[(slice(0,rows),*inner)]=_checked(source,region,source.read(region))
        yield band


def chunks(source,check):
    """Contiguous C-order rows, at most 1 MiB, with their index origins (row export and unchunked sources)."""
    width=max(1,(1 << 20)//np.dtype(source.dtype).itemsize)
    if source.chunks is not None:
        from .scientific_arrays import MAX_CHUNKS_PER_READ, MAX_SOURCE_BYTES_PER_READ
        chunk_bytes=math.prod(source.chunks)*np.dtype(source.dtype).itemsize
        blocks=min(MAX_CHUNKS_PER_READ,MAX_SOURCE_BYTES_PER_READ//chunk_bytes)
        width=min(width,source.chunks[-1]*blocks)
        # Keep each multi-chunk read aligned, including fragmented accepted arrays.
        if width>=source.chunks[-1]:width=width//source.chunks[-1]*source.chunks[-1]
    for prefix in itertools.product(*(range(n) for n in source.shape[:-1])):
        for start in range(0,source.shape[-1],width):
            check()
            region=tuple(slice(i,i+1) for i in prefix)+(slice(start,min(start+width,source.shape[-1])),)
            yield (*prefix,start),_checked(source,region,source.read(region))


def measurement_transaction(source):
    return hashlib.sha256(_json({'operation':FORMAT,'source':source})).hexdigest()


def measure(store,project,session,revision,key,*,check=lambda:None,admission=None):
    saved,reference,source=selected(store,project,session,revision,key)
    plan = measurement_plan(source)
    if not plan['available']:
        raise InvalidProject(plan['reason'])
    records={}
    for origin,data in blocks(source,check):  # each stored chunk read once
        flat=data.reshape(-1)
        if np.any(flat<0):raise InvalidProject('Object labels must be nonnegative')
        ids,inverse,counts=np.unique(flat,return_inverse=True,return_counts=True)
        sums=[np.bincount(inverse,minlength=len(ids),weights=np.broadcast_to(
                  np.arange(start,start+size,dtype=np.float64).reshape([-1 if a==axis else 1 for a in range(data.ndim)]),
                  data.shape).reshape(-1))
              for axis,(start,size) in enumerate(zip(origin,data.shape))]
        for index,label in enumerate(ids):
            identity=int(label)
            if identity==0:continue
            if identity not in records:
                if len(records)>=MAX_OBJECTS:raise InvalidProject('This beta measurement supports at most 10,000 objects; select a smaller retained region')
                records[identity]=[0,[0.]*len(source.shape)]
            record=records[identity]; count=int(counts[index]);record[0]+=count
            for axis in range(len(source.shape)):record[1][axis]+=float(sums[axis][index])
    ordered=sorted(records.items())
    source_record={'id':project,'session':session,'revision':revision,'digest':saved.digest,'input':key,'reference':reference}
    columns = plan['columns']
    values={'object_id':np.array([identity for identity,_ in ordered],dtype='<u8'),
            'voxel_count':np.array([count for _,(count,_) in ordered],dtype='<i8')}
    for axis,name in enumerate(source.axes):
        values[f'centroid_{name}_px']=np.array([sums[axis]/count for _,(count,sums) in ordered],dtype='<f8')
    if plan['physicalSize'] is not None:
        physical = plan['physicalSize']
        with np.errstate(over='ignore', under='ignore', invalid='ignore'):
            sizes = values['voxel_count'].astype('<f8') * physical['perElement']
        if not np.isfinite(sizes).all() or np.any(sizes <= 0):
            dimension = 'area' if physical['column'] == 'area_um2' else 'volume'
            raise InvalidProject(f'Object {dimension} cannot be represented as a positive finite number. '
                                 "Check the labels' calibration or select a smaller retained region before measuring.")
        values[physical['column']] = sizes
    declaration=tables.declare(semantic='object-table',key='object_id',object_space=reference,
        calibration=source.descriptor.get('calibration'),columns=columns,
        producer={'operation':MEASUREMENTS,'background':0,'source':source_record,
                  **({'admission':admission} if admission is not None else {})})
    pointer={'format':MEASUREMENTS,'table':TABLE,'source':source_record,'background':0}
    transaction=measurement_transaction(source_record)
    check()
    existing=tables.tables(saved.document)
    return tables.publish(store,saved,{TABLE:(declaration,values)},replace=(TABLE,) if TABLE in existing else (),
                          state={'scientificMeasurements':pointer},transaction=transaction,check=check)


def table(store,project,session,revision):
    state=store.read(project,session,revision=revision).document['state']
    value=state.get('scientificMeasurements')
    if type(value) is not dict or value.get('format') not in (FORMAT,MEASUREMENTS):
        raise InvalidProject('This saved revision has no supported object measurements')
    # A later save (a label correction) keeps the table but replaces its input.
    source=value.get('source') if type(value.get('source')) is dict else {}
    inputs=state.get('scientificView',{}).get('inputs',{}) if type(state.get('scientificView')) is dict else {}
    if type(inputs) is not dict or inputs.get(source.get('input'))!=source.get('reference'):
        measured=source.get('revision')
        raise InvalidProject(f'These measurements belong to earlier labels (saved revision {measured+1 if type(measured) is int else "?"}). '
                             'Measure the current labels again, or download the CSV from that revision')
    if value['format']==MEASUREMENTS:
        typed=tables.verify(store,project,session,revision,value.get('table'))
        if typed.schema['object_space']!=source['reference'] or typed.schema['key']!='object_id':
            raise InvalidProject('The saved measurement table does not belong to its measured labels')
        names=[c['name'] for c in typed.schema['columns']]
        columns=[typed.read(name) for name in names]
        rows=[[str(int(row[0])),int(row[1]),*map(float,row[2:])] for row in zip(*columns)]
        return {'format':MEASUREMENTS,'table':value['table'],'source':source,'columns':names,'rows':rows,
                'units':{c['name']:c['unit'] for c in typed.schema['columns']},
                'background':value.get('background'),'calibration':typed.schema['calibration']}
    columns,rows=value.get('columns'),value.get('rows')
    if (type(columns) is not list or not 4<=len(columns)<=10 or columns[:2]!=['object_id','voxel_count']
            or any(type(column) is not str or not column.startswith('centroid_') or not column.endswith('_px') for column in columns[2:])
            or type(rows) is not list or len(rows)>MAX_OBJECTS):
        raise InvalidProject('The saved measurement table is incomplete')
    for row in rows:
        if (type(row) is not list or len(row)!=len(columns) or type(row[0]) is not str or not row[0].isascii()
                or not row[0].isdigit() or not 0<int(row[0])<2**64 or type(row[1]) is not int or row[1]<=0
                or any(type(v) not in (int,float) or not math.isfinite(v) for v in row[2:])):
            raise InvalidProject('The saved measurement values are invalid')
    return value


def table_csv(value):
    result=io.StringIO(newline='');writer=csv.writer(result)
    writer.writerow(value['columns']);writer.writerows(value['rows'])
    return result.getvalue().encode('utf-8')


def _entry(name,at):
    """Fixed entry metadata: one saved revision always exports the same ZIP bytes."""
    stamp=max(time.gmtime(at)[:6],(1980,1,1,0,0,0))
    info=zipfile.ZipInfo(name,date_time=stamp);info.compress_type=zipfile.ZIP_STORED
    info.external_attr=0o644<<16;info.create_system=3
    return info


class _Hashing:
    def __init__(self,output):self.output,self.digest,self.size=output,hashlib.sha256(),0
    def write(self,data):
        self.digest.update(data);self.size+=len(data);return self.output.write(data)


def export_array(store,project,session,revision,key,*,check=lambda:None):
    saved,reference,source=selected(store,project,session,revision,key)
    from .downloads import SPARE
    size=math.prod(source.shape)*np.dtype(source.dtype).itemsize
    if source.chunks is None:
        ordered=(data for _,data in chunks(source,check))
    else:
        plan=band_plan(source)
        if plan is None or plan[1]>MAX_REREADS:
            raise InvalidProject(f'Writing this array in C order would read each stored chunk more than {MAX_REREADS} times '
                                 f'within the {WORKING_BYTES >> 20} MiB native export holds at once, so this chunk layout '
                                 'cannot be exported yet. Saved data is unchanged')
        ordered=bands(source,check,plan[0])
    if shutil.disk_usage(tempfile.gettempdir()).free < size+(4 << 20)+SPARE:
        raise InvalidProject('Not enough temporary storage for native export. Free space and retry; saved data is unchanged')
    stream=tempfile.TemporaryFile(prefix='hooke-native-export-')
    try:
        values=hashlib.sha256()
        with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_STORED) as archive:
            with archive.open(_entry('array.npy',saved.at),'w',force_zip64=True) as raw:
                output=_Hashing(raw)
                np.lib.format.write_array_header_2_0(output,{'descr':np.dtype(source.dtype).str,'fortran_order':False,'shape':source.shape})
                for data in ordered:
                    block=data.tobytes(order='C');values.update(block);output.write(block)
            # Hashes let a recipient check the file and the decoded values without Hooke.
            archive.writestr(_entry('metadata.json',saved.at),_json({'format':'hooke-native-array-export-v1',
                'document':{'id':project,'session':session,'revision':revision,'digest':saved.digest},
                'input':key,'reference':reference,'scientific':source.descriptor,
                'axes':list(source.axes),'dtype':np.dtype(source.dtype).str,'shape':list(source.shape),
                'values':{'sha256':values.hexdigest(),'order':'C','bytes':math.prod(source.shape)*np.dtype(source.dtype).itemsize},
                'files':{'array.npy':{'sha256':output.digest.hexdigest(),'bytes':output.size}},
                'state':saved.document['state']}))
        check();stream.seek(0);return stream
    except BaseException:
        stream.close();raise
