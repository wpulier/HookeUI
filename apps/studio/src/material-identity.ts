/** Join a Stage alias to its exact retained record, never by filename or bytes alone. */
import type {DataEntry} from './data-roles';
import type {AssetMeta} from './protocol';
import type {ReaderEntry} from './data-reader';

export function isRetainedMaterial(entry:DataEntry, asset:AssetMeta):boolean {
  const ref=asset.retained;
  return !!ref && entry.id===ref.document && entry.day===ref.session &&
    entry.version===ref.revision && entry.source?.sha256===ref.digest;
}

export function retainedSource(entry:DataEntry,sources:readonly AssetMeta[]):AssetMeta|undefined {
  return sources.find(asset=>isRetainedMaterial(entry,asset));
}

/** A source can arrive before inventory; its reader must still use exact evidence. */
export function sourceReaderEntry(asset:AssetMeta,session:string):ReaderEntry {
  const ref=asset.retained;
  const common={title:asset.title,summary:'',state:'saved',actions:[],
    ...(typeof asset.capturedAt==='number'?{at:asset.capturedAt}:{})};
  return ref ? {...common,id:ref.document,kind:'project',day:ref.session,version:ref.revision,
    source:{sha256:ref.digest}} : {...common,id:asset.id,kind:'asset',day:session,asset};
}
