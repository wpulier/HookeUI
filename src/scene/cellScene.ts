import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { EffectComposer } from 'three/examples/jsm/postprocessing/EffectComposer.js';
import { OutputPass } from 'three/examples/jsm/postprocessing/OutputPass.js';
import { RenderPass } from 'three/examples/jsm/postprocessing/RenderPass.js';
import { SSAOPass } from 'three/examples/jsm/postprocessing/SSAOPass.js';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';
import type {
  CellSceneController,
  HoverDetail,
  InteractiveTarget,
  OrganelleMeta,
  ProbeStackDetail,
  RenderStats,
  SelectableGroup,
  SelectionDetail,
  SelectionSource,
} from './types';

const CELL_RADIUS = 5;
const CELL_SCALE = new THREE.Vector3(1.12, 0.78, 0.92);
const CELL_BOUNDS = new THREE.Vector3(
  CELL_RADIUS * CELL_SCALE.x,
  CELL_RADIUS * CELL_SCALE.y,
  CELL_RADIUS * CELL_SCALE.z,
);
const POINTER_IDLE = new THREE.Vector2(2, 2);
const SELECT_COLOR = new THREE.Color('#f4d38a');
const MEMBRANE_CUT_RADIUS = 0.46;
const MEMBRANE_CUT_RIM_WIDTH = 0.024;

type AnimatedTask = (elapsed: number, delta: number) => void;
type SurfaceVisualMode = 'overview' | 'surface-focus' | 'interior-focus';

const SURFACE_VISUALS: Record<
  SurfaceVisualMode,
  {
    membraneAlpha: number;
    cytoplasmOpacity: number;
    cytoplasmSize: number;
    actinOpacity: number;
    actinPulse: number;
    membraneProteinOpacity: number;
    membraneProteinEmissive: number;
  }
> = {
  overview: {
    membraneAlpha: 0.2,
    cytoplasmOpacity: 0.34,
    cytoplasmSize: 0.038,
    actinOpacity: 0.5,
    actinPulse: 0.018,
    membraneProteinOpacity: 0.96,
    membraneProteinEmissive: 1.18,
  },
  'surface-focus': {
    membraneAlpha: 0.22,
    cytoplasmOpacity: 0.36,
    cytoplasmSize: 0.04,
    actinOpacity: 0.54,
    actinPulse: 0.015,
    membraneProteinOpacity: 1,
    membraneProteinEmissive: 1.34,
  },
  'interior-focus': {
    membraneAlpha: 0.005,
    cytoplasmOpacity: 0.018,
    cytoplasmSize: 0.024,
    actinOpacity: 0.024,
    actinPulse: 0.001,
    membraneProteinOpacity: 0.018,
    membraneProteinEmissive: 0.16,
  },
};

interface HoverState {
  target: InteractiveTarget;
  hit: THREE.Intersection<THREE.Object3D>;
}

interface TargetRef {
  target: InteractiveTarget;
  hit?: THREE.Intersection<THREE.Object3D>;
}

interface SurfaceInstanceTransform {
  normal: THREE.Vector3;
  visibleMatrix: THREE.Matrix4;
  hiddenMatrix: THREE.Matrix4;
}

interface SelectionState {
  refs: TargetRef[];
  detail: SelectionDetail;
}

interface OrganelleMaterials {
  membrane: THREE.ShaderMaterial;
  cytoplasm: THREE.PointsMaterial;
  nucleusEnvelope: THREE.MeshPhysicalMaterial;
  nucleolus: THREE.MeshPhysicalMaterial;
  chromatin: THREE.LineBasicMaterial;
  er: THREE.MeshPhysicalMaterial;
  smoothEr: THREE.MeshPhysicalMaterial;
  golgi: THREE.MeshPhysicalMaterial;
  mitochondria: THREE.MeshPhysicalMaterial;
  cristae: THREE.LineBasicMaterial;
  ribosome: THREE.MeshStandardMaterial;
  vesicle: THREE.MeshPhysicalMaterial;
  cytoskeleton: THREE.MeshStandardMaterial;
  microtubule: THREE.MeshStandardMaterial;
  intermediateFilament: THREE.LineBasicMaterial;
  actin: THREE.LineBasicMaterial;
  centrosome: THREE.MeshStandardMaterial;
  membraneProtein: THREE.MeshStandardMaterial;
  lysosome: THREE.MeshPhysicalMaterial;
  endosome: THREE.MeshPhysicalMaterial;
  peroxisome: THREE.MeshPhysicalMaterial;
  proteasome: THREE.MeshStandardMaterial;
  cargoTrail: THREE.LineBasicMaterial;
  pore: THREE.MeshStandardMaterial;
}

const meta = {
  membrane: {
    id: 'cell-membrane',
    name: 'Cell membrane',
    type: 'Membrane',
    description:
      'A flexible phospholipid bilayer with embedded proteins that separates cytoplasm from the environment and controls molecular passage.',
    biologicalScale: 'About 7.5 nanometers thick in real cells.',
    visualScaleNote: 'Rendered as an exaggerated translucent shell so membrane motion and boundaries remain visible.',
  },
  cytoplasm: {
    id: 'cytoplasm',
    name: 'Cytoplasm and cytosol',
    type: 'Cytoplasm',
    description:
      'A densely crowded, water-rich intracellular medium where organelles, ions, metabolites, proteins, RNA, and proteolytic machinery move through active cytosolic currents.',
    biologicalScale: 'Fills the volume between plasma membrane and nuclear envelope.',
    visualScaleNote: 'Crowding particles are deliberately enlarged and class-colored so molecular soup remains visible without hiding organelles.',
  },
  nucleus: {
    id: 'nucleus',
    name: 'Nucleus',
    type: 'Nucleus',
    description:
      'The largest animal-cell organelle, with a double envelope, nuclear pore complexes, chromatin territories, and a nucleolus for ribosomal RNA production.',
    biologicalScale: 'Common mammalian nuclei are roughly 5-10 micrometers across.',
    visualScaleNote: 'Mapped to about one scene unit in radius; envelope spacing, pores, and chromatin domains are enlarged for legibility.',
  },
  nuclearPores: {
    id: 'nuclear-pores',
    name: 'Nuclear pore complexes',
    type: 'Nuclear envelope',
    description:
      'Protein-lined openings in the double nuclear envelope that regulate RNA and macromolecule traffic between nucleus and cytoplasm.',
    biologicalScale: 'Nanometer-scale pores distributed across the nuclear envelope.',
    visualScaleNote: 'Pores are enlarged as luminous rings to make nuclear transport sites hoverable.',
  },
  roughEr: {
    id: 'rough-er',
    name: 'Rough endoplasmic reticulum',
    type: 'Endoplasmic reticulum',
    description:
      'Flattened membrane cisternae continuous with the nuclear envelope, studded with ribosomes that synthesize secreted and membrane proteins.',
    biologicalScale: 'An extended membrane network concentrated near the nucleus.',
    visualScaleNote: 'Folded sheets visibly emerge from the nuclear envelope; bound ribosomes are enlarged and attached to membrane surfaces.',
  },
  smoothEr: {
    id: 'smooth-er',
    name: 'Smooth endoplasmic reticulum',
    type: 'Endoplasmic reticulum',
    description:
      'A ribosome-free tubular ER network involved in lipid synthesis, calcium handling, and detoxification-like processing.',
    biologicalScale: 'Tubular membrane network branching through the cytoplasm.',
    visualScaleNote: 'Shown as smoother branching tubes with slow elastic drift.',
  },
  golgi: {
    id: 'golgi',
    name: 'Golgi apparatus',
    type: 'Golgi apparatus',
    description:
      'Polarized stacked cisternae that receive ER cargo at the cis face, modify and sort it, then bud vesicles from the trans face.',
    biologicalScale: 'Ribbon-like stacks commonly positioned near the nucleus in mammalian cells.',
    visualScaleNote: 'Curved stacked membranes emphasize cis-to-trans polarity and budding vesicles.',
  },
  mitochondria: {
    id: 'mitochondrion',
    name: 'Mitochondrion',
    type: 'Mitochondrion',
    description:
      'A double-membrane energy organelle whose folded inner cristae expand membrane area for ATP-producing cellular respiration.',
    biologicalScale: 'Often about 0.5-2 micrometers long, with abundance varying by metabolic demand.',
    visualScaleNote: 'Each mitochondrion is enlarged and semi-transparent so internal cristae remain visible.',
  },
  ribosome: {
    id: 'ribosome',
    name: 'Ribosome',
    type: 'Ribosome',
    description:
      'A protein-RNA complex that translates messenger RNA into protein, either free in cytosol or attached to rough ER.',
    biologicalScale: 'About 15-25 nanometers across.',
    visualScaleNote: 'Strongly enlarged and instanced so individual ribosomes can be identified on hover.',
  },
  vesicle: {
    id: 'vesicle',
    name: 'Transport vesicle',
    type: 'Vesicle',
    description:
      'A small membrane-bound carrier that moves proteins, lipids, waste, and signaling cargo between ER, Golgi, lysosomal routes, and membrane.',
    biologicalScale: 'Usually tens to hundreds of nanometers depending on vesicle class.',
    visualScaleNote: 'Rendered larger with looping paths that reveal intracellular transport routes.',
  },
  cytoskeleton: {
    id: 'cytoskeleton',
    name: 'Cytoskeleton',
    type: 'Cytoskeleton',
    description:
      'A structural and transport scaffold of microtubules and filaments that maintains cell shape and organizes organelle movement.',
    biologicalScale: 'Filaments are nanometer-scale but can span much of the cell.',
    visualScaleNote: 'Shown as fine colored tubes tracing support lines through the cytoplasm.',
  },
  actinCortex: {
    id: 'actin-cortex',
    name: 'Actin cortex',
    type: 'Actin cortex',
    description:
      'A dense mesh of actin filaments just under the plasma membrane that supports cell shape, membrane ruffling, and cortical mechanics.',
    biologicalScale: 'Nanometer-thick filaments organized as a thin cortex below the membrane.',
    visualScaleNote: 'Rendered as a fine translucent line network under the membrane so the cortex reads separately from the outer bilayer.',
  },
  centrosome: {
    id: 'centrosome',
    name: 'Centrosome / MTOC',
    type: 'Centrosome/MTOC',
    description:
      'The main microtubule organizing center in many mammalian cells, nucleating microtubules that radiate through the cytoplasm.',
    biologicalScale: 'A compact perinuclear structure containing centrioles and pericentriolar material.',
    visualScaleNote: 'Enlarged as a paired centriole marker with microtubules radiating from it.',
  },
  membraneProteins: {
    id: 'membrane-proteins',
    name: 'Membrane proteins',
    type: 'Membrane protein',
    description:
      'Embedded channels, receptors, and transporters that break up the plasma membrane surface and control signaling and exchange.',
    biologicalScale: 'Nanometer-scale proteins distributed unevenly across the membrane.',
    visualScaleNote: 'Instanced and enlarged as sparse protrusions so the membrane is not a perfectly smooth shell.',
  },
  lysosome: {
    id: 'lysosome',
    name: 'Lysosome',
    type: 'Lysosome',
    description:
      'An acidic degradative organelle that digests macromolecules and receives cargo from endocytic and autophagic routes.',
    biologicalScale: 'Typically hundreds of nanometers across.',
    visualScaleNote: 'Grouped with other vesicular organelles and enlarged with denser warm contents.',
  },
  endosome: {
    id: 'endosome',
    name: 'Endosome',
    type: 'Endosome',
    description:
      'A sorting compartment for internalized membrane and extracellular cargo, connecting membrane uptake to recycling and lysosomal pathways.',
    biologicalScale: 'Variable vesicular compartments from tens of nanometers to over a micrometer.',
    visualScaleNote: 'Shown as cooler translucent compartments moving between membrane and Golgi-adjacent regions.',
  },
  peroxisome: {
    id: 'peroxisome',
    name: 'Peroxisome',
    type: 'Peroxisome',
    description:
      'A small oxidative metabolic organelle involved in lipid processing and reactive oxygen species handling.',
    biologicalScale: 'Usually submicron, with size and number varying by cell type.',
    visualScaleNote: 'Rendered as small pale organelles distinct from transport vesicles and lysosomes.',
  },
  proteasome: {
    id: 'proteasome',
    name: 'Proteasome',
    type: 'Proteasome',
    description:
      'A protein-degradation complex that helps maintain cytosolic protein quality by breaking down tagged proteins.',
    biologicalScale: 'Tens of nanometers long.',
    visualScaleNote: 'Strongly enlarged and instanced as tiny barrel-like particles inside the molecular crowding field.',
  },
} satisfies Record<string, OrganelleMeta>;

const selectionMeta = {
  er: {
    ...meta.roughEr,
    id: 'er',
    name: 'Endoplasmic reticulum',
    description:
      'A connected membrane system combining rough protein-synthesizing sheets and smooth lipid-handling tubules around the nucleus.',
    visualScaleNote: 'Rough and smooth ER are grouped for direct selection, with each branch still selectable from the rail drawer.',
  },
  mitochondria: {
    ...meta.mitochondria,
    id: 'mitochondria',
    name: 'Mitochondria',
    description:
      'A distributed population of energy organelles with individual motion, pulsing, and internal cristae detail.',
    visualScaleNote: 'All mitochondria are highlighted together from the rail; individual mitochondria remain selectable.',
  },
  vesicles: {
    ...meta.vesicle,
    id: 'vesicles',
    name: 'Transport vesicles',
    description:
      'Multiple membrane-bound carriers moving along looping intracellular paths between ER, Golgi, and membrane-proximal regions.',
    visualScaleNote: 'The rail can select the vesicle population or individual vesicles for inspection.',
  },
} satisfies Record<string, OrganelleMeta>;

function makeRng(seed: number) {
  let value = seed;
  return () => {
    value += 0x6d2b79f5;
    let result = Math.imul(value ^ (value >>> 15), value | 1);
    result ^= result + Math.imul(result ^ (result >>> 7), result | 61);
    return ((result ^ (result >>> 14)) >>> 0) / 4294967296;
  };
}

function randomRange(rng: () => number, min: number, max: number) {
  return min + (max - min) * rng();
}

function randomPointInCell(rng: () => number, margin = 0.3) {
  for (let attempt = 0; attempt < 200; attempt += 1) {
    const point = new THREE.Vector3(
      randomRange(rng, -CELL_BOUNDS.x + margin, CELL_BOUNDS.x - margin),
      randomRange(rng, -CELL_BOUNDS.y + margin, CELL_BOUNDS.y - margin),
      randomRange(rng, -CELL_BOUNDS.z + margin, CELL_BOUNDS.z - margin),
    );
    const normalized =
      point.x ** 2 / CELL_BOUNDS.x ** 2 +
      point.y ** 2 / CELL_BOUNDS.y ** 2 +
      point.z ** 2 / CELL_BOUNDS.z ** 2;
    const awayFromNucleus = point.distanceTo(new THREE.Vector3(-0.8, 0.1, 0.1)) > 1.55;

    if (normalized < 0.82 && awayFromNucleus) {
      return point;
    }
  }

  return new THREE.Vector3(1.2, 0, 0);
}

function cellSurfacePoint(phi: number, theta: number, inset = 1) {
  return new THREE.Vector3(
    Math.sin(phi) * Math.cos(theta) * CELL_BOUNDS.x * inset,
    Math.cos(phi) * CELL_BOUNDS.y * inset,
    Math.sin(phi) * Math.sin(theta) * CELL_BOUNDS.z * inset,
  );
}

function randomSurfacePoint(rng: () => number, inset = 1) {
  return cellSurfacePoint(Math.acos(2 * rng() - 1), rng() * Math.PI * 2, inset);
}

function normalFromCellPoint(point: THREE.Vector3) {
  return new THREE.Vector3(
    point.x / CELL_BOUNDS.x,
    point.y / CELL_BOUNDS.y,
    point.z / CELL_BOUNDS.z,
  ).normalize();
}

function createMitochondrionGeometry(length: number, radius: number, phase: number) {
  const lengthSegments = 34;
  const radialSegments = 16;
  const positions: number[] = [];
  const indices: number[] = [];

  for (let i = 0; i <= lengthSegments; i += 1) {
    const t = i / lengthSegments;
    const x = (t - 0.5) * length;
    const centerY = Math.sin(t * Math.PI * 1.2 + phase) * radius * 0.32;
    const centerZ = Math.sin(t * Math.PI * 2.1 + phase * 0.7) * radius * 0.22;
    const taper = Math.sin(Math.PI * t);
    const localRadius = radius * (0.34 + 0.72 * Math.pow(Math.max(taper, 0.001), 0.42));

    for (let j = 0; j < radialSegments; j += 1) {
      const angle = (j / radialSegments) * Math.PI * 2;
      const asymmetry =
        1 +
        Math.sin(angle * 2.0 + phase + t * 4.7) * 0.13 +
        Math.cos(angle * 3.0 - phase * 0.4 + t * 7.5) * 0.07;
      positions.push(
        x,
        centerY + Math.cos(angle) * localRadius * asymmetry * 0.78,
        centerZ + Math.sin(angle) * localRadius * asymmetry,
      );
    }
  }

  for (let i = 0; i < lengthSegments; i += 1) {
    for (let j = 0; j < radialSegments; j += 1) {
      const a = i * radialSegments + j;
      const b = i * radialSegments + ((j + 1) % radialSegments);
      const c = (i + 1) * radialSegments + j;
      const d = (i + 1) * radialSegments + ((j + 1) % radialSegments);
      indices.push(a, c, b, b, c, d);
    }
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  return geometry;
}

function createCristaeGeometry(length: number, radius: number, phase: number, band: number) {
  const points: THREE.Vector3[] = [];

  for (let index = 0; index < 48; index += 1) {
    const t = index / 47;
    const x = (t - 0.5) * length * 0.78;
    const centerY = Math.sin(t * Math.PI * 1.2 + phase) * radius * 0.23;
    const centerZ = Math.sin(t * Math.PI * 2.1 + phase * 0.7) * radius * 0.15;
    points.push(
      new THREE.Vector3(
        x,
        centerY + Math.sin(t * Math.PI * 8.5 + phase + band) * radius * 0.18 + (band - 2.5) * radius * 0.15,
        centerZ + radius * 0.48,
      ),
    );
  }

  return new THREE.BufferGeometry().setFromPoints(points);
}

function createFoldedSheetGeometry(width: number, height: number, phase: number) {
  const geometry = new THREE.PlaneGeometry(width, height, 54, 12);
  const position = geometry.attributes.position as THREE.BufferAttribute;

  for (let index = 0; index < position.count; index += 1) {
    const x = position.getX(index);
    const y = position.getY(index);
    const longitudinal = x / width;
    const edgeCurl = Math.sin((longitudinal + 0.5) * Math.PI) * 0.08;
    const z =
      Math.sin(x * 2.4 + phase) * 0.12 +
      Math.cos(y * 5.2 + phase * 0.7) * 0.055 +
      Math.sin((x + y) * 5.0 + phase) * 0.025;
    position.setZ(index, z + edgeCurl);
  }

  geometry.computeVertexNormals();
  return geometry;
}

function createSurfaceLineGeometry(rng: () => number, count: number, inset: number, segmentLength: number) {
  const points: number[] = [];

  for (let index = 0; index < count; index += 1) {
    const start = randomSurfacePoint(rng, inset);
    const normal = normalFromCellPoint(start);
    const tangent = new THREE.Vector3(-normal.z, randomRange(rng, -0.32, 0.32), normal.x).normalize();
    const bitangent = new THREE.Vector3().crossVectors(normal, tangent).normalize();
    const direction = tangent.multiplyScalar(Math.cos(index) * segmentLength).add(bitangent.multiplyScalar(Math.sin(index * 1.7) * segmentLength));
    const end = start.clone().add(direction);
    const normalized =
      end.x ** 2 / (CELL_BOUNDS.x * inset) ** 2 +
      end.y ** 2 / (CELL_BOUNDS.y * inset) ** 2 +
      end.z ** 2 / (CELL_BOUNDS.z * inset) ** 2;
    if (normalized > 0) {
      end.multiplyScalar(1 / Math.sqrt(normalized));
    }
    points.push(start.x, start.y, start.z, end.x, end.y, end.z);
  }

  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(points, 3));
  return geometry;
}

function createInstancedSurfaceMesh(
  mesh: THREE.InstancedMesh,
  rng: () => number,
  count: number,
  tempObject: THREE.Object3D,
  colors: THREE.Color[],
  palette: string[],
  inset = 1.012,
  cutDirection?: THREE.Vector3,
) {
  const yAxis = new THREE.Vector3(0, 1, 0);
  const normalizedCutDirection = cutDirection?.clone().normalize();
  const transforms: SurfaceInstanceTransform[] = [];

  for (let index = 0; index < count; index += 1) {
    const point = randomSurfacePoint(rng, inset);
    const normal = normalFromCellPoint(point);
    const cutDot = normalizedCutDirection ? normal.dot(normalizedCutDirection) : 0;
    const distanceFromCutAxis = Math.sqrt(Math.max(0, 1 - cutDot * cutDot));
    const hiddenInCutWindow = distanceFromCutAxis < MEMBRANE_CUT_RADIUS - MEMBRANE_CUT_RIM_WIDTH;
    tempObject.position.copy(point);
    tempObject.quaternion.setFromUnitVectors(yAxis, normal);
    const width = randomRange(rng, 0.7, 1.35);
    tempObject.scale.set(width, randomRange(rng, 0.75, 1.55), width);
    tempObject.updateMatrix();
    const visibleMatrix = tempObject.matrix.clone();
    tempObject.scale.setScalar(0.0001);
    tempObject.updateMatrix();
    const hiddenMatrix = tempObject.matrix.clone();
    if (hiddenInCutWindow) {
      mesh.setMatrixAt(index, hiddenMatrix);
    } else {
      mesh.setMatrixAt(index, visibleMatrix);
    }
    transforms.push({ normal: normal.clone(), visibleMatrix, hiddenMatrix });
    const base = new THREE.Color(palette[index % palette.length]);
    base.offsetHSL(randomRange(rng, -0.025, 0.025), randomRange(rng, -0.08, 0.06), randomRange(rng, -0.04, 0.04));
    colors.push(base);
    mesh.setColorAt(index, base);
  }

  return transforms;
}

function estimateSceneStats(root: THREE.Object3D): RenderStats {
  let drawCalls = 0;
  let triangles = 0;

  root.traverse((object) => {
    const renderable = object as THREE.Object3D & {
      count?: number;
      geometry?: THREE.BufferGeometry;
      isInstancedMesh?: boolean;
      isLine?: boolean;
      isLineSegments?: boolean;
      isMesh?: boolean;
      isPoints?: boolean;
      material?: THREE.Material | THREE.Material[];
    };

    if (!renderable.geometry || !renderable.material || (!renderable.isMesh && !renderable.isPoints && !renderable.isLine && !renderable.isLineSegments)) {
      return;
    }

    const materialCount = Array.isArray(renderable.material) ? renderable.material.length : 1;
    drawCalls += materialCount;

    if (!renderable.isMesh) {
      return;
    }

    const geometry = renderable.geometry;
    const triangleCount = geometry.index
      ? geometry.index.count / 3
      : (geometry.attributes.position as THREE.BufferAttribute | undefined)?.count
        ? ((geometry.attributes.position as THREE.BufferAttribute).count / 3)
        : 0;
    triangles += Math.round(triangleCount * (renderable.isInstancedMesh ? renderable.count ?? 1 : 1));
  });

  return { drawCalls, triangles };
}

function createMaterials(cutDirection: THREE.Vector3): OrganelleMaterials {
  const membrane = new THREE.ShaderMaterial({
    uniforms: {
      uTime: { value: 0 },
      uHighlight: { value: 0 },
      uSelect: { value: 0 },
      uDim: { value: 0 },
      uCutDirection: { value: cutDirection.clone().normalize() },
      uSurfaceVisibility: { value: SURFACE_VISUALS.overview.membraneAlpha },
    },
    vertexShader: `
      uniform float uTime;
      varying vec3 vNormal;
      varying vec3 vWorld;
      varying vec3 vLocal;
      varying float vRuffle;

      void main() {
        vNormal = normalize(normalMatrix * normal);
        vLocal = position;
        vec3 displaced = position;
        float fine =
          sin(position.x * 8.2 + position.y * 3.1 + uTime * 0.45) * 0.018 +
          sin(position.z * 9.7 - position.x * 2.4 - uTime * 0.32) * 0.016;
        float ruffle =
          sin(position.y * 2.1 + uTime * 0.7) * 0.045 +
          sin(position.x * 1.3 - uTime * 0.45) * 0.035 +
          sin((position.z + position.x) * 1.4 + uTime * 0.55) * 0.025 +
          fine;
        float pulse =
          ruffle +
          sin(position.x * 17.0 + position.z * 11.0 + uTime * 0.18) * 0.006;
        vRuffle = ruffle;
        displaced += normal * pulse;
        vec4 world = modelMatrix * vec4(displaced, 1.0);
        vWorld = world.xyz;
        gl_Position = projectionMatrix * viewMatrix * world;
      }
    `,
    fragmentShader: `
      uniform float uHighlight;
      uniform float uSelect;
      uniform float uDim;
      uniform vec3 uCutDirection;
      uniform float uSurfaceVisibility;
      varying vec3 vNormal;
      varying vec3 vWorld;
      varying vec3 vLocal;
      varying float vRuffle;

      void main() {
        float cutDot = dot(normalize(vWorld), normalize(uCutDirection));
        float distanceFromCutAxis = sqrt(max(0.0, 1.0 - cutDot * cutDot));
        float cutRadius = ${MEMBRANE_CUT_RADIUS.toFixed(3)};
        float rimWidth = ${MEMBRANE_CUT_RIM_WIDTH.toFixed(3)};
        bool inCutTunnel = distanceFromCutAxis < cutRadius;
        bool rimBand = abs(distanceFromCutAxis - cutRadius) < rimWidth;
        bool frontRim = cutDot > 0.0 && rimBand;

        if ((inCutTunnel || rimBand) && !frontRim) {
          discard;
        }

        vec3 base = vec3(0.76, 0.63, 0.59);
        float alpha = uSurfaceVisibility;

        if (frontRim) {
          base = mix(vec3(0.93, 0.72, 0.58), vec3(1.0, 0.82, 0.50), uSelect);
          alpha = mix(uSurfaceVisibility * 2.4, 0.74, uSelect);
        } else if (uSelect > 0.5) {
          alpha = max(alpha, 0.2);
        }

        alpha *= mix(1.0, 0.85, uDim);
        gl_FragColor = vec4(base, alpha);
      }
    `,
    transparent: true,
    depthWrite: false,
    side: THREE.DoubleSide,
  });

  return {
    membrane,
    cytoplasm: new THREE.PointsMaterial({
      color: '#b9cec6',
      size: SURFACE_VISUALS.overview.cytoplasmSize,
      transparent: true,
      opacity: SURFACE_VISUALS.overview.cytoplasmOpacity,
      depthWrite: false,
      vertexColors: true,
    }),
    nucleusEnvelope: new THREE.MeshPhysicalMaterial({
      color: '#8f789f',
      emissive: '#19101f',
      roughness: 0.58,
      transmission: 0.28,
      thickness: 0.65,
      transparent: true,
      opacity: 0.5,
      metalness: 0,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
    nucleolus: new THREE.MeshPhysicalMaterial({
      color: '#b65a74',
      emissive: '#301018',
      roughness: 0.68,
      clearcoat: 0.18,
    }),
    chromatin: new THREE.LineBasicMaterial({
      color: '#c9afce',
      transparent: true,
      opacity: 0.5,
    }),
    er: new THREE.MeshPhysicalMaterial({
      color: '#5faaa0',
      emissive: '#062523',
      transparent: true,
      opacity: 0.68,
      roughness: 0.7,
      side: THREE.DoubleSide,
      depthWrite: false,
    }),
    smoothEr: new THREE.MeshPhysicalMaterial({
      color: '#6d9f7e',
      emissive: '#0b2619',
      transparent: true,
      opacity: 0.66,
      roughness: 0.58,
    }),
    golgi: new THREE.MeshPhysicalMaterial({
      color: '#c6905e',
      emissive: '#281405',
      transparent: true,
      opacity: 0.78,
      roughness: 0.62,
      side: THREE.DoubleSide,
    }),
    mitochondria: new THREE.MeshPhysicalMaterial({
      color: '#c46a55',
      emissive: '#39120b',
      transparent: true,
      opacity: 0.78,
      roughness: 0.56,
      clearcoat: 0.22,
    }),
    cristae: new THREE.LineBasicMaterial({
      color: '#e7c786',
      transparent: true,
      opacity: 0.72,
    }),
    ribosome: new THREE.MeshStandardMaterial({
      color: '#ded7be',
      emissive: '#17140c',
      roughness: 0.75,
      vertexColors: true,
    }),
    vesicle: new THREE.MeshPhysicalMaterial({
      color: '#8fb4bd',
      emissive: '#10252b',
      transparent: true,
      opacity: 0.62,
      roughness: 0.3,
      transmission: 0.18,
      thickness: 0.22,
    }),
    cytoskeleton: new THREE.MeshStandardMaterial({
      color: '#a0b978',
      emissive: '#151c0f',
      roughness: 0.62,
    }),
    microtubule: new THREE.MeshStandardMaterial({
      color: '#a7bd7b',
      emissive: '#1b2412',
      roughness: 0.58,
    }),
    intermediateFilament: new THREE.LineBasicMaterial({
      color: '#d0c18f',
      transparent: true,
      opacity: 0.5,
    }),
    actin: new THREE.LineBasicMaterial({
      color: '#c99386',
      transparent: true,
      opacity: SURFACE_VISUALS.overview.actinOpacity,
    }),
    centrosome: new THREE.MeshStandardMaterial({
      color: '#d2bc7a',
      emissive: '#2b2410',
      roughness: 0.48,
    }),
    membraneProtein: new THREE.MeshStandardMaterial({
      color: '#e4c6a3',
      emissive: '#24170c',
      emissiveIntensity: SURFACE_VISUALS.overview.membraneProteinEmissive,
      roughness: 0.5,
      transparent: true,
      opacity: SURFACE_VISUALS.overview.membraneProteinOpacity,
      depthWrite: false,
      vertexColors: true,
    }),
    lysosome: new THREE.MeshPhysicalMaterial({
      color: '#b96b76',
      emissive: '#2b1014',
      transparent: true,
      opacity: 0.7,
      roughness: 0.42,
      transmission: 0.12,
      thickness: 0.18,
      vertexColors: true,
    }),
    endosome: new THREE.MeshPhysicalMaterial({
      color: '#7fa6b4',
      emissive: '#0d2027',
      transparent: true,
      opacity: 0.62,
      roughness: 0.36,
      transmission: 0.14,
      thickness: 0.18,
      vertexColors: true,
    }),
    peroxisome: new THREE.MeshPhysicalMaterial({
      color: '#c0bd86',
      emissive: '#24210d',
      transparent: true,
      opacity: 0.72,
      roughness: 0.5,
      vertexColors: true,
    }),
    proteasome: new THREE.MeshStandardMaterial({
      color: '#beb7a0',
      emissive: '#17150f',
      roughness: 0.7,
      vertexColors: true,
    }),
    cargoTrail: new THREE.LineBasicMaterial({
      color: '#d8c48a',
      transparent: true,
      opacity: 0.24,
    }),
    pore: new THREE.MeshStandardMaterial({
      color: '#c6dbe0',
      emissive: '#13363d',
      roughness: 0.42,
    }),
  };
}

function worldAnchor(object: THREE.Object3D) {
  return object.getWorldPosition(new THREE.Vector3());
}

function instanceHit(target: InteractiveTarget, instanceId?: number) {
  if (instanceId === undefined) {
    return undefined;
  }

  return {
    distance: 0,
    point: target.getAnchor(),
    object: target.object,
    instanceId,
  } as THREE.Intersection<THREE.Object3D>;
}

function makeMeshTarget(
  object: THREE.Object3D,
  itemMeta: OrganelleMeta,
  targetId = itemMeta.id,
  groupId = targetId,
): InteractiveTarget {
  const materials: THREE.Material[] = [];
  const baseEmissive = new Map<THREE.Material, THREE.Color>();
  const baseColor = new Map<THREE.Material, THREE.Color>();
  const baseOpacity = new Map<THREE.Material, number>();
  const baseScale = object.scale.clone();
  let selected = false;
  let dimmed = false;

  object.traverse((child) => {
    const renderable = child as THREE.Object3D & {
      isLine?: boolean;
      isMesh?: boolean;
      isPoints?: boolean;
      material?: THREE.Material | THREE.Material[];
    };
    if (!renderable.isMesh && !renderable.isPoints && !renderable.isLine) {
      return;
    }

    if (!renderable.material) {
      return;
    }

    const materialList = Array.isArray(renderable.material) ? renderable.material : [renderable.material];
    materialList.forEach((material) => {
      materials.push(material);
      baseOpacity.set(material, material.opacity);
      if ('emissive' in material) {
        const emissiveMaterial = material as THREE.MeshStandardMaterial;
        baseEmissive.set(material, emissiveMaterial.emissive.clone());
      }
      if ('color' in material) {
        const colorMaterial = material as THREE.Material & { color: THREE.Color };
        baseColor.set(material, colorMaterial.color.clone());
      }
    });
  });

  function updateVisualState() {
    object.scale.copy(baseScale).multiplyScalar(selected ? 1.035 : 1);

    materials.forEach((material) => {
      if ('emissive' in material) {
        const emissiveMaterial = material as THREE.MeshStandardMaterial;
        const base = baseEmissive.get(material) ?? new THREE.Color(0, 0, 0);
        emissiveMaterial.emissive.copy(base);
        if (selected) {
          emissiveMaterial.emissive.lerp(SELECT_COLOR, 0.42);
        }
        emissiveMaterial.emissiveIntensity = selected ? 1.18 : 1;
      }

      if ('color' in material) {
        const colorMaterial = material as THREE.Material & { color: THREE.Color };
        const base = baseColor.get(material) ?? colorMaterial.color;
        if (selected) {
          colorMaterial.color.copy(base).lerp(SELECT_COLOR, 0.22);
        } else if (dimmed) {
          colorMaterial.color.copy(base).multiplyScalar(0.72);
        } else {
          colorMaterial.color.copy(base);
        }
      }

      material.opacity = dimmed && !selected ? (baseOpacity.get(material) ?? material.opacity) * 0.72 : baseOpacity.get(material) ?? material.opacity;
    });
  }

  return {
    targetId,
    groupId,
    object,
    getMeta: () => itemMeta,
    getAnchor: (hit) => hit?.point.clone() ?? worldAnchor(object),
    setHovered: () => undefined,
    setSelected: (nextSelected) => {
      selected = nextSelected;
      updateVisualState();
    },
    setDimmed: (nextDimmed) => {
      dimmed = nextDimmed;
      updateVisualState();
    },
  };
}

function makeShaderTarget(
  object: THREE.Object3D,
  itemMeta: OrganelleMeta,
  material: THREE.ShaderMaterial,
  targetId = itemMeta.id,
  groupId = targetId,
): InteractiveTarget {
  let selected = false;
  let dimmed = false;

  function updateVisualState() {
    material.uniforms.uHighlight.value = 0;
    if (material.uniforms.uSelect) {
      material.uniforms.uSelect.value = selected ? 1 : 0;
    }
    if (material.uniforms.uDim) {
      material.uniforms.uDim.value = dimmed && !selected ? 1 : 0;
    }
  }

  return {
    targetId,
    groupId,
    object,
    getMeta: () => itemMeta,
    getAnchor: (hit) => hit?.point.clone() ?? worldAnchor(object),
    setHovered: () => undefined,
    setSelected: (nextSelected) => {
      selected = nextSelected;
      updateVisualState();
    },
    setDimmed: (nextDimmed) => {
      dimmed = nextDimmed;
      updateVisualState();
    },
  };
}

function makeInstancedTarget(
  mesh: THREE.InstancedMesh,
  itemMeta: OrganelleMeta,
  baseColors: THREE.Color[],
  targetId = itemMeta.id,
  groupId = targetId,
): InteractiveTarget {
  let selectedInstance: number | null = null;
  let selectedAll = false;
  let dimmed = false;

  function updateInstanceColors() {
    for (let index = 0; index < baseColors.length; index += 1) {
      const color = baseColors[index].clone();

      if (dimmed && !selectedAll && selectedInstance !== index) {
        color.multiplyScalar(0.68);
      }

      if (selectedAll || selectedInstance === index) {
        color.lerp(SELECT_COLOR, 0.30);
      }

      mesh.setColorAt(index, color);
    }

    if (mesh.instanceColor) {
      mesh.instanceColor.needsUpdate = true;
    }
  }

  return {
    targetId,
    groupId,
    object: mesh,
    getMeta: () => itemMeta,
    getAnchor: (hit) => {
      if (hit?.instanceId !== undefined) {
        const matrix = new THREE.Matrix4();
        mesh.getMatrixAt(hit.instanceId, matrix);
        return new THREE.Vector3().setFromMatrixPosition(matrix).applyMatrix4(mesh.matrixWorld);
      }

      return worldAnchor(mesh);
    },
    setHovered: () => undefined,
    setSelected: (selected, hit) => {
      selectedAll = selected && hit?.instanceId === undefined;
      selectedInstance = selected && hit?.instanceId !== undefined ? hit.instanceId : null;
      updateInstanceColors();
    },
    setDimmed: (nextDimmed) => {
      dimmed = nextDimmed;
      updateInstanceColors();
    },
  };
}

function createArcGeometry(radius: number, tubeRadius: number, arc = Math.PI * 1.45) {
  return new THREE.TorusGeometry(radius, tubeRadius, 12, 72, arc);
}

function createChromatinCurve(rng: () => number, radius: number) {
  const points: THREE.Vector3[] = [];

  for (let index = 0; index < 26; index += 1) {
    const angle = index * 0.55 + randomRange(rng, -0.2, 0.2);
    const loopRadius = radius * randomRange(rng, 0.2, 0.82);
    const y = randomRange(rng, -radius * 0.42, radius * 0.42);
    points.push(new THREE.Vector3(Math.cos(angle) * loopRadius, y, Math.sin(angle) * loopRadius));
  }

  return new THREE.CatmullRomCurve3(points, false, 'catmullrom', 0.5);
}

function createCellScene(container: HTMLElement): CellSceneController {
  const timer = new THREE.Timer();
  timer.connect(document);
  const rng = makeRng(98231);
  const scene = new THREE.Scene();
  scene.background = new THREE.Color('#05070a');
  scene.fog = new THREE.FogExp2('#05070a', 0.03);

  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 80);
  camera.position.set(7.2, 4.6, 8.8);

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: false,
    powerPreference: 'high-performance',
  });
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.08;
  renderer.info.autoReset = false;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.domElement.className = 'cell-canvas';
  container.append(renderer.domElement);

  const label = document.createElement('div');
  label.className = 'scene-label';
  label.setAttribute('aria-hidden', 'true');
  container.append(label);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.055;
  controls.autoRotate = true;
  controls.autoRotateSpeed = 0.22;
  controls.minDistance = 6.2;
  controls.maxDistance = 15;
  controls.target.set(0, 0, 0);

  const membraneCutDirection = camera.position.clone().sub(controls.target).normalize();
  const materials = createMaterials(membraneCutDirection);
  const root = new THREE.Group();
  root.name = 'cell-root';
  scene.add(root);

  const interactionObjects: THREE.Object3D[] = [];
  const targetByObject = new Map<string, InteractiveTarget>();
  const targetById = new Map<string, InteractiveTarget>();
  const targetsByGroup = new Map<string, InteractiveTarget[]>();
  const allTargets: InteractiveTarget[] = [];
  const disposableObjects: THREE.Object3D[] = [];
  const geometries = new Set<THREE.BufferGeometry>();
  const materialSet = new Set<THREE.Material>();
  const animations: AnimatedTask[] = [];
  const selectableGroups: SelectableGroup[] = [];
  const tempObject = new THREE.Object3D();
  let surfaceVisualMode: SurfaceVisualMode = 'overview';
  let activeSurfaceVisuals = SURFACE_VISUALS.overview;

  function trackGeometry<T extends THREE.BufferGeometry>(geometry: T) {
    geometries.add(geometry);
    return geometry;
  }

  function trackMaterial<T extends THREE.Material>(material: T) {
    materialSet.add(material);
    return material;
  }

  Object.values(materials).forEach(trackMaterial);

  function register(target: InteractiveTarget) {
    interactionObjects.push(target.object);
    allTargets.push(target);
    targetById.set(target.targetId, target);
    const groupTargets = targetsByGroup.get(target.groupId) ?? [];
    groupTargets.push(target);
    targetsByGroup.set(target.groupId, groupTargets);
    target.object.traverse((object) => {
      targetByObject.set(object.uuid, target);
    });
  }

  const ambientLight = new THREE.HemisphereLight('#a8fff4', '#20102d', 1.85);
  const keyLight = new THREE.DirectionalLight('#ffe6c5', 2.8);
  keyLight.position.set(5, 7, 4);
  const fillLight = new THREE.PointLight('#77b7ff', 6.5, 18);
  fillLight.position.set(-4.5, -2.8, 5);
  const rimLight = new THREE.PointLight('#ff6e9d', 5.5, 18);
  rimLight.position.set(5, 2.8, -4.5);
  scene.add(ambientLight, keyLight, fillLight, rimLight);

  const composer = new EffectComposer(renderer);
  composer.addPass(new RenderPass(scene, camera));
  const ssaoPass = new SSAOPass(scene, camera, 1, 1, 24);
  ssaoPass.kernelRadius = 3.6;
  ssaoPass.minDistance = 0.003;
  ssaoPass.maxDistance = 0.085;
  composer.addPass(ssaoPass);
  const bloomPass = new UnrealBloomPass(new THREE.Vector2(1, 1), 0.28, 0.34, 0.82);
  composer.addPass(bloomPass);
  composer.addPass(new OutputPass());

  const membraneGeometry = trackGeometry(new THREE.SphereGeometry(CELL_RADIUS, 96, 64));
  const membrane = new THREE.Mesh(membraneGeometry, materials.membrane);
  membrane.name = 'interactive-cell-membrane';
  membrane.scale.copy(CELL_SCALE);
  root.add(membrane);
  register(makeShaderTarget(membrane, meta.membrane, materials.membrane));
  animations.push((elapsed) => {
    materials.membrane.uniforms.uTime.value = elapsed;
  });

  const actinCortexGeometry = trackGeometry(createSurfaceLineGeometry(rng, 210, 0.945, 0.46));
  const actinCortex = new THREE.LineSegments(actinCortexGeometry, materials.actin);
  actinCortex.name = 'interactive-actin-cortex';
  root.add(actinCortex);
  register(makeMeshTarget(actinCortex, meta.actinCortex, 'actin-cortex', 'actin-cortex'));
  animations.push((elapsed) => {
    actinCortex.rotation.y = Math.sin(elapsed * 0.07) * 0.018;
    materials.actin.opacity =
      activeSurfaceVisuals.actinOpacity + Math.sin(elapsed * 0.9) * activeSurfaceVisuals.actinPulse;
  });

  const membraneProteinCount = 132;
  const membraneProteinGeometry = trackGeometry(new THREE.CapsuleGeometry(0.027, 0.075, 4, 8));
  const membraneProteins = new THREE.InstancedMesh(membraneProteinGeometry, materials.membraneProtein, membraneProteinCount);
  membraneProteins.name = 'interactive-membrane-proteins';
  membraneProteins.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(membraneProteins);
  const membraneProteinColors: THREE.Color[] = [];
  const membraneProteinTransforms = createInstancedSurfaceMesh(
    membraneProteins,
    rng,
    membraneProteinCount,
    tempObject,
    membraneProteinColors,
    ['#d9b78f', '#b9c4a7', '#c9968b', '#ded0ae'],
    1.015,
    membraneCutDirection,
  );

  function updateSurfaceCutDirection() {
    membraneCutDirection.copy(camera.position).sub(controls.target).normalize();
    materials.membrane.uniforms.uCutDirection.value.copy(membraneCutDirection);

    membraneProteinTransforms.forEach((transform, index) => {
      const cutDot = transform.normal.dot(membraneCutDirection);
      const distanceFromCutAxis = Math.sqrt(Math.max(0, 1 - cutDot * cutDot));
      const hiddenInCutWindow = distanceFromCutAxis < MEMBRANE_CUT_RADIUS - MEMBRANE_CUT_RIM_WIDTH;
      membraneProteins.setMatrixAt(index, hiddenInCutWindow ? transform.hiddenMatrix : transform.visibleMatrix);
    });
    membraneProteins.instanceMatrix.needsUpdate = true;
  }

  updateSurfaceCutDirection();

  register(
    makeInstancedTarget(
      membraneProteins,
      meta.membraneProteins,
      membraneProteinColors,
      'membrane-proteins',
      'membrane-proteins',
    ),
  );
  animations.push((elapsed) => {
    membraneProteins.rotation.y = Math.sin(elapsed * 0.045) * 0.01;
  });

  const cytoplasmGeometry = trackGeometry(new THREE.BufferGeometry());
  const cytoplasmCount = 760;
  const cytoplasmPositions = new Float32Array(cytoplasmCount * 3);
  const cytoplasmColors = new Float32Array(cytoplasmCount * 3);
  const cytoplasmSeeds = new Float32Array(cytoplasmCount);
  const crowdingPalette = ['#b9cec6', '#d3c5a8', '#b6a9c6', '#9fb8c0'];

  for (let index = 0; index < cytoplasmCount; index += 1) {
    const point = randomPointInCell(rng, 0.45);
    cytoplasmPositions[index * 3] = point.x;
    cytoplasmPositions[index * 3 + 1] = point.y;
    cytoplasmPositions[index * 3 + 2] = point.z;
    const color = new THREE.Color(crowdingPalette[index % crowdingPalette.length]);
    color.offsetHSL(randomRange(rng, -0.02, 0.02), randomRange(rng, -0.06, 0.04), randomRange(rng, -0.05, 0.04));
    cytoplasmColors[index * 3] = color.r;
    cytoplasmColors[index * 3 + 1] = color.g;
    cytoplasmColors[index * 3 + 2] = color.b;
    cytoplasmSeeds[index] = rng() * Math.PI * 2;
  }

  cytoplasmGeometry.setAttribute('position', new THREE.BufferAttribute(cytoplasmPositions, 3));
  cytoplasmGeometry.setAttribute('color', new THREE.BufferAttribute(cytoplasmColors, 3));
  const cytoplasm = new THREE.Points(cytoplasmGeometry, materials.cytoplasm);
  cytoplasm.name = 'interactive-cytoplasm';
  root.add(cytoplasm);
  register(makeMeshTarget(cytoplasm, meta.cytoplasm));
  animations.push((elapsed, delta) => {
    const position = cytoplasmGeometry.attributes.position as THREE.BufferAttribute;

    for (let index = 0; index < position.count; index += 1) {
      const phase = cytoplasmSeeds[index] + elapsed * 0.24;
      const x = position.getX(index);
      const y = position.getY(index);
      const z = position.getZ(index);
      const swirl = Math.sin(phase + y * 0.7) * delta * 0.16;
      const lift = Math.cos(phase + x * 0.5) * delta * 0.08;
      position.setXYZ(
        index,
        x + (-z * 0.018 + swirl) * delta,
        y + lift,
        z + (x * 0.016 + Math.cos(phase) * 0.05) * delta,
      );

      const normalized =
        position.getX(index) ** 2 / CELL_BOUNDS.x ** 2 +
        position.getY(index) ** 2 / CELL_BOUNDS.y ** 2 +
        position.getZ(index) ** 2 / CELL_BOUNDS.z ** 2;

      if (normalized > 0.88) {
        const reset = randomPointInCell(rng, 0.5);
        position.setXYZ(index, reset.x, reset.y, reset.z);
      }
    }

    position.needsUpdate = true;
  });

  function setSurfaceVisualMode(mode: SurfaceVisualMode) {
    surfaceVisualMode = mode;
    activeSurfaceVisuals = SURFACE_VISUALS[surfaceVisualMode];
    renderer.domElement.dataset.surfaceMode = surfaceVisualMode;

    materials.membrane.uniforms.uSurfaceVisibility.value = activeSurfaceVisuals.membraneAlpha;
    materials.cytoplasm.opacity = activeSurfaceVisuals.cytoplasmOpacity;
    materials.cytoplasm.size = activeSurfaceVisuals.cytoplasmSize;
    materials.actin.opacity = activeSurfaceVisuals.actinOpacity;
    materials.membraneProtein.opacity = activeSurfaceVisuals.membraneProteinOpacity;
    materials.membraneProtein.emissiveIntensity = activeSurfaceVisuals.membraneProteinEmissive;
  }

  setSurfaceVisualMode('overview');

  const nucleus = new THREE.Group();
  nucleus.name = 'interactive-nucleus';
  nucleus.position.set(-0.8, 0.1, 0.1);
  root.add(nucleus);

  const nucleusOuterGeometry = trackGeometry(new THREE.SphereGeometry(1.12, 64, 42));
  const nucleusOuter = new THREE.Mesh(nucleusOuterGeometry, materials.nucleusEnvelope);
  nucleusOuter.scale.set(1.1, 0.94, 1.02);
  nucleus.add(nucleusOuter);

  const nucleusInnerGeometry = trackGeometry(new THREE.SphereGeometry(0.94, 48, 32));
  const nucleusInner = new THREE.Mesh(nucleusInnerGeometry, materials.nucleusEnvelope.clone());
  trackMaterial(nucleusInner.material as THREE.Material);
  (nucleusInner.material as THREE.MeshPhysicalMaterial).opacity = 0.26;
  (nucleusInner.material as THREE.MeshPhysicalMaterial).color.set('#6d5c77');
  nucleusInner.scale.set(1.03, 0.9, 0.96);
  nucleus.add(nucleusInner);

  const envelopeGapGeometry = trackGeometry(new THREE.SphereGeometry(1.02, 48, 32));
  const envelopeGapMaterial = trackMaterial(
    new THREE.MeshBasicMaterial({
      color: '#ead8df',
      transparent: true,
      opacity: 0.08,
      wireframe: true,
      depthWrite: false,
    }),
  );
  const envelopeGap = new THREE.Mesh(envelopeGapGeometry, envelopeGapMaterial);
  envelopeGap.scale.set(1.08, 0.93, 1.0);
  nucleus.add(envelopeGap);

  const nucleolusGeometry = trackGeometry(new THREE.SphereGeometry(0.31, 36, 24));
  const nucleolus = new THREE.Mesh(nucleolusGeometry, materials.nucleolus);
  nucleolus.position.set(-0.2, 0.12, 0.25);
  nucleolus.scale.set(1.12, 0.86, 1.0);
  nucleus.add(nucleolus);

  const chromatinGroup = new THREE.Group();
  nucleus.add(chromatinGroup);

  for (let index = 0; index < 12; index += 1) {
    const curve = createChromatinCurve(rng, 0.85);
    const points = curve.getPoints(90);
    const geometry = trackGeometry(new THREE.BufferGeometry().setFromPoints(points));
    const territoryMaterial =
      index % 3 === 0
        ? materials.chromatin.clone()
        : new THREE.LineBasicMaterial({
            color: index % 3 === 1 ? '#b7a7c8' : '#d1b8b6',
            transparent: true,
            opacity: index % 3 === 1 ? 0.38 : 0.32,
          });
    if (territoryMaterial !== materials.chromatin) {
      trackMaterial(territoryMaterial);
    }
    const line = new THREE.Line(geometry, territoryMaterial);
    chromatinGroup.add(line);
  }

  register(makeMeshTarget(nucleus, meta.nucleus));
  animations.push((elapsed) => {
    nucleus.rotation.y = Math.sin(elapsed * 0.14) * 0.08;
    nucleolus.scale.setScalar(1 + Math.sin(elapsed * 1.6) * 0.035);
    chromatinGroup.rotation.y = elapsed * 0.025;
  });

  const nuclearPoreCount = 72;
  const poreGeometry = trackGeometry(new THREE.TorusGeometry(0.046, 0.007, 8, 18));
  const poreMesh = new THREE.InstancedMesh(poreGeometry, materials.pore, nuclearPoreCount);
  poreMesh.name = 'interactive-nuclear-pores';
  poreMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  nucleus.add(poreMesh);
  const poreColors = Array.from({ length: nuclearPoreCount }, () => new THREE.Color('#c6dbe0'));

  for (let index = 0; index < nuclearPoreCount; index += 1) {
    const fraction = (index + 0.5) / nuclearPoreCount;
    const phi = Math.acos(1 - 2 * fraction);
    const theta = index * 2.399963229728653 + randomRange(rng, -0.08, 0.08);
    const point = new THREE.Vector3(
      Math.sin(phi) * Math.cos(theta) * 1.15,
      Math.cos(phi) * 0.98,
      Math.sin(phi) * Math.sin(theta) * 1.08,
    );
    tempObject.position.copy(point);
    tempObject.quaternion.setFromUnitVectors(new THREE.Vector3(0, 0, 1), point.clone().normalize());
    tempObject.scale.setScalar(randomRange(rng, 0.85, 1.18));
    tempObject.updateMatrix();
    poreMesh.setMatrixAt(index, tempObject.matrix);
    poreMesh.setColorAt(index, poreColors[index]);
  }

  register(makeInstancedTarget(poreMesh, meta.nuclearPores, poreColors, 'nuclear-pores', 'nuclear-pores'));

  const roughEr = new THREE.Group();
  roughEr.name = 'interactive-rough-er';
  root.add(roughEr);
  const roughErSurfaces: THREE.Mesh[] = [];

  for (let index = 0; index < 7; index += 1) {
    const sheet = new THREE.Mesh(
      trackGeometry(createFoldedSheetGeometry(2.25 + index * 0.16, 0.58 + (index % 2) * 0.14, index * 0.67)),
      materials.er,
    );
    sheet.position.set(-0.68 + index * 0.13, -0.52 + index * 0.17, -0.45 + index * 0.13);
    sheet.rotation.set(0.68 + index * 0.04, 0.38 + index * 0.33, -0.42 + index * 0.16);
    sheet.scale.set(1, 0.72, 1);
    roughErSurfaces.push(sheet);
    roughEr.add(sheet);
  }

  for (let index = 0; index < 5; index += 1) {
    const angle = -1.2 + index * 0.52;
    const start = new THREE.Vector3(
      -0.8 + Math.cos(angle) * 0.95,
      0.1 + randomRange(rng, -0.22, 0.22),
      0.1 + Math.sin(angle) * 0.82,
    );
    const end = new THREE.Vector3(-0.55 + index * 0.2, -0.44 + index * 0.2, -0.45 + index * 0.15);
    const curve = new THREE.CatmullRomCurve3([start, start.clone().lerp(end, 0.55).add(new THREE.Vector3(0.1, -0.12, 0.08)), end]);
    const connector = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(curve, 42, 0.038, 8, false)), materials.er);
    roughEr.add(connector);
  }

  register(makeMeshTarget(roughEr, meta.roughEr, 'rough-er', 'er'));
  animations.push((elapsed) => {
    roughErSurfaces.forEach((sheet, index) => {
      sheet.rotation.z += Math.sin(elapsed * 0.3 + index) * 0.0008;
      sheet.position.y += Math.sin(elapsed * 0.8 + index) * 0.0007;
    });
  });

  const smoothEr = new THREE.Group();
  smoothEr.name = 'interactive-smooth-er';
  root.add(smoothEr);

  for (let index = 0; index < 7; index += 1) {
    const points = [
      new THREE.Vector3(randomRange(rng, -0.3, 0.7), randomRange(rng, -1.0, 1.0), randomRange(rng, -0.6, 0.9)),
      new THREE.Vector3(randomRange(rng, 0.4, 1.7), randomRange(rng, -1.4, 1.5), randomRange(rng, -1.6, 1.5)),
      new THREE.Vector3(randomRange(rng, 1.1, 2.8), randomRange(rng, -1.7, 1.8), randomRange(rng, -2.2, 1.9)),
      new THREE.Vector3(randomRange(rng, 2.0, 3.8), randomRange(rng, -1.9, 1.8), randomRange(rng, -2.5, 2.4)),
    ];
    const curve = new THREE.CatmullRomCurve3(points);
    const tube = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(curve, 72, 0.045, 8, false)), materials.smoothEr);
    smoothEr.add(tube);
  }

  register(makeMeshTarget(smoothEr, meta.smoothEr, 'smooth-er', 'er'));
  animations.push((elapsed) => {
    smoothEr.rotation.y = Math.sin(elapsed * 0.13) * 0.035;
    smoothEr.rotation.z = Math.cos(elapsed * 0.18) * 0.025;
  });

  const golgi = new THREE.Group();
  golgi.name = 'interactive-golgi';
  golgi.position.set(1.1, -0.12, -0.55);
  golgi.rotation.set(0.18, -0.52, 0.22);
  root.add(golgi);

  for (let index = 0; index < 6; index += 1) {
    const arc = new THREE.Mesh(trackGeometry(createArcGeometry(0.72 + index * 0.03, 0.045, Math.PI * 1.38)), materials.golgi);
    arc.position.set(index * 0.05, -0.34 + index * 0.13, index * 0.028);
    arc.rotation.set(Math.PI * 0.5, 0, -0.45 + index * 0.06);
    arc.scale.set(1.0, 0.42, 0.72);
    golgi.add(arc);
  }

  const cisMaterial = materials.endosome.clone();
  cisMaterial.color.set('#83a8ad');
  cisMaterial.opacity = 0.58;
  trackMaterial(cisMaterial);
  const transMaterial = materials.lysosome.clone();
  transMaterial.color.set('#c49476');
  transMaterial.opacity = 0.64;
  trackMaterial(transMaterial);
  const golgiVesicleGeometry = trackGeometry(new THREE.SphereGeometry(0.075, 14, 10));
  for (let index = 0; index < 5; index += 1) {
    const incoming = new THREE.Mesh(golgiVesicleGeometry, cisMaterial);
    incoming.position.set(-0.62 + index * 0.09, -0.28 + index * 0.05, -0.28 + Math.sin(index) * 0.08);
    incoming.scale.setScalar(0.82 + index * 0.04);
    golgi.add(incoming);

    const outgoing = new THREE.Mesh(golgiVesicleGeometry, transMaterial);
    outgoing.position.set(0.62 + index * 0.1, 0.28 + index * 0.04, 0.18 + Math.cos(index) * 0.08);
    outgoing.scale.setScalar(0.86 + index * 0.06);
    golgi.add(outgoing);
  }

  for (let index = 0; index < 4; index += 1) {
    const route = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-1.2, -0.34 + index * 0.08, -0.42),
      new THREE.Vector3(-0.45, -0.12 + index * 0.06, -0.2),
      new THREE.Vector3(0.46, 0.06 + index * 0.05, 0.05),
      new THREE.Vector3(1.0, 0.28 + index * 0.04, 0.24),
    ]);
    const line = new THREE.Line(trackGeometry(new THREE.BufferGeometry().setFromPoints(route.getPoints(44))), materials.cargoTrail);
    golgi.add(line);
  }

  register(makeMeshTarget(golgi, meta.golgi));
  animations.push((elapsed) => {
    golgi.rotation.y = -0.52 + Math.sin(elapsed * 0.25) * 0.045;
  });

  const mitochondria: THREE.Group[] = [];

  for (let index = 0; index < 12; index += 1) {
    const length = randomRange(rng, 0.78, 1.26);
    const radius = randomRange(rng, 0.17, 0.25);
    const phase = rng() * Math.PI * 2;
    const group = new THREE.Group();
    group.name = `interactive-mitochondrion-${index + 1}`;
    group.position.copy(randomPointInCell(rng, 0.75));
    group.rotation.set(rng() * Math.PI, rng() * Math.PI, rng() * Math.PI);
    group.scale.setScalar(randomRange(rng, 0.78, 1.22));

    const bodyMaterial = materials.mitochondria.clone();
    bodyMaterial.color.offsetHSL(randomRange(rng, -0.025, 0.025), randomRange(rng, -0.08, 0.05), randomRange(rng, -0.05, 0.05));
    trackMaterial(bodyMaterial);
    const body = new THREE.Mesh(trackGeometry(createMitochondrionGeometry(length, radius, phase)), bodyMaterial);
    group.add(body);

    if (index % 4 === 0) {
      const branchMaterial = bodyMaterial.clone();
      branchMaterial.opacity = 0.58;
      trackMaterial(branchMaterial);
      const branch = new THREE.Mesh(trackGeometry(createMitochondrionGeometry(length * 0.45, radius * 0.78, phase + 1.7)), branchMaterial);
      branch.position.set(length * 0.15, radius * 0.72, 0);
      branch.rotation.z = randomRange(rng, 0.5, 0.9);
      group.add(branch);
    }

    for (let curveIndex = 0; curveIndex < 4; curveIndex += 1) {
      const cristaeMaterial = materials.cristae.clone();
      cristaeMaterial.opacity = 0.5 + rng() * 0.2;
      trackMaterial(cristaeMaterial);
      const line = new THREE.Line(trackGeometry(createCristaeGeometry(length, radius, phase, curveIndex)), cristaeMaterial);
      group.add(line);
    }

    root.add(group);
    mitochondria.push(group);
    register(
      makeMeshTarget(
        group,
        { ...meta.mitochondria, id: `mitochondrion-${index + 1}`, name: `Mitochondrion ${index + 1}` },
        `mitochondrion-${index + 1}`,
        'mitochondria',
      ),
    );
  }

  animations.push((elapsed) => {
    mitochondria.forEach((group, index) => {
      group.rotation.y += 0.002 + index * 0.00004;
      group.position.y += Math.sin(elapsed * 0.75 + index) * 0.0011;
      const pulse = 1 + Math.sin(elapsed * 1.15 + index * 0.7) * 0.035;
      group.scale.setScalar((0.78 + (index % 5) * 0.08) * pulse);
    });
  });

  const ribosomeGeometry = trackGeometry(new THREE.IcosahedronGeometry(0.047, 1));
  const ribosomeCount = 320;
  const ribosomeMesh = new THREE.InstancedMesh(ribosomeGeometry, materials.ribosome, ribosomeCount);
  ribosomeMesh.name = 'interactive-ribosomes';
  ribosomeMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(ribosomeMesh);
  const ribosomeColors = Array.from({ length: ribosomeCount }, (_, index) =>
    new THREE.Color(index < 155 ? '#fff4cb' : '#d8f7ff'),
  );

  for (let index = 0; index < ribosomeCount; index += 1) {
    let point: THREE.Vector3;

    if (index < 155) {
      const sheet = roughErSurfaces[index % roughErSurfaces.length];
      sheet.updateWorldMatrix(true, false);
      point = new THREE.Vector3(
        randomRange(rng, -1.1, 1.1),
        randomRange(rng, -0.36, 0.36),
        0.055 + randomRange(rng, -0.015, 0.025),
      ).applyMatrix4(sheet.matrixWorld);
    } else {
      point = randomPointInCell(rng, 0.8);
    }

    tempObject.position.copy(point);
    tempObject.rotation.set(rng() * Math.PI, rng() * Math.PI, rng() * Math.PI);
    const ribosomeScale = index < 155 ? randomRange(rng, 0.9, 1.16) : randomRange(rng, 0.72, 1.05);
    tempObject.scale.set(ribosomeScale * randomRange(rng, 0.82, 1.15), ribosomeScale * randomRange(rng, 0.82, 1.14), ribosomeScale);
    tempObject.updateMatrix();
    ribosomeMesh.setMatrixAt(index, tempObject.matrix);
    ribosomeMesh.setColorAt(index, ribosomeColors[index]);
  }

  register(makeInstancedTarget(ribosomeMesh, meta.ribosome, ribosomeColors, 'ribosome', 'ribosome'));

  const proteasomeCount = 110;
  const proteasomeGeometry = trackGeometry(new THREE.CylinderGeometry(0.024, 0.024, 0.085, 10, 1));
  const proteasomeMesh = new THREE.InstancedMesh(proteasomeGeometry, materials.proteasome, proteasomeCount);
  proteasomeMesh.name = 'interactive-proteasomes';
  proteasomeMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
  root.add(proteasomeMesh);
  const proteasomeColors = Array.from({ length: proteasomeCount }, () => {
    const color = new THREE.Color('#beb7a0');
    color.offsetHSL(randomRange(rng, -0.015, 0.015), randomRange(rng, -0.04, 0.04), randomRange(rng, -0.035, 0.035));
    return color;
  });

  for (let index = 0; index < proteasomeCount; index += 1) {
    const point = randomPointInCell(rng, 0.9);
    tempObject.position.copy(point);
    tempObject.rotation.set(rng() * Math.PI, rng() * Math.PI, rng() * Math.PI);
    const scale = randomRange(rng, 0.72, 1.18);
    tempObject.scale.set(scale, scale * randomRange(rng, 0.8, 1.35), scale);
    tempObject.updateMatrix();
    proteasomeMesh.setMatrixAt(index, tempObject.matrix);
    proteasomeMesh.setColorAt(index, proteasomeColors[index]);
  }

  register(makeInstancedTarget(proteasomeMesh, meta.proteasome, proteasomeColors, 'proteasomes', 'proteasomes'));
  animations.push((elapsed) => {
    proteasomeMesh.rotation.y = Math.sin(elapsed * 0.06) * 0.012;
  });

  const vesicleGeometry = trackGeometry(new THREE.SphereGeometry(0.13, 24, 18));
  const vesicles: Array<{ mesh: THREE.Mesh; curve: THREE.CatmullRomCurve3; speed: number; offset: number }> = [];

  for (let index = 0; index < 18; index += 1) {
    const start = new THREE.Vector3(randomRange(rng, 0.0, 1.4), randomRange(rng, -0.5, 0.7), randomRange(rng, -0.8, 0.3));
    const middle = new THREE.Vector3(randomRange(rng, 0.9, 2.2), randomRange(rng, -1.0, 1.0), randomRange(rng, -1.2, 0.8));
    const end = randomPointInCell(rng, 0.8).normalize().multiply(new THREE.Vector3(4.2, 2.9, 3.5));
    const curve = new THREE.CatmullRomCurve3([start, middle, end, middle.clone().multiplyScalar(0.7)], true);
    const vesicleMaterial = materials.vesicle.clone();
    trackMaterial(vesicleMaterial);
    const mesh = new THREE.Mesh(vesicleGeometry, vesicleMaterial);
    mesh.name = `interactive-vesicle-${index + 1}`;
    mesh.scale.setScalar(randomRange(rng, 0.75, 1.35));
    root.add(mesh);
    vesicles.push({ mesh, curve, speed: randomRange(rng, 0.025, 0.052), offset: rng() });
    register(
      makeMeshTarget(
        mesh,
        { ...meta.vesicle, id: `vesicle-${index + 1}`, name: `Transport vesicle ${index + 1}` },
        `vesicle-${index + 1}`,
        'vesicles',
      ),
    );
  }

  animations.push((elapsed) => {
    vesicles.forEach(({ mesh, curve, speed, offset }, index) => {
      const point = curve.getPoint((offset + elapsed * speed) % 1);
      mesh.position.copy(point);
      mesh.scale.setScalar((0.85 + (index % 4) * 0.08) * (1 + Math.sin(elapsed * 2 + index) * 0.05));
    });
  });

  const vesicularOrganellePools: Array<{
    id: 'lysosomes' | 'endosomes' | 'peroxisomes';
    mesh: THREE.InstancedMesh;
    paths: Array<{ curve: THREE.CatmullRomCurve3; speed: number; offset: number; baseScale: THREE.Vector3 }>;
    count: number;
  }> = [];

  function createVesicularOrganellePool(
    id: 'lysosomes' | 'endosomes' | 'peroxisomes',
    itemMeta: OrganelleMeta,
    material: THREE.MeshPhysicalMaterial | THREE.MeshStandardMaterial,
    count: number,
    radius: number,
    palette: string[],
    routeKind: 'endo' | 'lysosomal' | 'metabolic',
  ) {
    const geometry = trackGeometry(new THREE.IcosahedronGeometry(radius, 2));
    const mesh = new THREE.InstancedMesh(geometry, material, count);
    mesh.name = `interactive-${id}`;
    mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    root.add(mesh);
    const colors: THREE.Color[] = [];
    const paths: Array<{ curve: THREE.CatmullRomCurve3; speed: number; offset: number; baseScale: THREE.Vector3 }> = [];

    for (let index = 0; index < count; index += 1) {
      const nearGolgi = new THREE.Vector3(randomRange(rng, 0.4, 1.9), randomRange(rng, -0.9, 0.8), randomRange(rng, -1.0, 0.8));
      const membraneSide = randomSurfacePoint(rng, 0.72);
      const metabolic = randomPointInCell(rng, 0.8);
      const curve =
        routeKind === 'endo'
          ? new THREE.CatmullRomCurve3([membraneSide, nearGolgi, randomPointInCell(rng, 1.0), membraneSide.clone().multiplyScalar(0.85)], true)
          : routeKind === 'lysosomal'
            ? new THREE.CatmullRomCurve3([nearGolgi, randomPointInCell(rng, 0.9), randomPointInCell(rng, 0.85), nearGolgi.clone().multiplyScalar(0.7)], true)
            : new THREE.CatmullRomCurve3([metabolic, randomPointInCell(rng, 0.85), randomPointInCell(rng, 0.85), metabolic.clone().multiplyScalar(0.85)], true);
      const color = new THREE.Color(palette[index % palette.length]);
      color.offsetHSL(randomRange(rng, -0.02, 0.02), randomRange(rng, -0.08, 0.05), randomRange(rng, -0.04, 0.04));
      colors.push(color);
      mesh.setColorAt(index, color);
      paths.push({
        curve,
        speed: routeKind === 'metabolic' ? randomRange(rng, 0.012, 0.024) : randomRange(rng, 0.018, 0.038),
        offset: rng(),
        baseScale: new THREE.Vector3(
          randomRange(rng, 0.75, 1.25),
          randomRange(rng, 0.72, 1.18),
          randomRange(rng, 0.78, 1.28),
        ),
      });
    }

    register(makeInstancedTarget(mesh, itemMeta, colors, id, id));
    vesicularOrganellePools.push({ id, mesh, paths, count });
  }

  createVesicularOrganellePool('lysosomes', meta.lysosome, materials.lysosome, 9, 0.115, ['#b96b76', '#a85f6e'], 'lysosomal');
  createVesicularOrganellePool('endosomes', meta.endosome, materials.endosome, 11, 0.105, ['#7fa6b4', '#6c99a6'], 'endo');
  createVesicularOrganellePool('peroxisomes', meta.peroxisome, materials.peroxisome, 8, 0.092, ['#c0bd86', '#b4b27c'], 'metabolic');

  animations.push((elapsed) => {
    vesicularOrganellePools.forEach((pool) => {
      pool.paths.forEach(({ curve, speed, offset, baseScale }, index) => {
        const point = curve.getPoint((offset + elapsed * speed) % 1);
        tempObject.position.copy(point);
        tempObject.rotation.set(elapsed * speed * 1.5 + index, index * 0.7, elapsed * speed + index * 0.3);
        const pulse = 1 + Math.sin(elapsed * (pool.id === 'peroxisomes' ? 0.9 : 1.4) + index) * 0.035;
        tempObject.scale.copy(baseScale).multiplyScalar(pulse);
        tempObject.updateMatrix();
        pool.mesh.setMatrixAt(index, tempObject.matrix);
      });
      pool.mesh.instanceMatrix.needsUpdate = true;
    });
  });

  const cytoskeleton = new THREE.Group();
  cytoskeleton.name = 'interactive-cytoskeleton';
  root.add(cytoskeleton);

  const centrosome = new THREE.Group();
  centrosome.name = 'interactive-centrosome';
  centrosome.position.set(-0.1, 0.58, 0.24);
  root.add(centrosome);
  const centrioleGeometry = trackGeometry(new THREE.CylinderGeometry(0.045, 0.045, 0.34, 10, 1));
  const centrioleA = new THREE.Mesh(centrioleGeometry, materials.centrosome);
  centrioleA.rotation.z = Math.PI * 0.5;
  const centrioleB = new THREE.Mesh(centrioleGeometry, materials.centrosome);
  centrioleB.rotation.x = Math.PI * 0.5;
  centrioleB.rotation.z = Math.PI * 0.1;
  centrioleB.position.set(0.08, 0.04, -0.02);
  centrosome.add(centrioleA, centrioleB);
  register(makeMeshTarget(centrosome, meta.centrosome, 'centrosome', 'centrosome'));

  for (let index = 0; index < 20; index += 1) {
    const start = centrosome.position.clone();
    const end = randomSurfacePoint(rng, randomRange(rng, 0.64, 0.9));
    const middle = start.clone().lerp(end, 0.55);
    middle.x += randomRange(rng, -0.35, 0.35);
    middle.y += randomRange(rng, -0.42, 0.42);
    middle.z += randomRange(rng, -0.35, 0.35);
    const curve = new THREE.CatmullRomCurve3([start, middle, end]);
    const tube = new THREE.Mesh(trackGeometry(new THREE.TubeGeometry(curve, 82, index % 5 === 0 ? 0.024 : 0.016, 6)), materials.microtubule);
    cytoskeleton.add(tube);
  }

  const intermediatePoints: number[] = [];
  for (let index = 0; index < 70; index += 1) {
    const start = randomPointInCell(rng, 0.75);
    const end = start.clone().lerp(randomPointInCell(rng, 0.7), 0.62);
    intermediatePoints.push(start.x, start.y, start.z, end.x, end.y, end.z);
  }
  const intermediateGeometry = trackGeometry(new THREE.BufferGeometry());
  intermediateGeometry.setAttribute('position', new THREE.Float32BufferAttribute(intermediatePoints, 3));
  cytoskeleton.add(new THREE.LineSegments(intermediateGeometry, materials.intermediateFilament));

  register(makeMeshTarget(cytoskeleton, meta.cytoskeleton));
  animations.push((elapsed) => {
    cytoskeleton.rotation.y = Math.sin(elapsed * 0.08) * 0.035;
    centrosome.rotation.y = elapsed * 0.12;
  });

  selectableGroups.push(
    {
      id: 'cell-membrane',
      targetId: 'cell-membrane',
      name: 'Membrane',
      type: meta.membrane.type,
      color: '#c98382',
      count: 1,
    },
    {
      id: 'membrane-proteins',
      targetId: 'membrane-proteins',
      name: 'Membrane proteins',
      type: meta.membraneProteins.type,
      color: '#d9b78f',
      count: membraneProteinCount,
    },
    {
      id: 'actin-cortex',
      targetId: 'actin-cortex',
      name: 'Actin cortex',
      type: meta.actinCortex.type,
      color: '#c99386',
      count: 1,
    },
    {
      id: 'cytoplasm',
      targetId: 'cytoplasm',
      name: 'Cytoplasm',
      type: meta.cytoplasm.type,
      color: '#b9cec6',
      count: 1,
    },
    {
      id: 'proteasomes',
      targetId: 'proteasomes',
      name: 'Proteasomes',
      type: meta.proteasome.type,
      color: '#beb7a0',
      count: proteasomeCount,
    },
    {
      id: 'nucleus',
      targetId: 'nucleus',
      name: 'Nucleus',
      type: meta.nucleus.type,
      color: '#8f789f',
      count: 2,
      children: [
        { id: 'nucleus-shell', targetId: 'nucleus', name: 'Nucleus' },
        {
          id: 'nuclear-pores',
          targetId: 'nuclear-pores',
          name: 'Nuclear pores',
          children: Array.from({ length: nuclearPoreCount }, (_, index) => ({
            id: `nuclear-pore-${index + 1}`,
            targetId: 'nuclear-pores',
            instanceId: index,
            name: `Pore ${index + 1}`,
          })),
        },
      ],
    },
    {
      id: 'er',
      targetId: 'er',
      name: 'ER',
      type: meta.roughEr.type,
      color: '#5faaa0',
      count: 2,
      children: [
        { id: 'rough-er', targetId: 'rough-er', name: 'Rough ER' },
        { id: 'smooth-er', targetId: 'smooth-er', name: 'Smooth ER' },
      ],
    },
    {
      id: 'golgi',
      targetId: 'golgi',
      name: 'Golgi',
      type: meta.golgi.type,
      color: '#c6905e',
      count: 1,
    },
    {
      id: 'mitochondria',
      targetId: 'mitochondria',
      name: 'Mitochondria',
      type: meta.mitochondria.type,
      color: '#c46a55',
      count: mitochondria.length,
      children: mitochondria.map((_, index) => ({
        id: `mitochondrion-${index + 1}`,
        targetId: `mitochondrion-${index + 1}`,
        name: `Mito ${index + 1}`,
      })),
    },
    {
      id: 'ribosome',
      targetId: 'ribosome',
      name: 'Ribosomes',
      type: meta.ribosome.type,
      color: '#ded7be',
      count: ribosomeCount,
      children: Array.from({ length: ribosomeCount }, (_, index) => ({
        id: `ribosome-${index + 1}`,
        targetId: 'ribosome',
        instanceId: index,
        name: `R${index + 1}`,
      })),
    },
    {
      id: 'vesicles',
      targetId: 'vesicles',
      name: 'Vesicles',
      type: meta.vesicle.type,
      color: '#8fb4bd',
      count: vesicles.length,
      children: vesicles.map((_, index) => ({
        id: `vesicle-${index + 1}`,
        targetId: `vesicle-${index + 1}`,
        name: `Vesicle ${index + 1}`,
      })),
    },
    {
      id: 'lysosomes',
      targetId: 'lysosomes',
      name: 'Lysosomes',
      type: meta.lysosome.type,
      color: '#b96b76',
      count: vesicularOrganellePools.find((pool) => pool.id === 'lysosomes')?.count ?? 0,
    },
    {
      id: 'endosomes',
      targetId: 'endosomes',
      name: 'Endosomes',
      type: meta.endosome.type,
      color: '#7fa6b4',
      count: vesicularOrganellePools.find((pool) => pool.id === 'endosomes')?.count ?? 0,
    },
    {
      id: 'peroxisomes',
      targetId: 'peroxisomes',
      name: 'Peroxisomes',
      type: meta.peroxisome.type,
      color: '#c0bd86',
      count: vesicularOrganellePools.find((pool) => pool.id === 'peroxisomes')?.count ?? 0,
    },
    {
      id: 'cytoskeleton',
      targetId: 'cytoskeleton',
      name: 'Cytoskeleton',
      type: meta.cytoskeleton.type,
      color: '#a0b978',
      count: 1,
    },
    {
      id: 'centrosome',
      targetId: 'centrosome',
      name: 'Centrosome',
      type: meta.centrosome.type,
      color: '#d2bc7a',
      count: 1,
    },
  );

  disposableObjects.push(root);
  const sceneStats = estimateSceneStats(root);

  const raycaster = new THREE.Raycaster();
  raycaster.params.Points = { threshold: 0.12 };
  raycaster.params.Line = { threshold: 0.06 };
  const pointer = POINTER_IDLE.clone();
  let pointerDirty = false;
  let hoverState: HoverState | null = null;
  let selectedState: SelectionState | null = null;
  let probeMode = false;
  let lastProbePoint: THREE.Vector2 | null = null;
  let lastProbeIndex = -1;
  let lastCanvasPoint: THREE.Vector2 | null = null;
  let lastCanvasIndex = -1;
  let paused = false;
  let disposed = false;
  let lastStatsAt = 0;

  function setContainerSize() {
    const width = Math.max(container.clientWidth, 1);
    const height = Math.max(container.clientHeight, 1);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
    renderer.setSize(width, height, false);
    composer.setSize(width, height);
    ssaoPass.setSize(width, height);
    bloomPass.setSize(width, height);
  }

  function clearHover() {
    if (hoverState) {
      hoverState.target.setHovered(false, hoverState.hit);
      hoverState = null;
    }
    label.classList.remove('is-visible');
    container.dispatchEvent(new CustomEvent<HoverDetail>('cell-hover', { detail: { meta: null } }));
  }

  function hoverKey(state: HoverState | null) {
    if (!state) {
      return '';
    }

    return `${state.target.object.uuid}:${state.hit.instanceId ?? 'object'}`;
  }

  function selectionKey(targetId: string, instanceId?: number) {
    return `${targetId}:${instanceId ?? 'all'}`;
  }

  function selectionDetailMeta(target: InteractiveTarget, hit?: THREE.Intersection<THREE.Object3D>, fallback?: OrganelleMeta) {
    const base = fallback ?? target.getMeta(hit);

    if (hit?.instanceId === undefined) {
      if (target.targetId === 'ribosome') {
        return {
          ...base,
          id: 'ribosome',
          name: 'Ribosomes',
          description:
            'A population of free and rough-ER-bound translation complexes, shown as enlarged selectable particles throughout the cytosol.',
        };
      }

      if (target.targetId === 'proteasomes') {
        return { ...base, id: 'proteasomes', name: 'Proteasomes' };
      }

      if (target.targetId === 'lysosomes') {
        return { ...base, id: 'lysosomes', name: 'Lysosomes' };
      }

      if (target.targetId === 'endosomes') {
        return { ...base, id: 'endosomes', name: 'Endosomes' };
      }

      if (target.targetId === 'peroxisomes') {
        return { ...base, id: 'peroxisomes', name: 'Peroxisomes' };
      }

      return base;
    }

    if (target.targetId === 'ribosome') {
      return { ...base, id: `ribosome-${hit.instanceId + 1}`, name: `Ribosome ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'nuclear-pores') {
      return { ...base, id: `nuclear-pore-${hit.instanceId + 1}`, name: `Nuclear pore ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'membrane-proteins') {
      return { ...base, id: `membrane-protein-${hit.instanceId + 1}`, name: `Membrane protein ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'proteasomes') {
      return { ...base, id: `proteasome-${hit.instanceId + 1}`, name: `Proteasome ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'lysosomes') {
      return { ...base, id: `lysosome-${hit.instanceId + 1}`, name: `Lysosome ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'endosomes') {
      return { ...base, id: `endosome-${hit.instanceId + 1}`, name: `Endosome ${hit.instanceId + 1}` };
    }

    if (target.targetId === 'peroxisomes') {
      return { ...base, id: `peroxisome-${hit.instanceId + 1}`, name: `Peroxisome ${hit.instanceId + 1}` };
    }

    return base;
  }

  function dispatchSelection(detail: SelectionDetail) {
    container.dispatchEvent(new CustomEvent<SelectionDetail>('cell-selection-change', { detail }));
  }

  function dispatchProbeStack(detail: ProbeStackDetail) {
    container.dispatchEvent(new CustomEvent<ProbeStackDetail>('cell-probe-stack-change', { detail }));
  }

  function clearSelection() {
    if (selectedState) {
      selectedState.refs.forEach(({ target, hit }) => {
        target.setSelected(false, hit);
      });
      selectedState = null;
    }

    allTargets.forEach((target) => {
      target.setDimmed(false);
    });
    setSurfaceVisualMode('overview');
    lastCanvasPoint = null;
    lastCanvasIndex = -1;
    label.classList.remove('is-visible');
    dispatchSelection({ meta: null });
    dispatchProbeStack({ active: probeMode, meta: null, stackIndex: 0, stackSize: 0 });
  }

  function applySelection(refs: TargetRef[], detail: SelectionDetail) {
    if (selectedState) {
      selectedState.refs.forEach(({ target, hit }) => {
        target.setSelected(false, hit);
      });
    }

    clearHover();
    const selectedTargets = new Set(refs.map(({ target }) => target));
    const nextSurfaceMode: SurfaceVisualMode = refs.some(({ target }) => isSurfaceVisualTarget(target))
      ? 'surface-focus'
      : 'interior-focus';
    allTargets.forEach((target) => {
      const keepSurfaceVisible = nextSurfaceMode === 'surface-focus' && isSurfaceVisualTarget(target);
      target.setDimmed(!selectedTargets.has(target) && !keepSurfaceVisible);
    });
    refs.forEach(({ target, hit }) => {
      target.setSelected(true, hit);
    });
    setSurfaceVisualMode(nextSurfaceMode);

    selectedState = { refs, detail };
    dispatchSelection(detail);

    if (detail.stackSize && detail.stackIndex !== undefined) {
      dispatchProbeStack({
        active: probeMode,
        meta: detail.meta,
        stackIndex: detail.stackIndex,
        stackSize: detail.stackSize,
      });
    }
  }

  function resolveSelectionRefs(targetId: string, instanceId?: number) {
    const directTarget = targetById.get(targetId);

    if (directTarget) {
      const hit = instanceHit(directTarget, instanceId);
      return {
        refs: [{ target: directTarget, hit }],
        meta: selectionDetailMeta(directTarget, hit, selectionMeta[targetId as keyof typeof selectionMeta]),
        groupId: directTarget.groupId,
      };
    }

    const groupTargets = targetsByGroup.get(targetId);
    if (groupTargets?.length) {
      return {
        refs: groupTargets.map((target) => ({ target })),
        meta: selectionMeta[targetId as keyof typeof selectionMeta] ?? groupTargets[0].getMeta(),
        groupId: targetId,
      };
    }

    return null;
  }

  function selectTarget(targetId: string, instanceId: number | undefined, source: SelectionSource, stackIndex?: number, stackSize?: number) {
    const resolved = resolveSelectionRefs(targetId, instanceId);
    if (!resolved) {
      return;
    }

    const currentKey = selectedState?.detail.targetId
      ? selectionKey(selectedState.detail.targetId, selectedState.detail.instanceId)
      : '';
    const nextKey = selectionKey(targetId, instanceId);

    if (source === 'rail' && currentKey === nextKey) {
      clearSelection();
      return;
    }

    applySelection(resolved.refs, {
      meta: resolved.meta,
      targetId,
      groupId: resolved.groupId,
      instanceId,
      source,
      stackIndex,
      stackSize,
    });
  }

  function isShellTarget(target: InteractiveTarget) {
    return target.targetId === meta.membrane.id;
  }

  function isContextTarget(target: InteractiveTarget) {
    return target.targetId === meta.cytoplasm.id || target.targetId === meta.actinCortex.id || isShellTarget(target);
  }

  function isSurfaceVisualTarget(target: InteractiveTarget) {
    return (
      target.targetId === meta.membrane.id ||
      target.targetId === meta.cytoplasm.id ||
      target.targetId === meta.actinCortex.id ||
      target.targetId === meta.membraneProteins.id
    );
  }

  function isRimShellHit(target: InteractiveTarget, hit?: THREE.Intersection<THREE.Object3D>) {
    if (!hit || !isShellTarget(target)) {
      return false;
    }

    const surfaceNormal = normalFromCellPoint(hit.point);
    const viewDirection = camera.position.clone().sub(hit.point).normalize();
    return Math.abs(surfaceNormal.dot(viewDirection)) < 0.34;
  }

  function hitPriority(target: InteractiveTarget, hit?: THREE.Intersection<THREE.Object3D>) {
    if (isShellTarget(target)) {
      return isRimShellHit(target, hit) ? 62 : 95;
    }

    if (target.targetId === meta.cytoplasm.id || target.targetId === meta.actinCortex.id) {
      return 88;
    }

    if (
      target.groupId === 'mitochondria' ||
      target.groupId === 'vesicles' ||
      target.groupId === 'lysosomes' ||
      target.groupId === 'endosomes' ||
      target.groupId === 'peroxisomes' ||
      target.targetId === 'golgi' ||
      target.groupId === 'er' ||
      target.targetId === 'centrosome'
    ) {
      return 10;
    }

    if (
      target.targetId === 'ribosome' ||
      target.targetId === 'proteasomes' ||
      target.targetId === 'membrane-proteins' ||
      target.targetId === 'nuclear-pores'
    ) {
      return 18;
    }

    if (target.targetId === 'nucleus') {
      return 22;
    }

    if (target.targetId === 'cytoskeleton') {
      return 38;
    }

    return 30;
  }

  function orderedHitStack() {
    raycaster.setFromCamera(pointer, camera);
    const rawCandidates = raycaster
      .intersectObjects(interactionObjects, true)
      .filter((candidate) => targetByObject.has(candidate.object.uuid));
    const hasSpecificHit = rawCandidates.some((candidate) => {
      const target = targetByObject.get(candidate.object.uuid);
      return target ? !isContextTarget(target) : false;
    });
    const seen = new Set<string>();
    const stack: TargetRef[] = [];

    rawCandidates
      .map((hit) => {
        const target = targetByObject.get(hit.object.uuid);
        return target ? { target, hit, priority: hitPriority(target, hit) } : null;
      })
      .filter((candidate): candidate is { target: InteractiveTarget; hit: THREE.Intersection<THREE.Object3D>; priority: number } => {
        if (!candidate) {
          return false;
        }

        return !(hasSpecificHit && isContextTarget(candidate.target) && !isRimShellHit(candidate.target, candidate.hit));
      })
      .sort((a, b) => a.priority - b.priority || a.hit.distance - b.hit.distance)
      .forEach(({ target, hit }) => {
        const key = selectionKey(target.targetId, hit.instanceId);
        if (seen.has(key)) {
          return;
        }

        seen.add(key);
        stack.push({ target, hit });
      });

    return stack;
  }

  function resolveHit() {
    if (!pointerDirty) {
      return;
    }
    pointerDirty = false;

    if (selectedState) {
      if (hoverState) {
        clearHover();
      }
      return;
    }

    if (pointer.equals(POINTER_IDLE)) {
      clearHover();
      return;
    }

    const hoverRef = orderedHitStack()[0];

    if (!hoverRef?.hit) {
      clearHover();
      return;
    }

    const nextState = { target: hoverRef.target, hit: hoverRef.hit };
    const previousKey = hoverKey(hoverState);
    const nextKey = hoverKey(nextState);

    if (previousKey !== nextKey) {
      if (hoverState) {
        hoverState.target.setHovered(false, hoverState.hit);
      }

      hoverRef.target.setHovered(true, hoverRef.hit);
      container.dispatchEvent(new CustomEvent<HoverDetail>('cell-hover', { detail: { meta: hoverRef.target.getMeta(hoverRef.hit) } }));
    }

    hoverState = nextState;
  }

  function updateLabel() {
    const activeRef = selectedState?.refs[0];
    if (!activeRef || !selectedState?.detail.meta) {
      label.classList.remove('is-visible');
      return;
    }

    const anchor = activeRef.target.getAnchor(activeRef.hit);
    const projected = anchor.clone().project(camera);

    if (projected.z < -1 || projected.z > 1) {
      label.classList.remove('is-visible');
      return;
    }

    const rect = renderer.domElement.getBoundingClientRect();
    const x = (projected.x * 0.5 + 0.5) * rect.width;
    const y = (-projected.y * 0.5 + 0.5) * rect.height;
    const selectedLabel =
      selectedState.detail.stackSize && selectedState.detail.stackIndex !== undefined
        ? `${selectedState.detail.meta.name} ${selectedState.detail.stackIndex + 1} / ${selectedState.detail.stackSize}`
        : selectedState.detail.meta.name;
    label.textContent = selectedLabel;
    label.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px) translate(-50%, -125%)`;
    label.classList.add('is-visible');
  }

  function animate(timestamp = performance.now()) {
    if (disposed) {
      return;
    }

    timer.update(timestamp);
    const delta = Math.min(timer.getDelta(), 0.033);
    const elapsed = timer.getElapsed();

    if (!paused) {
      animations.forEach((animation) => {
        animation(elapsed, delta);
      });
    }

    controls.autoRotate = !paused;
    controls.update();
    updateSurfaceCutDirection();
    resolveHit();
    updateLabel();
    renderer.info.reset();
    composer.render(delta);

    if (elapsed - lastStatsAt > 0.75) {
      lastStatsAt = elapsed;
      container.dispatchEvent(
        new CustomEvent<RenderStats>('cell-render-stats', {
          detail: {
            drawCalls: sceneStats.drawCalls,
            triangles: sceneStats.triangles,
          },
        }),
      );
    }
  }

  function setPointerFromEvent(event: PointerEvent | MouseEvent) {
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  }

  function handlePointerMove(event: PointerEvent) {
    setPointerFromEvent(event);
    pointerDirty = true;
  }

  function handlePointerLeave() {
    pointer.copy(POINTER_IDLE);
    pointerDirty = true;
    lastCanvasPoint = null;
    lastCanvasIndex = -1;
  }

  function handleCanvasClick(event: MouseEvent) {
    setPointerFromEvent(event);
    const stack = orderedHitStack();

    if (stack.length === 0) {
      clearSelection();
      return;
    }

    if (!probeMode) {
      const nearPrevious = lastCanvasPoint ? lastCanvasPoint.distanceTo(pointer) < 0.045 : false;
      lastCanvasIndex = nearPrevious ? (lastCanvasIndex + 1) % stack.length : 0;
      lastCanvasPoint = pointer.clone();

      const clickHit = stack[lastCanvasIndex];
      if (!clickHit) {
        return;
      }
      selectTarget(clickHit.target.targetId, clickHit.hit?.instanceId, 'canvas', lastCanvasIndex, nearPrevious ? stack.length : undefined);
      return;
    }

    const nearPrevious = lastProbePoint ? lastProbePoint.distanceTo(pointer) < 0.045 : false;
    lastProbeIndex = nearPrevious ? (lastProbeIndex + 1) % stack.length : 0;
    lastProbePoint = pointer.clone();

    const probeHit = stack[lastProbeIndex];
    if (!probeHit) {
      return;
    }
    selectTarget(probeHit.target.targetId, probeHit.hit?.instanceId, 'probe', lastProbeIndex, stack.length);
  }

  const resizeObserver = new ResizeObserver(setContainerSize);
  resizeObserver.observe(container);
  renderer.domElement.addEventListener('pointermove', handlePointerMove);
  renderer.domElement.addEventListener('pointerleave', handlePointerLeave);
  renderer.domElement.addEventListener('click', handleCanvasClick);

  setContainerSize();
  renderer.setAnimationLoop(animate);

  return {
    setPaused(nextPaused) {
      paused = nextPaused;
      pointerDirty = true;
    },
    setProbeMode(enabled) {
      probeMode = enabled;
      renderer.domElement.classList.toggle('is-probing', enabled);
      lastProbePoint = null;
      lastProbeIndex = -1;
      dispatchProbeStack({ active: enabled, meta: null, stackIndex: 0, stackSize: 0 });
    },
    selectTarget(targetId, instanceId) {
      selectTarget(targetId, instanceId, 'rail');
    },
    clearSelection,
    getSelectableGroups() {
      return JSON.parse(JSON.stringify(selectableGroups)) as SelectableGroup[];
    },
    dispose() {
      disposed = true;
      renderer.setAnimationLoop(null);
      resizeObserver.disconnect();
      renderer.domElement.removeEventListener('pointermove', handlePointerMove);
      renderer.domElement.removeEventListener('pointerleave', handlePointerLeave);
      renderer.domElement.removeEventListener('click', handleCanvasClick);
      clearHover();
      clearSelection();
      controls.dispose();
      timer.dispose();

      disposableObjects.forEach((object) => {
        object.removeFromParent();
      });
      geometries.forEach((geometry) => geometry.dispose());
      materialSet.forEach((material) => material.dispose());
      composer.dispose();
      renderer.dispose();
      renderer.domElement.remove();
      label.remove();
    },
  };
}

export { createCellScene };
