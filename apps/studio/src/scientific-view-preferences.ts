import { textAppearance } from './scientific-text-appearance';
import {scientificGrid,type ScientificGrid} from './scientific-grid';
import {sliceMargins,imageProjection} from './scientific-slices';
import { pointAppearance } from './scientific-point-appearance';
import { surfaceAppearance } from './scientific-surface-appearance';
import { pathAppearance } from './scientific-path-appearance';
import { imageColormap } from './scientific-colormap';
import { imageSampling } from './scientific-image-sampling';
import { clippingPlanes } from './scientific-clipping';
import { imagePlane } from './scientific-image-plane';
import { imageAppearance } from './scientific-image-appearance';
import { labelsAppearance } from './scientific-labels-appearance';
import { layerBlending, scientificGeometry, type ScientificGeometry, type ScientificFrame, type ScientificLayer } from './scientific-view';

export type ScientificViewPreferences = {
  schema: 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14;
  grid?: ScientificGrid;
  dimensions: Pick<ScientificFrame['dimensions'], 'order' | 'ndisplay' | 'point' | 'labels' | 'ranges' | 'margins'>;
  camera: ScientificFrame['camera'];
  layers: Array<Pick<ScientificLayer, 'type' | 'visible' | 'opacity' | 'scale' | 'translate' | 'imageAppearance' | 'blending' | 'imagePlane' | 'clippingPlanes' | 'imageSampling' | 'imageColormap' | 'pathAppearance' | 'surfaceAppearance' | 'pointAppearance' | 'imageProjection' | 'textAppearance' | 'labelsAppearance'> & { key: string; geometry: ScientificGeometry }>;
};
const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const fields = (v: unknown, keys: string): v is Record<string, unknown> => object(v) && Object.keys(v).sort().join(' ') === keys.split(' ').sort().join(' ');
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= Number.MAX_SAFE_INTEGER;
const vector = (v: unknown, count: number): v is number[] => Array.isArray(v) && v.length === count && v.every(number);

export function scientificViewPreferences(raw: unknown): ScientificViewPreferences {
  const fail = (): never => { throw new Error('The remembered scientific view contains unsupported or inconsistent settings.'); };
  if (!fields(raw, 'schema dimensions camera layers'+(object(raw)&&Number(raw.schema)>=12&&'grid' in raw?' grid':'')) || ![1,2,3,4,5,6,7,8,9,10,11,12,13,14].includes(Number(raw.schema)) || typeof raw.schema !== 'number' || new TextEncoder().encode(JSON.stringify(raw)).length > 65536) return fail();
  if('grid' in raw)scientificGrid(raw.grid);
  const d = raw.dimensions, c = raw.camera;
  if (!fields(d, 'order ndisplay point labels ranges'+(Number(raw.schema)>=11&&object(d)&&'margins' in d?' margins':'')) || !Array.isArray(d.order) || d.order.length < 2 || d.order.length > 8 ||
      !d.order.every(Number.isSafeInteger) || [...d.order].sort((a,b)=>a-b).some((n,i)=>n!==i) ||
      ![2,3].includes(Number(d.ndisplay)) || typeof d.ndisplay !== 'number' || d.ndisplay > d.order.length ||
      !vector(d.point,d.order.length) || !Array.isArray(d.labels) || d.labels.length !== d.order.length ||
      !d.labels.every(v=>typeof v==='string' && v.length<=1000) || !Array.isArray(d.ranges) || d.ranges.length!==d.order.length) return fail();
  if('margins' in d)sliceMargins(d.margins,d.order.length);
  for (const [i,bounds] of d.ranges.entries()) {
    if (!vector(bounds,3) || bounds[2]<=0 || d.point[i]<bounds[0] || d.point[i]>bounds[1]) return fail();
  }
  if (!fields(c, 'center zoom orientation angles perspective') || !vector(c.center,3) || !vector(c.angles,3) ||
      !number(c.zoom) || c.zoom<1e-6 || c.zoom>1e6 || !number(c.perspective) || c.perspective<0 || c.perspective>=180 ||
      !Array.isArray(c.orientation) || c.orientation.length!==3 ||
      c.orientation.some((v,i)=>typeof v!=='string' || ![['towards','away'],['up','down'],['left','right']][i].includes(v))) return fail();
  if (!Array.isArray(raw.layers) || raw.layers.length>64) return fail();
  const keys = new Set<string>();
  for (const layer of raw.layers) {
    const labelsDisplay=object(layer)&&Number(raw.schema)>=14&&'labelsAppearance' in layer;
    if(labelsDisplay){if(layer.type!=='labels')return fail();if(labelsAppearance(layer.labelsAppearance).autoSource==='other')throw new Error('These automatic label colors were changed in Python. Choose original colors or new colors from a seed before remembering this view.');}
    const projection=object(layer)&&Number(raw.schema)>=11&&'imageProjection' in layer;
    if(projection){if(layer.type!=='image')return fail();imageProjection(layer.imageProjection);}
    const textDisplay=object(layer)&&Number(raw.schema)>=13&&'textAppearance' in layer;
    if(textDisplay){if(!['points','shapes'].includes(String(layer.type))||!Array.isArray(layer.scale))return fail();textAppearance(layer.textAppearance,layer.scale.length);}
    const point=object(layer)&&Number(raw.schema)>=10&&'pointAppearance' in layer;
    if(point){if(layer.type!=='points')return fail();pointAppearance(layer.pointAppearance);}
    const surface=object(layer)&&Number(raw.schema)>=9&&'surfaceAppearance' in layer;
    if(surface){if(layer.type!=='surface')return fail();surfaceAppearance(layer.surfaceAppearance);}
    const path=object(layer)&&Number(raw.schema)>=8&&'pathAppearance' in layer;
    if(path)pathAppearance(layer.pathAppearance,String(layer.type));
    const colormap=object(layer)&&Number(raw.schema)>=7&&'imageColormap' in layer;
    if(colormap){if(layer.type!=='image')return fail();imageColormap(layer.imageColormap);}
    const sampling = object(layer) && Number(raw.schema)>=6 && 'imageSampling' in layer;
    if(sampling){if(layer.type!=='image')return fail();imageSampling(layer.imageSampling);}
    const clipping = object(layer) && Number(raw.schema)>=5 && 'clippingPlanes' in layer;
    if(clipping)clippingPlanes(layer.clippingPlanes);
    const plane = object(layer) && Number(raw.schema)>=4 && 'imagePlane' in layer;
    if(plane) { if(layer.type!=='image') return fail(); imagePlane(layer.imagePlane); }
    const appearance = object(layer) && ((raw.schema===2 && layer.type==='image') || (Number(raw.schema)>=3 && 'imageAppearance' in layer));
    const blending = object(layer) && Number(raw.schema)>=3 && 'blending' in layer;
    if (blending && !layerBlending.some(value=>value===layer.blending)) return fail();
    if (appearance) { if(layer.type!=='image') return fail(); imageAppearance(layer.imageAppearance); }
    if (!fields(layer,'key type visible opacity scale translate geometry' + (appearance ? ' imageAppearance' : '') + (blending ? ' blending' : '') + (plane ? ' imagePlane' : '') + (clipping ? ' clippingPlanes' : '') + (sampling ? ' imageSampling' : '') + (colormap ? ' imageColormap' : '') + (path ? ' pathAppearance' : '') + (surface ? ' surfaceAppearance' : '') + (point ? ' pointAppearance' : '') + (textDisplay ? ' textAppearance' : '') + (projection ? ' imageProjection' : '') + (labelsDisplay ? ' labelsAppearance' : '')) || typeof layer.key!=='string' ||
        !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(layer.key) || keys.has(layer.key) ||
        typeof layer.type!=='string' || !['image','labels','points','shapes','surface','vectors','tracks'].includes(layer.type) ||
        typeof layer.visible!=='boolean' || !number(layer.opacity) || layer.opacity<0 || layer.opacity>1 ||
        !Array.isArray(layer.scale) || layer.scale.length<2 || layer.scale.length>d.order.length ||
        !vector(layer.scale,layer.scale.length) || !vector(layer.translate,layer.scale.length)) return fail();
    if (!scientificGeometry(layer.geometry,layer.scale.length)) return fail();
    keys.add(layer.key);
  }
  return structuredClone(raw) as ScientificViewPreferences;
}

export function rememberScientificFrame(frame: ScientificFrame): ScientificViewPreferences {
  if (frame.layers.some(layer=>!layer.viewKey)) throw new Error('This view contains temporary layers. The integration must declare their identities before remembering the complete view.');
  const {order,ndisplay,point,labels,ranges,margins} = frame.dimensions;
  return scientificViewPreferences({...('grid' in frame?{grid:frame.grid}:{}),schema:frame.layers.some(layer=>layer.labelsAppearance)?14:frame.layers.some(layer=>layer.textAppearance)?13:frame.grid?12:margins||frame.layers.some(layer=>layer.imageProjection)?11:frame.layers.some(layer=>layer.pointAppearance)?10:frame.layers.some(layer=>layer.surfaceAppearance)?9:frame.layers.some(layer=>layer.pathAppearance)?8:frame.layers.some(layer=>layer.imageColormap)?7:frame.layers.some(layer=>layer.imageSampling)?6:frame.layers.some(layer=>layer.clippingPlanes)?5:frame.layers.some(layer=>layer.imagePlane)?4:frame.layers.some(layer=>layer.blending)?3:frame.layers.some(layer=>layer.imageAppearance)?2:1,dimensions:{order,ndisplay,point,labels,ranges,...(margins?{margins}:{})},camera:frame.camera,
    layers:frame.layers.map(({viewKey,viewGeometry,type,visible,opacity,scale,translate,imageAppearance,blending,imagePlane,clippingPlanes,imageSampling,imageColormap,pathAppearance,surfaceAppearance,pointAppearance,imageProjection,textAppearance,labelsAppearance})=>({key:viewKey,geometry:viewGeometry,type,visible,opacity,scale,translate,...(imageAppearance?{imageAppearance}:{}),...(blending?{blending}:{}),...(imagePlane?{imagePlane}:{}),...(clippingPlanes?{clippingPlanes}:{}),...(imageSampling?{imageSampling}:{}),...(imageColormap?{imageColormap}:{}),...(pathAppearance?{pathAppearance}:{}),...(surfaceAppearance?{surfaceAppearance}:{}),...(pointAppearance?{pointAppearance}:{}),...(textAppearance?{textAppearance}:{}),...(imageProjection?{imageProjection}:{}),...(labelsAppearance?{labelsAppearance}:{})}))});
}
