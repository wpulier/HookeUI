import type * as THREE from 'three';

export type OrganelleType =
  | 'Membrane'
  | 'Cytoplasm'
  | 'Nucleus'
  | 'Nuclear envelope'
  | 'Endoplasmic reticulum'
  | 'Golgi apparatus'
  | 'Mitochondrion'
  | 'Ribosome'
  | 'Vesicle'
  | 'Cytoskeleton'
  | 'Actin cortex'
  | 'Centrosome/MTOC'
  | 'Lysosome'
  | 'Endosome'
  | 'Peroxisome'
  | 'Membrane protein'
  | 'Proteasome';

export interface OrganelleMeta {
  id: string;
  name: string;
  type: OrganelleType;
  description: string;
  biologicalScale: string;
  visualScaleNote: string;
}

export interface InteractiveTarget {
  targetId: string;
  groupId: string;
  object: THREE.Object3D;
  getMeta: (hit?: THREE.Intersection<THREE.Object3D>) => OrganelleMeta;
  getAnchor: (hit?: THREE.Intersection<THREE.Object3D>) => THREE.Vector3;
  setHovered: (hovered: boolean, hit?: THREE.Intersection<THREE.Object3D>) => void;
  setSelected: (selected: boolean, hit?: THREE.Intersection<THREE.Object3D>) => void;
  setDimmed: (dimmed: boolean) => void;
}

export interface HoverDetail {
  meta: OrganelleMeta | null;
}

export interface SelectableItem {
  id: string;
  targetId: string;
  name: string;
  instanceId?: number;
  children?: SelectableItem[];
}

export interface SelectableGroup {
  id: string;
  targetId: string;
  name: string;
  type: OrganelleType;
  color: string;
  count: number;
  children?: SelectableItem[];
}

export type SelectionMode = 'orbit' | 'probe';
export type SelectionSource = 'canvas' | 'rail' | 'probe';

export interface SelectionDetail {
  meta: OrganelleMeta | null;
  targetId?: string;
  groupId?: string;
  instanceId?: number;
  source?: SelectionSource;
  stackIndex?: number;
  stackSize?: number;
}

export interface ProbeStackDetail {
  active: boolean;
  meta: OrganelleMeta | null;
  stackIndex: number;
  stackSize: number;
}

export interface RenderStats {
  drawCalls: number;
  triangles: number;
}

export interface CellSceneController {
  setPaused: (paused: boolean) => void;
  setProbeMode: (enabled: boolean) => void;
  selectTarget: (targetId: string, instanceId?: number) => void;
  clearSelection: () => void;
  getSelectableGroups: () => SelectableGroup[];
  dispose: () => void;
}
