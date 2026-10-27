import { textAppearance, type TextAppearance } from './scientific-text-appearance';
import {scientificGrid,type ScientificGrid} from './scientific-grid';
import {sliceMargins,imageProjection,type SliceMargins,type ImageProjection} from './scientific-slices';
import { pointAppearance, type PointAppearance } from './scientific-point-appearance';
import { surfaceAppearance, type SurfaceAppearance } from './scientific-surface-appearance';
import { pathAppearance, type PathAppearance } from './scientific-path-appearance';
import { imageColormap, colormapChoices, type ImageColormap } from './scientific-colormap';
import { imageSampling, type ImageSampling } from './scientific-image-sampling';
import { clippingPlanes, type ClippingPlane } from './scientific-clipping';
import { imagePlane, type ImagePlane } from './scientific-image-plane';
import { imageAppearance, type ImageAppearance } from './scientific-image-appearance';
import { scientificPanel, type ScientificPanel } from './scientific-controls';
import { labelsTool, type LabelsTool } from './scientific-labels-tool';
import { annotationState, type AnnotationState } from './scientific-annotate';
import { labelsAppearance, type LabelsAppearance } from './scientific-labels-appearance';
import { pointStyle, shapeStyle, textContent, styleChoices, type PointStyle, type ShapeStyle, type TextContent, type StyleChoices } from './scientific-geometry-style';
import { imageHistogram, type ImageHistogram } from './scientific-image-histogram';

export const layerBlending = ['translucent','translucent_no_depth','additive','minimum','opaque','multiplicative'] as const;
export type ScientificLayer = { id: string; name: string; type: 'image' | 'labels' | 'points' | 'shapes' | 'surface' | 'vectors' | 'tracks';
  visible: boolean; opacity: number; elementCount?: number; scale: number[]; translate: number[]; editableInput?: string; inputMeaning?: {semantic: string; axes: string[]}; outputMeaning?: {semantic: string; axes: string[]}; viewKey?: string; viewGeometry?: ScientificGeometry; imageAppearance?: ImageAppearance; imageProjection?: ImageProjection; pathAppearance?: PathAppearance; surfaceAppearance?: SurfaceAppearance; pointAppearance?: PointAppearance; labelsTool?: LabelsTool; annotation?: AnnotationState; textAppearance?: TextAppearance; imagePlane?: ImagePlane; imageSampling?: ImageSampling; imageColormap?: ImageColormap; colormapChoices?: string[]; clippingPlanes?: ClippingPlane[]; blending?: typeof layerBlending[number]; labelsAppearance?: LabelsAppearance; labelsSelectedColor?: [number, number, number, number] | null; imageHistogram?: ImageHistogram; pointStyle?: PointStyle; shapeStyle?: ShapeStyle; textContent?: TextContent; styleChoices?: StyleChoices };
export type ScientificGeometry = { affine: number[][]; axes: string[]; units: string[] };
export type ScientificFrame = {
  format: 'scientific-host-v1'; frame: string; width: number; height: number; pixelRatio: 1;
  derivedDisplay: true; privatePreview: true; dirty: boolean; layers: ScientificLayer[];
  dimensions: { order: number[]; displayed: number[]; ndisplay: 2 | 3; steps: number[]; counts: number[]; labels: string[]; ranges: number[][]; point: number[]; margins?: SliceMargins; lastUsed?: number; rollable?: boolean[]; units?: string[] | null };
  camera: { center: number[]; zoom: number; orientation: ['towards' | 'away', 'up' | 'down', 'left' | 'right']; angles: number[]; perspective: number };
  grid?: ScientificGrid;
  nativePanelAvailable?: boolean;
  toolDocumentAvailable?: boolean;
  layerSelection?: {selected:string[];active:string|null};
  controls: ScientificPanel;
};

const object = (v: unknown): v is Record<string, unknown> => v !== null && typeof v === 'object' && !Array.isArray(v);
const fields = (v: unknown, names: string): v is Record<string, unknown> => object(v) && Object.keys(v).sort().join(' ') === names.split(' ').sort().join(' ');
const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max;
const number = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= Number.MAX_SAFE_INTEGER;
const text = (v: unknown): v is string => typeof v === 'string' && v.length <= 1000;
const token = (v: unknown, size = 32): v is string => typeof v === 'string' && new RegExp(`^[a-f0-9]{${size}}$`).test(v);
const vector = (v: unknown, size: number): v is number[] => Array.isArray(v) && v.length === size && v.every(number);
const roundEven = (v: number) => v - Math.floor(v) === .5 ? Math.floor(v) + Math.floor(v) % 2 : Math.round(v);

export function scientificFrame(value: unknown): ScientificFrame {
  const fail = (): never => { throw new Error('This scientific view returned unsupported or inconsistent metadata. Reopen its saved work.'); };
  if (!fields(value, 'format frame width height pixelRatio derivedDisplay privatePreview dirty layers dimensions camera controls'+(object(value)&&'grid' in value?' grid':'')+(object(value)&&'layerSelection' in value?' layerSelection':'')+(object(value)&&'nativePanelAvailable' in value?' nativePanelAvailable':'')+(object(value)&&'toolDocumentAvailable' in value?' toolDocumentAvailable':'')) ||
      value.format !== 'scientific-host-v1' || !token(value.frame, 64) || !integer(value.width, 1, 1024) || !integer(value.height, 1, 1024) ||
      value.pixelRatio !== 1 || value.derivedDisplay !== true || value.privatePreview !== true || typeof value.dirty !== 'boolean') return fail();
  if (new TextEncoder().encode(JSON.stringify(value)).length > 128 * 1024 - 32) return fail();
  if ('nativePanelAvailable' in value && typeof value.nativePanelAvailable !== 'boolean') return fail();
  if ('toolDocumentAvailable' in value && typeof value.toolDocumentAvailable !== 'boolean') return fail();
  if('grid' in value)scientificGrid(value.grid);
  const d = value.dimensions, c = value.camera;
  if (!fields(d, 'order displayed ndisplay steps counts labels ranges point'+['margins','lastUsed','rollable','units'].filter(name=>object(d)&&name in d).map(name=>' '+name).join('')) || !Array.isArray(d.order) || d.order.length < 2 || d.order.length > 8 ||
      !d.order.every(n => integer(n, 0, 7)) || [...d.order].sort((a, b) => a - b).some((n, index) => n !== index) ||
      !integer(d.ndisplay, 2, 3) || d.ndisplay > d.order.length || !Array.isArray(d.displayed) ||
      d.displayed.length !== d.ndisplay) return fail();
  if('margins' in d)sliceMargins(d.margins,d.order.length);
  if('lastUsed' in d && !integer(d.lastUsed,0,d.order.length-1)) return fail();
  if('rollable' in d && (!Array.isArray(d.rollable) || d.rollable.length !== d.order.length || !d.rollable.every(flag=>typeof flag==='boolean'))) return fail();
  if('units' in d && d.units!==null && (!Array.isArray(d.units) || d.units.length !== d.order.length || !d.units.every(unit=>text(unit)&&unit.length<=100))) return fail();
  const displayed = d.order.slice(-d.ndisplay);
  if (d.displayed.some((n, index) => n !== displayed[index])) return fail();
  const counts = d.counts;
  if (!Array.isArray(counts) || counts.length !== d.order.length || !counts.every(n => integer(n, 1)) ||
      !Array.isArray(d.steps) || d.steps.length !== d.order.length || !d.steps.every(n => integer(n)) ||
      !Array.isArray(d.labels) || d.labels.length !== d.order.length || !d.labels.every(text) ||
      !Array.isArray(d.ranges) || d.ranges.length !== d.order.length || !vector(d.point, d.order.length)) return fail();
  for (const [i, bounds] of d.ranges.entries()) {
    if (!vector(bounds, 3)) return fail();
    const [start, stop, spacing] = bounds, point = d.point[i];
    if (start > point || point > stop || spacing <= 0) return fail();
    const span = (stop - start) / spacing, position = (point - start) / spacing;
    if (!number(span) || !number(position) || counts[i] !== Math.floor(span) + 1 || d.steps[i] !== roundEven(position)) return fail();
  }
  if (!fields(c, 'center zoom orientation angles perspective') || !vector(c.center, 3) || !vector(c.angles, 3) ||
      !number(c.zoom) || c.zoom <= 0 || !number(c.perspective) || c.perspective < 0 || c.perspective >= 180 ||
      !Array.isArray(c.orientation) || c.orientation.length !== 3 ||
      c.orientation.some((v, i) => typeof v !== 'string' || ![['towards', 'away'], ['up', 'down'], ['left', 'right']][i].includes(v))) return fail();
  scientificLayers(value.layers, d.order.length);
  if('layerSelection' in value){
    const selection=value.layerSelection;
    if(!fields(selection,'selected active')||!Array.isArray(selection.selected))return fail();
    const ids=new Set((value.layers as ScientificLayer[]).map(layer=>layer.id));
    if(!selection.selected.every(id=>token(id)&&ids.has(id))||new Set(selection.selected).size!==selection.selected.length||
       selection.active!==(selection.selected.length===1?selection.selected[0]:null))return fail();
  }
  scientificPanel(value.controls);
  return structuredClone(value) as ScientificFrame;
}

export function scientificGeometry(value: unknown, ndim: number): value is ScientificGeometry {
  if (!fields(value,'affine axes units') || !Array.isArray(value.affine) || value.affine.length!==ndim+1 ||
      !value.affine.every(row=>vector(row,ndim+1)) ||
      value.affine[ndim].some((n:number,i:number)=>n!==(i===ndim?1:0))) return false;
  return ['axes','units'].every(key=>Array.isArray(value[key]) && value[key].length===ndim && value[key].every(text));
}


function scientificLayers(layers: unknown, ndim: number): void {
  const fail = (): never => { throw new Error('The scientific layer inventory returned unsupported metadata. Refresh its layers.'); };
  if (!Array.isArray(layers) || layers.length > 64) return fail();
  const seen = new Set<string>(), editableInputs = new Set<string>(), viewKeys = new Set<string>();
  for (const layer of layers) {
    const counted=object(layer)&&'elementCount' in layer;
    if(counted&&(!['points','vectors','tracks','shapes'].includes(String(layer.type))||!integer(layer.elementCount)))return fail();
    const editable = object(layer) && 'editableInput' in layer;
    const bound = object(layer) && 'viewKey' in layer;
    const inputMeaning = object(layer) && 'inputMeaning' in layer;
    if (inputMeaning) {
      const meaning = layer.inputMeaning;
      if (!editable || !fields(meaning, 'semantic axes') ||
          !(layer.type === 'image' ? ['intensity','probabilities'] : ['class-labels','instance-labels']).includes(String(meaning.semantic)) ||
          !Array.isArray(meaning.axes) || !Array.isArray(layer.scale) || meaning.axes.length !== layer.scale.length ||
          new Set(meaning.axes).size !== meaning.axes.length || meaning.axes.some(axis => typeof axis !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(axis))) return fail();
    }
    const outputMeaning = object(layer) && 'outputMeaning' in layer;
    if (outputMeaning) {
      const meaning = layer.outputMeaning;
      const kinds: Record<string, string> = {'intensity':'image','probabilities':'image',
        'class-labels':'labels','instance-labels':'labels','point-set':'points',
        'track-coordinates':'tracks','vector-set':'vectors','surface-mesh':'surface','shape-set':'shapes'};
      if (!bound || editable || inputMeaning || !fields(meaning, 'semantic axes') ||
          typeof meaning.semantic !== 'string' || kinds[meaning.semantic] !== layer.type ||
          !Array.isArray(meaning.axes) || meaning.axes.length < 2 || meaning.axes.length > 8 ||
          !Array.isArray(layer.scale) || meaning.axes.length !== layer.scale.length ||
          new Set(meaning.axes).size !== meaning.axes.length ||
          meaning.axes.some(axis => typeof axis !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(axis)) ||
          (layer.type === 'tracks' && meaning.axes[0] !== 't')) return fail();
    }
    const projection=object(layer)&&'imageProjection' in layer;
    if(projection){if(layer.type!=='image')return fail();imageProjection(layer.imageProjection);}
    const textDisplay=object(layer)&&'textAppearance' in layer;
    if(textDisplay){if(!['points','shapes'].includes(String(layer.type))||!Array.isArray(layer.scale))return fail();textAppearance(layer.textAppearance,layer.scale.length);}
    const point=object(layer)&&'pointAppearance' in layer;
    if(point){if(layer.type!=='points')return fail();pointAppearance(layer.pointAppearance);}
    const annotation=object(layer)&&'annotation' in layer;
    if(annotation){if(!['points','shapes'].includes(String(layer.type)))return fail();annotationState(layer.annotation);}
    const tool=object(layer)&&'labelsTool' in layer;
    if(tool){if(layer.type!=='labels'||!('editableInput' in layer))return fail();labelsTool(layer.labelsTool);}
    const markers=object(layer)&&'pointStyle' in layer, shapes=object(layer)&&'shapeStyle' in layer, content=object(layer)&&'textContent' in layer, choices=object(layer)&&'styleChoices' in layer;
    if(markers){if(layer.type!=='points'||!choices)return fail();pointStyle(layer.pointStyle,false);}
    if(shapes){if(layer.type!=='shapes'||!choices)return fail();shapeStyle(layer.shapeStyle,false);}
    if(content){if(!['points','shapes'].includes(String(layer.type)))return fail();textContent(layer.textContent,false);}
    if(choices){if(!['points','shapes'].includes(String(layer.type)))return fail();styleChoices(layer.styleChoices);}
    const surface=object(layer)&&'surfaceAppearance' in layer;
    if(surface){if(layer.type!=='surface')return fail();surfaceAppearance(layer.surfaceAppearance);}
    const path = object(layer) && 'pathAppearance' in layer;
    if(path)pathAppearance(layer.pathAppearance,String(layer.type));
    const colormap = object(layer) && 'imageColormap' in layer;
    if(colormap){if(layer.type!=='image')return fail();imageColormap(layer.imageColormap);colormapChoices(layer.colormapChoices);}
    const sampling = object(layer) && 'imageSampling' in layer;
    if(sampling){if(layer.type!=='image')return fail();imageSampling(layer.imageSampling);}
    const clipping = object(layer) && 'clippingPlanes' in layer;
    if(clipping)clippingPlanes(layer.clippingPlanes);
    const plane = object(layer) && 'imagePlane' in layer;
    if (plane) { if(layer.type!=='image') return fail(); imagePlane(layer.imagePlane); }
    const labelsDisplay = object(layer) && 'labelsAppearance' in layer;
    if (labelsDisplay) {
      const color = layer.labelsSelectedColor;
      if (layer.type !== 'labels' || !(color === null || (Array.isArray(color) && color.length === 4 &&
          color.every(n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 1)))) return fail();
      labelsAppearance(layer.labelsAppearance);
    }
    const histogram = object(layer) && 'imageHistogram' in layer;
    if (histogram) { if (layer.type !== 'image') return fail(); imageHistogram(layer.imageHistogram); }
    const appearance = object(layer) && 'imageAppearance' in layer;
    const blending = object(layer) && 'blending' in layer;
    if (blending && !layerBlending.some(value=>value===layer.blending)) return fail();
    if (appearance) { if (layer.type !== 'image') return fail(); imageAppearance(layer.imageAppearance); }
    if (bound && (typeof layer.viewKey !== 'string' || !/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(layer.viewKey) || viewKeys.has(layer.viewKey))) return fail();
    if (bound) viewKeys.add(String(layer.viewKey));
    if (editable && (typeof layer.editableInput !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(layer.editableInput) ||
        !['image', 'labels'].includes(String(layer.type)) || editableInputs.has(layer.editableInput))) return fail();
    if (editable) editableInputs.add(String(layer.editableInput));
    if (!fields(layer, `id name type visible opacity scale translate${counted ? ' elementCount' : ''}${editable ? ' editableInput' : ''}${inputMeaning ? ' inputMeaning' : ''}${outputMeaning ? ' outputMeaning' : ''}${bound ? ' viewKey viewGeometry' : ''}${appearance ? ' imageAppearance' : ''}${path ? ' pathAppearance' : ''}${surface ? ' surfaceAppearance' : ''}${point ? ' pointAppearance' : ''}${tool ? ' labelsTool' : ''}${annotation ? ' annotation' : ''}${textDisplay ? ' textAppearance' : ''}${projection ? ' imageProjection' : ''}${plane ? ' imagePlane' : ''}${clipping ? ' clippingPlanes' : ''}${sampling ? ' imageSampling' : ''}${colormap ? ' imageColormap colormapChoices' : ''}${blending ? ' blending' : ''}${labelsDisplay ? ' labelsAppearance labelsSelectedColor' : ''}${histogram ? ' imageHistogram' : ''}${markers ? ' pointStyle' : ''}${shapes ? ' shapeStyle' : ''}${content ? ' textContent' : ''}${choices ? ' styleChoices' : ''}`) || !token(layer.id) || seen.has(layer.id) || !text(layer.name) ||
        typeof layer.type !== 'string' || !['image', 'labels', 'points', 'shapes', 'surface', 'vectors', 'tracks'].includes(layer.type) ||
        typeof layer.visible !== 'boolean' || !number(layer.opacity) || layer.opacity < 0 || layer.opacity > 1 ||
        !Array.isArray(layer.scale) || layer.scale.length < 2 || layer.scale.length > ndim ||
        !vector(layer.scale, layer.scale.length) || !vector(layer.translate, layer.scale.length)) return fail();
    if (bound && !scientificGeometry(layer.viewGeometry, layer.scale.length)) return fail();
    seen.add(layer.id);
  }
}

export function scientificInventory(value: unknown): ScientificLayer[] {
  if (!fields(value, 'format layers') || value.format !== 'scientific-layers-v1' ||
      new TextEncoder().encode(JSON.stringify(value)).length > 128 * 1024 - 32)
    throw new Error('The scientific layer inventory was not confirmed. Refresh its layers.');
  scientificLayers(value.layers, 8);
  return structuredClone(value.layers) as ScientificLayer[];
}
