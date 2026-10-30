/** Join a Stage alias to its exact retained record, never by filename or bytes alone. */
import type {DataEntry} from './data-roles';
import type {AssetMeta} from './protocol';

export function isRetainedMaterial(entry:DataEntry, asset:AssetMeta):boolean {
  const ref=asset.retained;
  return !!ref && entry.id===ref.document && entry.day===ref.session &&
    entry.version===ref.revision && entry.source?.sha256===ref.digest;
}

export function retainedSource(entry:DataEntry,sources:readonly AssetMeta[]):AssetMeta|undefined {
  return sources.find(asset=>isRetainedMaterial(entry,asset));
}
