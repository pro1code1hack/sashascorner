// The café as a model you can turn round. Built from the owner's photos: a long
// room with the counter at the back under two TV menus, the drinks fridge beside
// it, white tables with velvet cushions, the white pillar with the chess table,
// the toilet door, and the front window carrying the logo. The owner's layout
// (2026-09): a wide counter with the toilet door behind it, one bar ledge with
// seven stools running along the window and down the logo wall, one big round
// table in the middle, and two small tables on the counter side of the pillar.
//
// Three objects are doors into the site: the counter (menu), the big table
// (booking) and the A-frame board by the entrance (visit). (Instagram was dropped,
// owner 2026-09-26.) Each has an HTML link pinned over it, so the page works by
// keyboard and for crawlers; the 3D is the enhancement, never the only way in.
//
// Turning: drag, or the two buttons. Whichever walls stand between the camera and
// the room drop to a stub, Sims-style, so the room is always open to view.
//
// People: Sasha at the till and a second barista; regulars sitting at most seats,
// sipping and chatting; and a few customers who come in, queue, order, carry a
// cup to a free seat, stay a while and leave. Short speech bubbles (dialogue.ts)
// play over their heads. Under reduced motion everyone sits still and Sasha says
// hello once.
import * as THREE from 'three';
import { SVGLoader } from 'three/addons/loaders/SVGLoader.js';
import { mergeGeometries } from 'three/addons/utils/BufferGeometryUtils.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import logoSvg from '../../../public/brand/logo.svg?raw';
import line from '../../data/logo-line.json';
import * as T from './textures';
import { C } from './textures';
import {
  aFrame,
  at,
  barStool,
  bigRoundTable,
  chair,
  cup,
  cupStack,
  cyl,
  espressoMachine,
  ficus,
  gameStack,
  dracaena,
  coatStand,
  banister,
  spindleChair,
  pumpkin,
  hangingPlant,
  cakeShowcase,
  laptop,
  cookieJar,
  tipJar,
  flowers,
  counterSign,
  roundTable,
  glass,
  grinder,
  jar,
  mat,
  milkJug,
  pillar,
  placemat,
  rbox,
  shadowed,
  smallPlant,
  squareTable,
  syrups,
  till,
} from './props';
import { CHAIR, STOOL, figure, idle, sit, standStill, walkPose, type Figure, type Look } from './people';
import { Talk, type Speaker } from './dialogue';

export interface Spot {
  key: string;
  href: string;
  external?: boolean;
}
export interface DioramaOptions {
  menuRows: Array<[string, string]>;
  spots: Spot[];
  reducedMotion: boolean;
  onReady?: () => void;
}

const H = 3.2; // wall height
const W2 = 3.5; // half width (x)
const D2 = 4.0; // half depth (z)
const LOGO_VB = { x: 336.66, y: 212.3, w: 683.53, h: 411.55 };
const TAU = Math.PI * 2;

type WallKey = 'back' | 'left' | 'front' | 'right';

export function mountDiorama(host: HTMLElement, opts: DioramaOptions): () => void {
  const canvas = host.querySelector('canvas')!;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
  // 1.5x is indistinguishable from 2x on this flat-shaded model and ~44% fewer pixels.
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping; // keeps brand hues honest
  renderer.toneMappingExposure = 1.02;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  // The sun is fixed in the world and turning moves only the camera, so the shadow
  // map is redrawn only when something casting a shadow moves (see frame()).
  renderer.shadowMap.autoUpdate = false;
  renderer.shadowMap.needsUpdate = true;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.32;

  const root = new THREE.Group();
  scene.add(root);

  // Intro choreography: [object, delay seconds, kind]
  const intro: Array<{ o: THREE.Object3D; d: number; kind: 'drop' | 'rise' }> = [];
  const drop = <O extends THREE.Object3D>(o: O, d: number): O => (intro.push({ o, d, kind: 'drop' }), o);

  // Seats: where the seat is (in its furniture group, so a sitter drops in with
  // the furniture during the intro), which way it faces, its height, and the
  // waypoints from the aisle to the spot beside it -- the room has one aisle (x =
  // AISLE_X) and two lanes, so nobody walks through a table.
  type Seat = {
    key: string;
    parent: THREE.Group;
    local: THREE.Vector3;
    pos: THREE.Vector3; // world
    face: number;
    h: number;
    via: THREE.Vector3[]; // first point is on the aisle; last is beside the seat
    wall?: WallKey; // the wall it is set against, for hiding bubbles on turning
    party: string; // who talks to whom
    taken: boolean;
  };
  const seats: Seat[] = [];
  const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  const AISLE_X = 1.1;
  const LANE_Z = 2.72; // between the big table and the window stools
  const LANE_X = -2.3; // between the big table and the wall stools
  function addSeat(parent: THREE.Group, key: string, x: number, z: number, face: number, h: number, via: Array<[number, number]>, party: string, wall?: WallKey) {
    const pos = v3(parent.position.x + x, 0, parent.position.z + z);
    const pts = via.map(([vx, vz]) => v3(vx, 0, vz));
    pts.unshift(v3(AISLE_X, 0, pts[0].z));
    seats.push({ key, parent, local: v3(x, 0, z), pos, face, h, via: pts, wall, party, taken: false });
  }

  // ---- shell -------------------------------------------------------------
  const cut = mat(C.olive900, 0.9);
  const floorTop = new THREE.MeshStandardMaterial({ map: T.marbleTiles(), roughness: 0.35 });
  const slab = new THREE.Mesh(new THREE.BoxGeometry(2 * W2 + 0.4, 0.3, 2 * D2 + 0.4), [cut, cut, floorTop, cut, cut, cut]);
  slab.position.y = -0.15;
  slab.receiveShadow = true;
  root.add(slab);

  const sageWall = new THREE.MeshStandardMaterial({ map: T.plaster(C.sage), roughness: 0.95 });
  const wainscot = new THREE.MeshStandardMaterial({ map: T.woodPanel(), roughness: 0.7 });
  const darkWood = mat(C.darkWood, 0.6);

  // A wall runs along one axis at a fixed coordinate; `holes` are openings in it.
  // Everything mounted on its inner face goes in `decor`, which hides when the
  // wall drops.
  interface Wall {
    group: THREE.Group;
    decor: THREE.Group;
    out: THREE.Vector2; // outward normal (x, z)
    level: number;
    target: number;
  }
  const walls = {} as Record<WallKey, Wall>;
  function buildWall(key: WallKey, holes: Array<{ a0: number; a1: number; y0: number; y1: number }>) {
    const alongX = key === 'back' || key === 'front';
    const sign = key === 'back' || key === 'left' ? -1 : 1;
    const fixed = sign * ((alongX ? D2 : W2) + 0.1);
    const half = (alongX ? W2 : D2) + 0.2;
    const group = new THREE.Group();
    const decor = new THREE.Group();
    const box = (a0: number, a1: number, y0: number, y1: number, m: THREE.Material, depth = 0.2, inset = 0) => {
      const len = a1 - a0;
      const mid = (a0 + a1) / 2;
      const mesh = new THREE.Mesh(new THREE.BoxGeometry(alongX ? len : depth, y1 - y0, alongX ? depth : len), m);
      const f = fixed - sign * inset;
      mesh.position.set(alongX ? mid : f, (y0 + y1) / 2, alongX ? f : mid);
      return mesh;
    };
    const sorted = [...holes].sort((a, b) => a.a0 - b.a0);
    let a = -half;
    for (const h of sorted) {
      if (h.a0 > a) group.add(box(a, h.a0, 0, H, sageWall));
      if (h.y0 > 0) group.add(box(h.a0, h.a1, 0, h.y0, sageWall));
      group.add(box(h.a0, h.a1, h.y1, H, sageWall));
      a = h.a1;
    }
    if (a < half) group.add(box(a, half, 0, H, sageWall));
    group.add(box(-half, half, H, H + 0.04, cut, 0.22)); // cut top, in olive
    // panelling and dado rail on the inner face, skipping openings that reach the floor
    let p = -half + 0.2;
    for (const h of [...sorted.filter((s) => s.y0 < 0.95), { a0: half - 0.2, a1: half, y0: 0, y1: 0 }]) {
      if (h.a0 > p + 0.05) {
        decor.add(box(p, h.a0, 0, 0.95, wainscot, 0.03, 0.115));
        decor.add(box(p, h.a0, 0.94, 1.0, darkWood, 0.06, 0.12));
      }
      p = h.a1;
    }
    group.add(decor);
    walls[key] = { group, decor, out: new THREE.Vector2(alongX ? 0 : sign, alongX ? sign : 0), level: 1, target: 1 };
    root.add(shadowed(group));
    intro.push({ o: group, d: 0.1 + Object.keys(walls).length * 0.06, kind: 'rise' });
    return { decor };
  }

  const back = buildWall('back', []);
  const left = buildWall('left', []);
  const DOOR = { a0: 1.0, a1: 2.0 };
  const WIN = { a0: -3.1, a1: 0.6, y0: 0.55, y1: 2.7 };
  const front = buildWall('front', [{ ...WIN }, { a0: DOOR.a0, a1: DOOR.a1, y0: 0, y1: 2.4 }]);
  const right = buildWall('right', []);

  // ---- back wall: counter, TVs, fridge, painted tree ---------------------------
  // The counter runs most of the width now: from the right-hand wall to within a
  // doorway's width of the logo wall, so the toilet door sits behind it.
  const zBack = -D2; // inner face
  const counter = new THREE.Group();
  const CX0 = -1.0; // shortened (owner, 2026-09-26): open floor at the toilet end
  const CX1 = 3.25;
  const cx = (CX0 + CX1) / 2;
  const cw = CX1 - CX0;
  const cz = -2.4; // pulled forward: a roomier working aisle behind the bar
  const TOP = 1.03;
  counter.add(at(rbox(cw, 0.98, 0.64, mat('#1f1c19', 0.6), 0.03), cx, 0.49, cz));
  counter.add(at(rbox(cw + 0.02, 0.3, 0.02, mat('#3a3430', 0.25, 0.1), 0.01), cx, 0.82, cz + 0.325));
  const nSlats = Math.floor(cw / 0.08);
  const slats = new THREE.InstancedMesh(new THREE.BoxGeometry(0.035, 0.62, 0.03), mat(C.oak, 0.55), nSlats);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < nSlats; i++) slats.setMatrixAt(i, m4.makeTranslation(CX0 + 0.08 + i * 0.08, 0.35, cz + 0.33));
  counter.add(slats);
  counter.add(at(rbox(cw + 0.1, 0.05, 0.74, mat(C.white, 0.35), 0.015), cx, 1.005, cz + 0.02));
  // Working end, by the machine: grinder, machine, jug, knock-out cups.
  counter.add(at(espressoMachine(), 2.05, TOP, cz - 0.02));
  counter.add(at(grinder(), 2.85, TOP, cz - 0.05));
  counter.add(at(milkJug(), 2.58, TOP, cz + 0.2));
  // Till where Sasha takes orders, with cups stacked ready.
  // (Kept clear of the menu pin's anchor, so the pin never sits on her head.)
  counter.add(at(till(), -0.5, TOP, cz + 0.05, Math.PI));
  counter.add(at(cupStack(C.paper, 8), -0.05, TOP, cz - 0.15));
  counter.add(at(cupStack(C.sage, 6), 0.07, TOP, cz - 0.12));
  counter.add(at(cupStack(C.caramel, 5), 0.01, TOP, cz + 0.0));
  // Cake stand and jars between the till and the machine; the Kyiv cake case
  // at the customer end.
  // Beside the till, kept clear for serving: a tip jar at the customer end and a
  // chalk sign. Past the cups: the glass cake showcase, then cookies and flowers
  // before the machine (owner, 2026-09-26).
  counter.add(at(tipJar(), -0.88, TOP, cz + 0.2));
  counter.add(at(counterSign(T.chalkSign(['Cake of', 'the day', '£3.60'])), 0.22, TOP, cz + 0.24, -0.15));
  counter.add(at(cookieJar(), 1.25, TOP, cz + 0.1));
  counter.add(at(flowers(), 1.5, TOP, cz + 0.18));
  // Acrylic tiered pastry display with chalk tags, between the till and the machine.
  counter.add(at(cakeShowcase(), 0.72, TOP, cz + 0.04));
  // Back bar along the wall: glasses, and a syrup shelf by the fridge.
  counter.add(at(rbox(2.7, 0.92, 0.46, mat('#1f1c19', 0.6), 0.03), 1.2, 0.46, zBack + 0.24));
  counter.add(at(rbox(2.75, 0.05, 0.5, mat(C.white, 0.35), 0.015), 1.2, 0.945, zBack + 0.24));
  for (let i = 0; i < 6; i++) counter.add(at(glass(i % 2 ? C.caramel : '#d9c7a8', 0.12), 0.3 + i * 0.13, 0.97, zBack + 0.3));
  counter.add(at(syrups(['#c96f6a', C.caramel, '#6d5aa8', '#e8d6b5', '#8fa05a', C.coffee]), 1.5, 0.97, zBack + 0.22));
  counter.add(at(rbox(1.0, 0.92, 0.46, mat('#1f1c19', 0.6), 0.03), -1.7, 0.46, zBack + 0.24));
  counter.add(at(rbox(1.04, 0.05, 0.5, mat(C.white, 0.35), 0.015), -1.7, 0.945, zBack + 0.24));
  counter.add(at(syrups(['#c96f6a', '#6d5aa8', C.caramel, '#e0c24a', '#8fa05a', '#5b5fa8', '#e8d6b5', C.coffee, '#c44b3b']), -2.05, 0.97, zBack + 0.2));
  const half = Math.ceil(opts.menuRows.length / 2);
  const screens: Array<[string, Array<[string, string]>, number]> = [
    ['Signatures', opts.menuRows.slice(0, half), -1.0],
    ['Coffee', opts.menuRows.slice(half), 1.6],
  ];
  for (const [title, rows, x] of screens) {
    const tv = new THREE.Group();
    // About 2.1x the old boards (owner, 2026-09-26): they fill the wall over the back bar.
    tv.add(at(rbox(2.54, 1.46, 0.06, mat(C.ink, 0.35, 0.2), 0.02), 0, 0, 0));
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(2.46, 1.38), new THREE.MeshBasicMaterial({ map: T.menuScreen(title, rows), toneMapped: false }));
    scr.position.z = 0.034; // in front of the 6 cm bezel
    tv.add(scr);
    tv.position.set(x, 2.2, zBack + 0.08);
    tv.rotation.x = 0.07;
    counter.add(tv);
  }
  root.add(shadowed(drop(counter, 0.6)));

  // Drinks fridge with the Prices chalkboard on its side.
  const fridge = new THREE.Group();
  fridge.add(at(rbox(0.6, 1.95, 0.6, mat('#141412', 0.4, 0.2), 0.02), 0, 0.975, 0));
  const glassDoor = new THREE.Mesh(new THREE.PlaneGeometry(0.46, 1.5), new THREE.MeshBasicMaterial({ color: '#cfe0e6', toneMapped: false, transparent: true, opacity: 0.55 }));
  glassDoor.position.set(0, 1.0, 0.302);
  fridge.add(glassDoor);
  const blue = new THREE.MeshStandardMaterial({ color: '#bfe3ff', emissive: '#3d8bff', emissiveIntensity: 0.9 });
  fridge.add(at(rbox(0.5, 0.14, 0.02, blue, 0.01), 0, 1.84, 0.305));
  // Seasonal: two paper pumpkin lanterns on top.
  fridge.add(at(pumpkin(0.11), -0.14, 1.95, 0.05, 0.2));
  fridge.add(at(pumpkin(0.12), 0.15, 1.95, 0.0, -0.3));
  const board = new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.96), new THREE.MeshStandardMaterial({ map: T.pricesBoard(), roughness: 0.95 }));
  board.position.set(-0.302, 1.3, 0);
  board.rotation.y = -Math.PI / 2;
  fridge.add(board);
  // On the toilet wall, just past the door towards the back (owner, 2026-09-26).
  root.add(shadowed(drop(at(fridge, -W2 + 0.35, 0, -3.35, Math.PI / 2), 0.55)));

  // (The painted tree is in the counter-side corner of the right wall; see below.)
  void back;

  // ---- left wall: logo mural with the self-drawing wire, frames, toilet door ------
  const xLeft = -W2; // inner face
  const mural = new THREE.Group();
  const S = 2.7 / LOGO_VB.w;
  const svg = new SVGLoader().parse(logoSvg);
  // Path order in logo.svg: sasha, s, corner, cup blob, portafilter blob, line art.
  // Reverse colourway, which is what reads on sage.
  const muralColours = [C.paper, C.caramel, C.paper, C.olive700, C.coffee];
  svg.paths.slice(0, 5).forEach((p, i) => {
    const m = new THREE.Mesh(new THREE.ShapeGeometry(p.toShapes(), 6), new THREE.MeshStandardMaterial({ color: muralColours[i], roughness: 0.9 }));
    m.scale.set(S, -S, 1);
    m.position.set(-LOGO_VB.x * S, LOGO_VB.y * S, 0.001 * (i + 1));
    m.receiveShadow = true;
    mural.add(m);
  });
  // Each traced segment becomes a tube; aT is the arc-time at which the flow
  // reaches that vertex, so one uniform draws the whole line.
  const tubes: THREE.BufferGeometry[] = [];
  for (const e of line.edges as Array<{ t0: number; pts: number[][] }>) {
    const pts = e.pts.map(([x, y], k) => new THREE.Vector3(x * S, -y * S, 0.035 + 0.012 * Math.sin(k * 0.21)));
    if (pts.length < 2) continue;
    const curve = new THREE.CatmullRomCurve3(pts);
    const segs = Math.max(4, Math.round(pts.length * 1.3));
    const g = new THREE.TubeGeometry(curve, segs, 0.0075, 6, false);
    const len = curve.getLength() / S;
    const aT = new Float32Array(g.attributes.position.count);
    for (let i = 0; i <= segs; i++) for (let j = 0; j <= 6; j++) aT[i * 7 + j] = e.t0 + (i / segs) * len;
    g.setAttribute('aT', new THREE.BufferAttribute(aT, 1));
    tubes.push(g);
  }
  const wireUniforms = { uProgress: { value: opts.reducedMotion ? 1e6 : 0 } };
  const wireMat = new THREE.MeshStandardMaterial({ color: C.ink, roughness: 0.4, metalness: 0.2 });
  wireMat.onBeforeCompile = (sh) => {
    sh.uniforms.uProgress = wireUniforms.uProgress;
    sh.vertexShader = sh.vertexShader
      .replace('#include <common>', '#include <common>\nattribute float aT;\nvarying float vT;')
      .replace('#include <begin_vertex>', '#include <begin_vertex>\nvT = aT;');
    sh.fragmentShader = sh.fragmentShader
      .replace('#include <common>', '#include <common>\nuniform float uProgress;\nvarying float vT;')
      .replace('#include <clipping_planes_fragment>', 'if (vT > uProgress) discard;\n#include <clipping_planes_fragment>');
  };
  const wire = new THREE.Mesh(mergeGeometries(tubes), wireMat);
  wire.castShadow = true;
  // Wire shadows respect the draw-on too, or the shadow arrives first.
  const wireDepth = new THREE.MeshDepthMaterial({ depthPacking: THREE.RGBADepthPacking });
  wireDepth.onBeforeCompile = wireMat.onBeforeCompile;
  wire.customDepthMaterial = wireDepth;
  mural.add(wire);
  const lineTotal = (line as { total: number }).total;
  // On the left wall, facing into the room (+x), running from z = 2.5 back to -0.2.
  mural.rotation.y = Math.PI / 2;
  mural.position.set(xLeft + 0.012, 1.3 + LOGO_VB.h * S, 2.5);
  left.decor.add(mural);

  // Toilet door, back of the left wall, behind the counter.
  const loo = new THREE.Group();
  const LOO_Z = -1.08;
  loo.add(at(rbox(0.08, 2.25, 0.95, darkWood, 0.02), xLeft + 0.03, 1.125, LOO_Z));
  // Open doorway onto the corridor painted in green and white triangles.
  const corridor = new THREE.Mesh(new THREE.PlaneGeometry(0.8, 2.1), new THREE.MeshStandardMaterial({ map: T.geoDoorway(), roughness: 0.9 }));
  corridor.position.set(xLeft + 0.075, 1.06, LOO_Z);
  corridor.rotation.y = Math.PI / 2;
  loo.add(corridor);
  const looSign = new THREE.Mesh(new THREE.PlaneGeometry(0.36, 0.09), new THREE.MeshBasicMaterial({ map: T.label('TOILET', 256, 64, '#899c6a', '#141412') }));
  looSign.position.set(xLeft + 0.015, 2.45, LOO_Z);
  looSign.rotation.y = Math.PI / 2;
  loo.add(looSign);
  const before = new THREE.Group();
  before.add(at(rbox(0.03, 0.44, 0.34, mat('#9a9c98', 0.4, 0.4), 0.008), 0, 0, 0));
  const beforeFace = new THREE.Mesh(new THREE.PlaneGeometry(0.29, 0.39), new THREE.MeshStandardMaterial({ map: T.catPoster('BEFORE COFFEE', 'AFTER COFFEE', '#4a3522'), roughness: 0.8 }));
  beforeFace.position.x = 0.017;
  beforeFace.rotation.y = Math.PI / 2;
  before.add(beforeFace);
  loo.add(at(before, xLeft + 0.02, 1.62, -2.72));
  loo.add(at(hangingPlant(), xLeft + 0.12, 2.55, -1.75));
  loo.add(at(rbox(0.1, 0.02, 0.02, mat(C.ink, 0.5), 0.005), xLeft + 0.06, 2.62, -1.75));
  left.decor.add(loo);

  // Between the door and the fridge: the cat painting and the gold stars.
  const frames = new THREE.Group();
  const loader = new THREE.TextureLoader();
  const catTex = loader.load('/img/cafe/cat-print.webp', () => invalidate());
  catTex.colorSpace = THREE.SRGBColorSpace;
  frames.add(at(rbox(0.035, 0.46, 0.34, darkWood, 0.01), xLeft + 0.02, 2.25, -2.72));
  const catPh = new THREE.Mesh(new THREE.PlaneGeometry(0.28, 0.4), new THREE.MeshStandardMaterial({ map: catTex, roughness: 0.8 }));
  catPh.position.set(xLeft + 0.04, 2.25, -2.72);
  catPh.rotation.y = Math.PI / 2;
  frames.add(catPh);
  const starShape = new THREE.Shape();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU + Math.PI / 2;
    const rr = i % 2 ? 0.018 : 0.09;
    if (i) starShape.lineTo(Math.cos(a) * rr, Math.sin(a) * rr);
    else starShape.moveTo(Math.cos(a) * rr, Math.sin(a) * rr);
  }
  const starGeo = new THREE.ExtrudeGeometry(starShape, { depth: 0.01, bevelEnabled: false });
  for (const [z, y, sc] of [
    [-2.05, 2.1, 0.9],
    [-2.3, 2.55, 0.6],
    [-2.3, 1.72, 0.6],
  ] as const) {
    const st = new THREE.Mesh(starGeo, mat('#c9a44c', 0.3, 0.8));
    st.position.set(xLeft + 0.01, y, z);
    st.rotation.y = Math.PI / 2;
    st.scale.setScalar(sc);
    frames.add(st);
  }
  left.decor.add(frames);

  // ---- front wall: shop window with the logo vinyl, entrance ----------------------
  const fz = D2 + 0.1;
  const win = front.decor;
  for (const x of [WIN.a0, (WIN.a0 + WIN.a1) / 2, WIN.a1])
    win.add(at(rbox(0.07, WIN.y1 - WIN.y0, 0.24, darkWood, 0.02), x, (WIN.y0 + WIN.y1) / 2, fz));
  win.add(at(rbox(WIN.a1 - WIN.a0, 0.07, 0.24, darkWood, 0.02), (WIN.a0 + WIN.a1) / 2, WIN.y0, fz));
  win.add(at(rbox(WIN.a1 - WIN.a0, 0.07, 0.24, darkWood, 0.02), (WIN.a0 + WIN.a1) / 2, WIN.y1, fz));
  const pane = new THREE.Mesh(
    new THREE.PlaneGeometry(WIN.a1 - WIN.a0, WIN.y1 - WIN.y0),
    new THREE.MeshStandardMaterial({ color: '#dfe6e2', roughness: 0.05, transparent: true, opacity: 0.18, depthWrite: false, side: THREE.DoubleSide }),
  );
  pane.position.set((WIN.a0 + WIN.a1) / 2, (WIN.y0 + WIN.y1) / 2, fz);
  win.add(pane);
  // White vinyl logo, applied outside -- reads backwards from in here, as it does.
  const vinyl = new THREE.Group();
  const VS = 2.0 / LOGO_VB.w;
  svg.paths.forEach((p, i) => {
    if (i === 3 || i === 4) return; // the vinyl is lettering and line only
    const m = new THREE.Mesh(new THREE.ShapeGeometry(p.toShapes(), 4), new THREE.MeshBasicMaterial({ color: '#f7f5ee', side: THREE.DoubleSide }));
    m.scale.set(VS, -VS, 1);
    m.position.set(-LOGO_VB.x * VS, LOGO_VB.y * VS, 0);
    vinyl.add(m);
  });
  vinyl.rotation.y = Math.PI;
  vinyl.position.set((WIN.a0 + WIN.a1) / 2 + 1.0, 2.35, fz + 0.005);
  win.add(vinyl);
  // The door is glazed in a white frame; through it, the tenements over the street.
  const doorWhite = mat(C.white, 0.45);
  win.add(at(rbox(0.07, 2.4, 0.26, doorWhite, 0.02), DOOR.a0, 1.2, fz));
  win.add(at(rbox(0.07, 2.4, 0.26, doorWhite, 0.02), DOOR.a1, 1.2, fz));
  win.add(at(rbox(DOOR.a1 - DOOR.a0, 0.08, 0.26, doorWhite, 0.02), (DOOR.a0 + DOOR.a1) / 2, 2.4, fz));
  const street = new THREE.Mesh(new THREE.PlaneGeometry(DOOR.a1 - DOOR.a0, 2.36), new THREE.MeshBasicMaterial({ map: T.streetView(), toneMapped: false }));
  street.position.set((DOOR.a0 + DOOR.a1) / 2, 1.18, fz + 0.35);
  street.rotation.y = Math.PI;
  win.add(street);

  // ---- right wall, from the owner's photos (2026-09-26) ------------------------------
  // Door end: the hand-painted landscape (mountains, lake, sailboat, pines, cats)
  // with the blossom tree leaning in by the door. Counter end: the tree whose
  // trunk rises in the corner and whose bough runs along the wall, the snack
  // shelves, the apple painting, the espresso poster, the allergy notice, and
  // the white cat walking along the dado rail.
  const xRight = W2; // inner face
  const onRight = (o: THREE.Object3D, z: number, y: number, inset = 0.012) => {
    o.position.set(xRight - inset, y, z);
    o.rotation.y = -Math.PI / 2;
    right.decor.add(o);
    return o;
  };
  const decal = (map: THREE.Texture, w: number, h: number) =>
    // Unlit, so the chalk reads the same white from every side of the room.
    new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshBasicMaterial({ map, transparent: true, depthWrite: false, color: '#e9e6dc' }));
  onRight(decal(T.landscapeMural(), 3.4, 2.1), 2.2, 2.08);
  onRight(decal(T.cornerTree(), 3.4, 1.55), -2.25, 2.4);
  onRight(decal(T.walkingCat(), 0.34, 0.27), -1.0, 1.135, 0.05);
  const picture = (map: THREE.Texture, w: number, h: number, frame: string) => {
    const grp = new THREE.Group();
    grp.add(at(rbox(w + 0.04, h + 0.04, 0.025, mat(frame, 0.5), 0.006), 0, 0, 0));
    const face = new THREE.Mesh(new THREE.PlaneGeometry(w, h), new THREE.MeshStandardMaterial({ map, roughness: 0.85 }));
    face.position.z = 0.014;
    grp.add(face);
    return grp;
  };
  onRight(picture(T.applePainting(), 0.3, 0.3, C.white), -3.15, 1.85, 0.03);
  onRight(picture(T.catPoster('MORE ESPRESSO', 'LESS DEPRESSO'), 0.22, 0.3, C.ink), -2.75, 1.7, 0.03);
  onRight(picture(T.notice('Food Allergy\nor\nIntolerance?'), 0.2, 0.27, '#fbfbf8'), -2.45, 1.52, 0.03);
  // two snack shelves: crisp tubes and packets below, small bottles and cards above
  const snackShelf = new THREE.Group();
  const pine = mat(C.oak, 0.6);
  snackShelf.add(at(rbox(1.0, 0.03, 0.18, pine, 0.006), 0, 0, 0.09));
  snackShelf.add(at(rbox(0.75, 0.03, 0.16, pine, 0.006), 0.05, 0.3, 0.08));
  ['#c44b3b', '#3a8a5a', '#c44b3b', '#e0c24a', '#3c6aa8'].forEach((c, i) =>
    snackShelf.add(at(cyl(0.035, 0.035, 0.14, mat(c, 0.5), 10), -0.44 + i * 0.08, 0.085, 0.09)),
  );
  for (let i = 0; i < 5; i++) snackShelf.add(at(rbox(0.09, 0.12, 0.03, mat(i % 2 ? '#d8452f' : '#e3a33a', 0.6), 0.005), -0.02 + i * 0.1, 0.075, 0.1, 0.1));
  for (let i = 0; i < 7; i++) snackShelf.add(at(cyl(0.015, 0.015, 0.08, mat('#e8d6b5', 0.3), 8), -0.25 + i * 0.05, 0.055, 0.1));
  for (let i = 0; i < 6; i++) snackShelf.add(at(cyl(0.013, 0.013, 0.09, mat(i % 2 ? '#8c5a2e' : '#f1efe8', 0.3), 8), -0.2 + i * 0.06, 0.36, 0.08));
  snackShelf.add(at(rbox(0.14, 0.1, 0.01, mat(C.white, 0.6), 0.004), 0.3, 0.37, 0.12));
  onRight(snackShelf, -1.95, 1.3, 0);

  // Door end: chess table with the board games piled on it, a chessboard leaning
  // on the panelling behind, the spiky plant, two spindle chairs by the door, and
  // the black coat stand at the pillar end.
  const leaning = new THREE.Group();
  leaning.add(at(rbox(0.46, 0.46, 0.02, mat('#3a2616', 0.6), 0.006), 0, 0, 0));
  const lb = new THREE.Mesh(new THREE.PlaneGeometry(0.4, 0.4), new THREE.MeshStandardMaterial({ map: T.chessboard(), roughness: 0.6 }));
  lb.position.z = 0.012;
  leaning.add(lb);
  leaning.rotation.x = -0.12;
  onRight(leaning, 1.72, 1.02, 0.14);
  root.add(shadowed(drop(at(dracaena(1.7), W2 - 0.32, 0, 2.62), 1.25)));
  const doorChairs = new THREE.Group();
  doorChairs.add(at(spindleChair(), W2 - 0.3, 0, 3.12, -Math.PI / 2));
  doorChairs.add(at(spindleChair(), W2 - 0.3, 0, 3.62, -Math.PI / 2));
  root.add(shadowed(drop(doorChairs, 1.3)));
  root.add(shadowed(drop(at(coatStand(), W2 - 0.3, 0, 0.92), 1.35)));
  // White banister of the stair down, just inside the door.
  front.decor.add(shadowed(at(banister(0.85), DOOR.a0 - 0.1, 0, D2 - 0.9)));

  // LED strip along the ceiling line of every wall, as fitted in the café.
  const leds = new THREE.MeshStandardMaterial({ color: '#fff0cc', emissive: '#ffb45e', emissiveIntensity: 0 });
  const ledGeo = new THREE.SphereGeometry(0.02, 6, 5);
  for (const key of ['back', 'left', 'front', 'right'] as WallKey[]) {
    const alongX = key === 'back' || key === 'front';
    const f = (key === 'back' || key === 'left' ? -1 : 1) * ((alongX ? D2 : W2) - 0.02);
    const len = alongX ? W2 : D2;
    const n = Math.round(len * 2 * 7);
    const im = new THREE.InstancedMesh(ledGeo, leds, n);
    for (let i = 0; i < n; i++) {
      const a = -len + ((i + 0.5) / n) * len * 2;
      im.setMatrixAt(i, m4.makeTranslation(alongX ? a : f, H - 0.08, alongX ? f : a));
    }
    walls[key].decor.add(im);
  }

  // ---- floor ---------------------------------------------------------------------------
  const grey = '#8f9496';
  const green = '#6d8a72';
  const plate = (color: string) => {
    const p = new THREE.Group();
    p.add(at(cyl(0.12, 0.1, 0.012, mat(C.paper, 0.4), 24), 0, 0.006, 0));
    const sl = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.08, 16, 1, false, 0, Math.PI / 3), mat(color, 0.7));
    sl.position.y = 0.05;
    p.add(sl);
    return p;
  };

  // One bar ledge with seven stools: four along the window, three down the logo
  // wall, the rubber plant in the corner where they meet.
  const bar = new THREE.Group();
  const LEDGE = 1.05;
  const ledgeTop = mat(C.white, 0.4);
  const bracket = mat(C.darkWood, 0.6);
  const FX0 = -2.65; // window run, x from FX0 to FX1
  const FX1 = 0.12;
  const LZ0 = -0.35; // wall run, z from LZ0 to LZ1
  const LZ1 = 3.15;
  bar.add(at(rbox(FX1 - FX0, 0.05, 0.36, ledgeTop, 0.012), (FX0 + FX1) / 2, LEDGE, D2 - 0.18));
  bar.add(at(rbox(0.36, 0.05, LZ1 - LZ0, ledgeTop, 0.012), -W2 + 0.18, LEDGE, (LZ0 + LZ1) / 2));
  for (const x of [FX0 + 0.1, (FX0 + FX1) / 2, FX1 - 0.1]) bar.add(at(rbox(0.04, 0.3, 0.3, bracket, 0.01), x, LEDGE - 0.17, D2 - 0.17));
  for (const z of [LZ0 + 0.1, (LZ0 + LZ1) / 2, LZ1 - 0.1]) bar.add(at(rbox(0.3, 0.3, 0.04, bracket, 0.01), -W2 + 0.17, LEDGE - 0.17, z));
  const SZ = D2 - 0.62; // window stools' z
  const SX = -W2 + 0.62; // wall stools' x
  [-0.2, -0.93, -1.66, -2.39].forEach((x, i) => {
    bar.add(at(barStool(i % 2 ? green : grey), x, 0, SZ));
    addSeat(bar, `w${i}`, x, SZ, 0, STOOL, [[x, LANE_Z]], i < 2 ? 'win-a' : 'win-b', 'front');
  });
  [2.3, 1.35, 0.4].forEach((z, i) => {
    bar.add(at(barStool(i % 2 ? grey : green), SX, 0, z));
    addSeat(bar, `l${i}`, SX, z, -Math.PI / 2, STOOL, [[LANE_X, LANE_Z], [LANE_X, z]], i < 2 ? 'wall-a' : 'wall-b', 'left');
  });
  // what the stool people have in front of them
  bar.add(at(cup(C.paper), -0.2, LEDGE + 0.025, D2 - 0.2));
  // Two people working on laptops (owner, 2026-09-26), each with a drink beside.
  for (const x of [-0.93, -2.39]) bar.add(at(laptop(), x, LEDGE + 0.025, D2 - 0.26, Math.PI));
  bar.add(at(glass('#5b5fa8', 0.14), -0.62, LEDGE + 0.025, D2 - 0.2)); // blue matcha
  bar.add(at(cup(C.paper), -2.08, LEDGE + 0.025, D2 - 0.2));
  bar.add(at(cup(C.sage), -W2 + 0.2, LEDGE + 0.025, 2.3));
  bar.add(at(plate('#c79363'), -W2 + 0.2, LEDGE + 0.025, 1.18));
  root.add(shadowed(drop(bar, 0.95)));
  root.add(shadowed(drop(at(ficus(1.3), -3.05, 0, 3.55), 1.3)));

  // The big round table, the booking hotspot.
  const table = new THREE.Group();
  const TX = -0.8;
  const TZ = 0.8;
  table.position.set(TX, 0, TZ);
  table.add(bigRoundTable(0.78));
  for (let k = 0; k < 6; k++) {
    const a = Math.PI / 6 + (k * Math.PI) / 3; // 30 deg, 90, 150, ... round from +z
    const r = 1.02;
    const ax = TX + Math.sin(a) * 1.5;
    const az = TZ + Math.cos(a) * 1.5;
    // straight in from the aisle where nothing is in the way, else round by a lane
    const via: Array<[number, number]> =
      Math.sin(a) > 0.1 || az < TZ - 0.3 ? [[ax, az]] : az > TZ + 0.3 ? [[ax, LANE_Z], [ax, az]] : [[LANE_X, LANE_Z], [LANE_X, az]];
    table.add(at(chair(k % 2 ? green : grey), Math.sin(a) * r, 0, Math.cos(a) * r, a + Math.PI));
    addSeat(table, `t${k}`, Math.sin(a) * r, Math.cos(a) * r, a + Math.PI, CHAIR, via, 'big');
    table.add(at(placemat(), Math.sin(a) * 0.52, 0.772, Math.cos(a) * 0.52));
  }
  table.add(at(cup(C.sage), Math.sin(Math.PI / 6) * 0.52, 0.778, Math.cos(Math.PI / 6) * 0.52));
  table.add(at(glass(C.caramel, 0.14), Math.sin(Math.PI / 2) * 0.52, 0.778, 0));
  table.add(at(plate('#c79363'), -0.15, 0.772, -0.1));
  table.add(at(cup(C.paper), Math.sin((7 * Math.PI) / 6) * 0.52, 0.778, Math.cos((7 * Math.PI) / 6) * 0.52));
  table.add(at(glass('#5b5fa8', 0.14), Math.sin((3 * Math.PI) / 2) * 0.52, 0.778, 0.05));
  table.add(at(smallPlant(), 0.08, 0.77, 0.12));
  root.add(shadowed(drop(table, 1.05)));

  // Pillar, and the two small tables on its counter side.
  root.add(shadowed(drop(at(pillar(H - 0.02), W2 - 0.55, 0, 0.35), 0.9)));
  [-0.5, -1.45].forEach((z, i) => {
    const g = new THREE.Group();
    g.position.set(2.7, 0, z);
    g.add(squareTable(0.62));
    g.add(at(placemat(), -0.12, 0.77, 0));
    g.add(at(placemat(), 0.14, 0.77, 0.02));
    g.add(at(i ? cup(C.olive700) : glass(C.caramel, 0.14), -0.12, 0.776, 0));
    const party = `col${i}`;
    g.add(at(chair(green), -0.55, 0, 0, Math.PI / 2));
    addSeat(g, `c${i}w`, -0.55, 0, Math.PI / 2, CHAIR, [[1.62, z]], party);
    g.add(at(chair(grey), 0.55, 0, 0, -Math.PI / 2));
    addSeat(g, `c${i}e`, 0.55, 0, -Math.PI / 2, CHAIR, [[1.62, z + 0.5], [3.25, z + 0.5]], party);
    root.add(shadowed(drop(g, 1.15 + i * 0.07)));
  });

  // Three-seat round table in the open floor by the fridge (owner, 2026-09-26).
  // Approach lane z = -1.2 runs clear of the counter front and the big table.
  const three = new THREE.Group();
  three.position.set(-2.0, 0, -2.35);
  three.add(roundTable());
  three.add(at(cup(C.sage), 0.12, 0.77, 0.08));
  three.add(at(glass(C.caramel, 0.14), -0.14, 0.77, -0.05));
  ([
    [0, [[-2.0, -1.2]]],
    [(2 * Math.PI) / 3, [[-1.3, -1.2], [-1.3, -2.66]]],
    [(-2 * Math.PI) / 3, [[-2.95, -1.2], [-2.95, -2.66]]],
  ] as Array<[number, Array<[number, number]>]>).forEach(([a, via], k) => {
    const r = 0.6;
    three.add(at(chair(k === 1 ? green : grey), Math.sin(a) * r, 0, Math.cos(a) * r, a + Math.PI));
    addSeat(three, `f${k}`, Math.sin(a) * r, Math.cos(a) * r, a + Math.PI, CHAIR, via, 'fridge');
  });
  root.add(shadowed(drop(three, 1.1)));

  // Chess table in front of the pillar.
  const chess = new THREE.Group();
  chess.position.set(2.7, 0, 1.65);
  chess.add(squareTable(0.7));
  const cb = new THREE.Mesh(new THREE.PlaneGeometry(0.44, 0.44), new THREE.MeshStandardMaterial({ map: T.chessboard(), roughness: 0.5 }));
  cb.rotation.x = -Math.PI / 2;
  cb.position.set(-0.08, 0.771, 0);
  chess.add(cb);
  chess.add(at(gameStack(), 0.22, 0.768, -0.02, 0.3));
  for (let i = 0; i < 10; i++) {
    const white = i < 5;
    const pc = new THREE.Group();
    pc.add(at(cyl(0.012, 0.018, 0.035, mat(white ? C.white : C.ink, 0.4), 10), 0, 0.017, 0));
    pc.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.013, 10, 8), mat(white ? C.white : C.ink, 0.4)), 0, 0.045, 0));
    chess.add(at(pc, -0.27 + (i % 5) * 0.09, 0.771, white ? 0.16 : -0.16 + (i === 7 ? 0.1 : 0)));
  }
  chess.add(at(chair(grey), 0, 0, 0.62, Math.PI));
  addSeat(chess, 'k0', 0, 0.62, Math.PI, CHAIR, [[2.7, LANE_Z]], 'chess');
  chess.add(at(chair(green), 0, 0, -0.62, 0));
  addSeat(chess, 'k1', 0, -0.62, 0, CHAIR, [[2.0, 1.03]], 'chess');
  root.add(shadowed(drop(chess, 1.2)));

  // Entrance: mat and the A-frame board (-> /visit), between the window bar and the door.
  const entrance = new THREE.Group();
  entrance.add(at(rbox(1.0, 0.02, 0.7, mat('#8f7650', 1), 0.008), (DOOR.a0 + DOOR.a1) / 2, 0.01, D2 - 0.45));
  entrance.add(at(aFrame(T.aFrameBoard()), 0.55, 0, D2 - 0.5, -0.5));
  root.add(shadowed(drop(entrance, 0.8)));

  // ---- lights ----------------------------------------------------------------------
  scene.add(new THREE.HemisphereLight('#fff4e2', C.olive900, 1.2));
  const sun = new THREE.DirectionalLight('#ffe0b0', 2.6);
  sun.position.set(-4, 9, 8); // daylight through the shop window
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  Object.assign(sun.shadow.camera, { left: -7, right: 7, top: 7, bottom: -7, near: 1, far: 30 });
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.02;
  scene.add(sun, sun.target);
  const fill = new THREE.DirectionalLight('#fff1dc', 0.8);
  fill.position.set(8, 7, -6);
  scene.add(fill);
  const lamps: THREE.PointLight[] = [];
  for (const [x, z] of [
    [1.5, -2.4],
    [-1.2, 1.2],
  ] as const) {
    const l = new THREE.PointLight('#ffc27a', 0, 4.5, 1.5);
    l.position.set(x, H - 0.4, z);
    root.add(l);
    lamps.push(l);
  }

  const ground = new THREE.Mesh(
    new THREE.PlaneGeometry(14, 14),
    new THREE.MeshBasicMaterial({ map: T.contactShadow(), transparent: true, depthWrite: false, toneMapped: false }),
  );
  ground.rotation.x = -Math.PI / 2;
  ground.position.y = -0.31;
  root.add(ground);

  // ---- people ------------------------------------------------------------------------
  const reduced = opts.reducedMotion;
  const skins = ['#f1c9a8', '#e2b594', '#c68d64', '#8a5a3c', '#f3d2b8', '#a8714c', '#5e3b26', '#eab893'];
  const hairs = ['#1d1a17', '#3b2a1e', '#5a3a24', '#c9a15a', '#7a4a2e', '#2b2320', '#8c8a86', '#1d1a17', '#a0522d'];
  const tops = [C.caramel, C.olive700, '#8f9aa6', C.coffee, C.sage, C.oat, '#5b6b7a', C.olive900, C.paper, '#b5654a', '#d9c7a8'];
  const styles = ['short', 'long', 'crop', 'bun', 'curly', 'ponytail', 'short', 'long', 'crop'] as const;
  const lookFor = (i: number): Look => ({
    top: tops[(i * 7) % tops.length],
    skin: skins[(i * 3) % skins.length],
    hair: hairs[(i * 5) % hairs.length],
    style: styles[(i * 4) % styles.length],
    legs: i % 3 ? '#2b2a26' : '#3c4a5c',
  });

  let viewW = 1;
  let viewH = 1;
  const hv = new THREE.Vector3();
  function onScreenHead(f: Figure, wall?: WallKey) {
    if (!f.group.visible || (f.group.parent && !f.group.parent.visible)) return null;
    if (wall && !walls[wall].decor.visible) return null; // turned away, like the pins
    f.head.getWorldPosition(hv);
    hv.y += 0.2;
    hv.project(camera);
    return { x: ((hv.x + 1) / 2) * viewW, y: ((1 - hv.y) / 2) * viewH };
  }

  // Who sits where. Each seat's occupant idles; a party is who talks to whom.
  type Sitter = { f: Figure; seed: number; speaker: Speaker };
  const occupant = new Map<Seat, Sitter>();
  function seatFigure(f: Figure, seat: Seat, seed: number, speaker: Speaker) {
    seat.parent.add(f.group);
    f.group.position.copy(seat.local);
    f.group.rotation.y = seat.face;
    sit(f, seat.h);
    seat.taken = true;
    occupant.set(seat, { f, seed, speaker });
  }
  // About 30% full (owner, 2026-09-26): 7 of 22 seats. Two on laptops at the
  // window bar, two at chess, one each at the big, small and fridge tables.
  const residents = ['w1', 'w3', 'k0', 'k1', 't0', 'c0e', 'f0'];
  const onLaptop = new Set(['w1', 'w3']);
  residents.forEach((key, i) => {
    const seat = seats.find((s) => s.key === key);
    if (!seat) return;
    const f = figure(lookFor(i));
    f.cup.visible = !onLaptop.has(key) && seat.party !== 'chess' && i % 3 !== 1;
    if (onLaptop.has(key)) f.arms.forEach((a) => (a.rotation.x = -1.1));
    seatFigure(f, seat, i * 1.37, { where: () => onScreenHead(f, seat.wall) });
    idle(f, 0, i * 1.37, f.cup.visible);
  });

  // Sasha, at the till; and a second barista on the machine.
  const sasha = figure({ top: C.paper, skin: '#f1c9a8', hair: '#4a2e1c', style: 'ponytail', legs: '#2b2a26', apron: C.olive900 });
  const TILL = v3(-0.5, 0, cz - 0.72);
  sasha.group.position.copy(TILL);
  root.add(sasha.group);
  const sashaSays: Speaker = { staff: true, where: () => onScreenHead(sasha, 'back') };
  const barista = figure({ top: C.ink, skin: '#c68d64', hair: '#1d1a17', style: 'crop', legs: '#2b2a26', apron: C.olive900 });
  barista.group.position.set(2.2, 0, cz - 0.72);
  root.add(barista.group);
  for (const f of [sasha, barista]) f.arms.forEach((a) => (a.rotation.x = -0.35));

  // Lines. Drinks come from the real menu rows passed in from the page.
  const drinks = opts.menuRows.map((r) => r[0]).filter((n) => n && !/[()]/.test(n) && n.length <= 26);
  if (!drinks.length) drinks.push('Flat white', 'Blue vanilla matcha', 'Raff coffee');
  const any = <X,>(xs: readonly X[]): X => xs[Math.floor(Math.random() * xs.length)];
  const HELLO = "Hello, I'm Sasha! What can I get for you today?";
  const ORDERS = [(d: string) => `${d}, please.`, (d: string) => `${d} to stay, please.`, (d: string) => `${d}, and a slice of Kyiv cake.`];
  const REPLIES = ['Coming right up.', 'Lovely. Coming right up.', 'Good choice. Have a seat!'];
  const CHAT: Array<() => [string, string]> = [
    () => ['This Kyiv cake is unreal.', 'Told you.'],
    () => ['Same again next week?', 'Obviously.'],
    () => ['How many drinks are on that board?', 'Over a hundred, I think.'],
    () => ['Try a sip of mine.', 'Oh, that’s good.'],
    () => [`${any(drinks)} next time?`, 'Definitely.'],
    () => ['Should we get waffles?', 'Pistachio ones.'],
  ];
  const CHESS: Array<[string, string]> = [
    ['Your move.', 'Give me a minute…'],
    ['Check.', 'Not again.'],
  ];
  const talk = new Talk(host);

  // Walk-in customers.
  type Phase = 'wait' | 'enter' | 'queue' | 'order' | 'walk' | 'settle' | 'sit' | 'leave';
  interface Guest {
    f: Figure;
    phase: Phase;
    path: THREE.Vector3[];
    timer: number;
    seat: Seat | null;
    wall?: WallKey;
    speaker: Speaker;
    step: number;
  }
  const DOOR_IN = v3((DOOR.a0 + DOOR.a1) / 2, 0, D2 + 0.1);
  const INSIDE = v3((DOOR.a0 + DOOR.a1) / 2, 0, D2 - 0.55);
  const queueSpot = (i: number) => v3(TILL.x + 0.05, 0, cz + 0.75 + i * 0.62);
  const guests: Guest[] = [];
  const queue: Guest[] = [];
  const guestCount = reduced ? 0 : 1;
  for (let i = 0; i < guestCount; i++) {
    const f = figure(lookFor(20 + i * 3));
    f.group.visible = false;
    root.add(f.group);
    const g: Guest = { f, phase: 'wait', path: [], timer: 0.4 + i * 4.5, seat: null, speaker: { where: () => onScreenHead(f, g.wall) }, step: Math.random() * 6 };
    guests.push(g);
  }
  let orders = 0;
  let chatIn = 14;
  let clockT = 0;
  const tmp = v3(0, 0, 0);

  // Walk toward `to`; true on arrival.
  function walk(f: Figure, to: THREE.Vector3, dt: number, g?: Guest) {
    const G = f.group;
    tmp.copy(to).sub(G.position);
    tmp.y = 0;
    const dist = tmp.length();
    if (dist < 0.02) return true;
    tmp.normalize();
    G.position.addScaledVector(tmp, Math.min(dist, 0.9 * dt));
    let dr = Math.atan2(tmp.x, tmp.z) - G.rotation.y;
    dr = ((((dr + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
    G.rotation.y += dr * Math.min(1, dt * 10);
    if (g) g.step += dt * 9;
    walkPose(f, g ? g.step : clockT * 9);
    return false;
  }
  function turnTo(G: THREE.Object3D, face: number, dt: number) {
    let dr = face - G.rotation.y;
    dr = ((((dr + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
    G.rotation.y += dr * Math.min(1, dt * 6);
  }

  function stepGuest(g: Guest, dt: number) {
    const f = g.f;
    const G = f.group;
    switch (g.phase) {
      case 'wait':
        g.timer -= dt;
        if (g.timer > 0) return;
        root.add(G);
        G.visible = true;
        G.position.copy(DOOR_IN);
        G.rotation.y = Math.PI;
        sit(f, 0);
        f.cup.visible = false;
        g.wall = undefined;
        g.path = [INSIDE.clone(), v3(AISLE_X, 0, 2.9), v3(AISLE_X, 0, -0.4)];
        g.phase = 'enter';
        return;
      case 'enter':
        if (walk(f, g.path[0], dt, g)) g.path.shift();
        if (!g.path.length) {
          queue.push(g);
          g.phase = 'queue';
        }
        return;
      case 'queue': {
        const i = queue.indexOf(g);
        if (!walk(f, queueSpot(i), dt, g)) return;
        standStill(f);
        turnTo(G, Math.PI, dt);
        if (i === 0) {
          g.phase = 'order';
          g.timer = 8.2;
          const d = any(drinks);
          talk.play('counter', [
            [sashaSays, orders++ === 0 || Math.random() < 0.6 ? HELLO : 'Hi! What can I get for you today?'],
            [g.speaker, any(ORDERS)(d)],
            [sashaSays, any(REPLIES)],
          ]);
        }
        return;
      }
      case 'order': {
        standStill(f);
        turnTo(G, Math.PI, dt);
        g.timer -= dt;
        if (g.timer > 0) return;
        queue.shift();
        f.cup.visible = true;
        f.arms[0].rotation.x = -0.6;
        const free = seats.filter((s) => !s.taken);
        const seat = free[Math.floor(Math.random() * free.length)];
        if (!seat) {
          g.path = [v3(AISLE_X, 0, G.position.z), v3(AISLE_X, 0, 2.9), INSIDE.clone(), DOOR_IN.clone()];
          g.phase = 'leave';
          return;
        }
        seat.taken = true;
        g.seat = seat;
        g.path = [v3(AISLE_X, 0, G.position.z), ...seat.via.map((p) => p.clone())];
        g.phase = 'walk';
        return;
      }
      case 'walk':
        if (walk(f, g.path[0], dt, g)) g.path.shift();
        f.arms[0].rotation.x = -0.6; // carrying the cup
        if (!g.path.length) {
          g.phase = 'settle';
          g.timer = 0.5;
        }
        return;
      case 'settle': {
        const s = g.seat!;
        g.timer -= dt;
        const k = Math.min(1, dt * 8);
        G.position.lerp(s.pos, k);
        turnTo(G, s.face, dt * 2);
        standStill(f);
        if (g.timer > 0) return;
        seatFigure(f, s, 30 + Math.random() * 10, g.speaker);
        g.wall = s.wall;
        g.phase = 'sit';
        g.timer = 16 + Math.random() * 12;
        return;
      }
      case 'sit': {
        g.timer -= dt;
        if (g.timer > 0) return;
        const s = g.seat!;
        occupant.delete(s);
        s.taken = false;
        g.seat = null;
        g.wall = undefined;
        root.add(G);
        G.position.copy(s.via[s.via.length - 1]);
        sit(f, 0);
        f.cup.visible = false;
        f.upper.rotation.x = 0;
        f.head.rotation.set(0, 0, 0);
        g.path = [...s.via.slice(0, -1).reverse().map((p) => p.clone()), v3(AISLE_X, 0, 2.9), INSIDE.clone(), DOOR_IN.clone()];
        g.phase = 'leave';
        return;
      }
      case 'leave':
        if (walk(f, g.path[0], dt, g)) g.path.shift();
        if (!g.path.length) {
          G.visible = false;
          g.phase = 'wait';
          g.timer = 3 + Math.random() * 6;
        }
        return;
    }
  }

  // Sasha takes orders at the till and turns to the machine between them; the
  // barista works the machine, the grinder and the back bar.
  let sashaTurn = 0;
  function stepStaff(t: number, dt: number) {
    const serving = talk.busy('counter');
    const goal = serving ? 0 : Math.sin(t * 0.23) > 0.4 ? 1.1 : 0.15 * Math.sin(t * 0.5);
    sashaTurn += (goal - sashaTurn) * Math.min(1, dt * 3);
    sasha.group.rotation.y = sashaTurn;
    sasha.head.rotation.y = serving ? 0 : Math.sin(t * 0.7) * 0.3;
    sasha.arms[0].rotation.x = -0.35 - (serving ? 0.25 : 0.4 * Math.max(0, Math.sin(t * 1.3)));
    if (sasha.tail) sasha.tail.rotation.z = Math.sin(t * 1.1) * 0.12;
    const bx = 2.05 + Math.sin(t * 0.35) * 0.5;
    const moving = Math.abs(Math.cos(t * 0.35)) > 0.3;
    barista.group.position.x = bx;
    barista.group.rotation.y = Math.sin(t * 0.35) > 0.7 ? Math.PI : Math.cos(t * 0.35) > 0 ? Math.PI / 2 : -Math.PI / 2;
    if (moving) walkPose(barista, t * 7);
    else standStill(barista);
    barista.arms[0].rotation.x = -0.6;
    barista.arms[1].rotation.x = -0.5;
  }

  function stepSitters(t: number) {
    for (const s of occupant.values()) idle(s.f, t, s.seed, s.f.cup.visible);
  }

  // Table chatter: two people from the same party, both on screen.
  function stepChat(dt: number) {
    chatIn -= dt;
    if (chatIn > 0 || talk.busy('room')) return;
    chatIn = 22 + Math.random() * 14;
    const parties = new Map<string, Sitter[]>();
    for (const [seat, who] of occupant) {
      if (!who.speaker.where()) continue;
      const list = parties.get(seat.party) ?? [];
      list.push(who);
      parties.set(seat.party, list);
    }
    const ready = [...parties.entries()].filter(([, l]) => l.length >= 2);
    if (!ready.length) return;
    const [party, list] = any(ready);
    const a = list.splice(Math.floor(Math.random() * list.length), 1)[0];
    const b = any(list);
    const [la, lb] = party === 'chess' ? any(CHESS) : any(CHAT)();
    talk.play('room', [
      [a.speaker, la],
      [b.speaker, lb],
    ]);
  }

  // ---- camera: orthographic, orbiting in azimuth only ---------------------------------
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 100);
  const target = v3(0, 1.0, 0);
  const ELEV = 0.62; // radians above the horizon
  let theta = Math.PI / 4; // default view: from the front-right
  let thetaGoal = theta;
  let spin = 0; // inertia, rad/s
  function placeCamera() {
    camera.position.set(
      target.x + 30 * Math.cos(ELEV) * Math.sin(theta),
      target.y + 30 * Math.sin(ELEV),
      target.z + 30 * Math.cos(ELEV) * Math.cos(theta),
    );
    camera.lookAt(target);
    camera.updateMatrixWorld();
  }
  // Frustum sized once for the widest angle, so turning never zooms.
  function fit() {
    const w = (viewW = host.clientWidth);
    const h = (viewH = host.clientHeight);
    renderer.setSize(w, h, false);
    const keep = theta;
    let ex = 0;
    let eyMin = Infinity;
    let eyMax = -Infinity;
    for (let a = 0; a < TAU; a += Math.PI / 12) {
      theta = a;
      placeCamera();
      const inv = camera.matrixWorldInverse;
      for (let i = 0; i < 8; i++) {
        const p = v3(i & 1 ? W2 + 0.2 : -W2 - 0.2, i & 2 ? H : -0.3, i & 4 ? D2 + 0.2 : -D2 - 0.2).applyMatrix4(inv);
        ex = Math.max(ex, Math.abs(p.x));
        eyMin = Math.min(eyMin, p.y);
        eyMax = Math.max(eyMax, p.y);
      }
    }
    theta = keep;
    placeCamera();
    const aspect = w / h;
    const halfH = Math.max((eyMax - eyMin) / 2, ex / aspect) * 1.02;
    const halfW = halfH * aspect;
    const cyv = (eyMax + eyMin) / 2;
    Object.assign(camera, { left: -halfW, right: halfW, top: cyv + halfH, bottom: cyv - halfH });
    camera.updateProjectionMatrix();
    invalidate();
  }

  const introScale = new Map<THREE.Object3D, number>();
  function updateWalls(dt: number, instant = false) {
    const dir = new THREE.Vector2(Math.sin(theta), Math.cos(theta));
    for (const w of Object.values(walls)) {
      w.target = w.out.dot(dir) > 0.15 ? 0.1 : 1;
      w.level = instant ? w.target : w.level + (w.target - w.level) * Math.min(1, dt * 8);
      w.group.scale.y = Math.max(0.001, w.level * (introScale.get(w.group) ?? 1));
      w.decor.visible = w.level > 0.6;
    }
  }

  // ---- hotspots ------------------------------------------------------------------------
  const spotObjects: Record<string, { obj: THREE.Object3D; anchor: THREE.Vector3; wall?: WallKey; lift: number }> = {
    menu: { obj: counter, anchor: v3(1.6, 1.9, cz), lift: 0 },
    book: { obj: table, anchor: v3(TX, 1.45, TZ), lift: 0 },
    visit: { obj: entrance, anchor: v3(0.55, 1.2, D2 - 0.5), lift: 0 },
  };
  const pins = new Map<string, HTMLElement>();
  host.querySelectorAll<HTMLElement>('[data-spot]').forEach((el) => pins.set(el.dataset.spot!, el));
  const spotByKey = new Map(opts.spots.map((s) => [s.key, s]));
  let hot: string | null = null;
  let dragging = false;
  const setHot = (k: string | null) => {
    if (k === hot) return;
    hot = k;
    pins.forEach((el, key) => el.classList.toggle('is-hot', key === k));
    canvas.style.cursor = k ? 'pointer' : dragging ? 'grabbing' : 'grab';
    invalidate();
  };
  pins.forEach((el, key) => {
    el.addEventListener('pointerenter', () => setHot(key));
    el.addEventListener('pointerleave', () => setHot(null));
    el.addEventListener('focus', () => setHot(key));
    el.addEventListener('blur', () => setHot(null));
  });

  const ray = new THREE.Raycaster();
  const ndc = new THREE.Vector2();
  function pick(ev: PointerEvent): string | null {
    const r = canvas.getBoundingClientRect();
    ndc.set(((ev.clientX - r.left) / r.width) * 2 - 1, -((ev.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    const objs = Object.values(spotObjects)
      .filter((s) => !s.wall || walls[s.wall].decor.visible)
      .map((s) => s.obj);
    const hits = ray.intersectObjects([...objs, sasha.group], true);
    if (!hits.length) return null;
    // Sasha stands in front of the counter's hotspot, so she wins if she's hit first.
    for (let o: THREE.Object3D | null = hits[0].object; o; o = o.parent) if (o === sasha.group) return 'sasha';
    for (const [k, s] of Object.entries(spotObjects)) {
      let o: THREE.Object3D | null = hits[0].object;
      while (o) {
        if (o === s.obj) return k;
        o = o.parent;
      }
    }
    return null;
  }

  // Drag to turn. Horizontal drags only; vertical ones still scroll the page.
  let down: { x: number; y: number; theta: number } | null = null;
  let lastX = 0;
  let lastT = 0;
  canvas.style.cursor = 'grab';
  const onDown = (ev: PointerEvent) => {
    down = { x: ev.clientX, y: ev.clientY, theta };
    lastX = ev.clientX;
    lastT = performance.now();
    spin = 0;
  };
  const onMove = (ev: PointerEvent) => {
    if (down) {
      const dx = ev.clientX - down.x;
      if (!dragging && Math.abs(dx) > 6 && Math.abs(dx) > Math.abs(ev.clientY - down.y)) {
        dragging = true;
        canvas.setPointerCapture(ev.pointerId);
        canvas.style.cursor = 'grabbing';
        host.classList.add('is-turned');
      }
      if (dragging) {
        const k = 3.2 / host.clientWidth; // a full-width drag is about half a turn
        thetaGoal = theta = down.theta - dx * k;
        const now = performance.now();
        spin = (-(ev.clientX - lastX) * k) / Math.max(0.001, (now - lastT) / 1000);
        lastX = ev.clientX;
        lastT = now;
        invalidate();
        return;
      }
    }
    if (ev.pointerType === 'mouse') setHot(pick(ev));
  };
  const onUp = (ev: PointerEvent) => {
    const wasDrag = dragging;
    dragging = false;
    canvas.style.cursor = hot ? 'pointer' : 'grab';
    if (!down) return;
    down = null;
    if (wasDrag) {
      if (opts.reducedMotion || performance.now() - lastT > 80) spin = 0;
      invalidate();
      return;
    }
    const k = pick(ev);
    if (k === 'sasha') {
      if (!reduced) talk.play('counter', [[sashaSays, HELLO]], { force: true });
      invalidate();
      return;
    }
    const spot = k && spotByKey.get(k);
    if (!spot) return;
    if (spot.external) window.open(spot.href, '_blank', 'noopener');
    else window.location.href = spot.href;
  };
  canvas.addEventListener('pointerdown', onDown);
  canvas.addEventListener('pointermove', onMove);
  canvas.addEventListener('pointerup', onUp);
  canvas.addEventListener('pointercancel', onUp);
  canvas.addEventListener('pointerleave', () => setHot(null));
  host.querySelectorAll<HTMLButtonElement>('[data-turn]').forEach((b) =>
    b.addEventListener('click', () => {
      spin = 0;
      thetaGoal = thetaGoal + Number(b.dataset.turn) * (Math.PI / 2);
      host.classList.add('is-turned');
      invalidate();
    }),
  );

  const v = new THREE.Vector3();
  function placePins() {
    const w = viewW;
    const h = viewH;
    for (const [k, s] of Object.entries(spotObjects)) {
      const el = pins.get(k);
      if (!el) continue;
      const away = !!s.wall && !walls[s.wall].decor.visible;
      if (away !== el.classList.contains('is-away')) {
        el.classList.toggle('is-away', away);
        // a pin you can't see mustn't take keyboard focus or be read out
        if (away) {
          el.tabIndex = -1;
          el.setAttribute('aria-hidden', 'true');
          if (document.activeElement === el) el.blur();
        } else {
          el.removeAttribute('tabindex');
          el.removeAttribute('aria-hidden');
        }
      }
      v.copy(s.anchor);
      root.localToWorld(v);
      v.project(camera);
      el.style.transform = `translate3d(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px, 0)`;
    }
  }

  // Pins' boxes in host px, for keeping bubbles off them (the hot one padded more).
  function pinBoxes(): DOMRect[] {
    const hr = host.getBoundingClientRect();
    const out: DOMRect[] = [];
    pins.forEach((el, key) => {
      if (el.classList.contains('is-away')) return;
      const pad = key === hot ? 14 : 8; // 8 covers the 6px the bubble slides in by
      // the label may sit outside the link's box (flipped above the dot on phones)
      for (const box of [el, el.querySelector('.pin__label')]) {
        if (!box) continue;
        const r = box.getBoundingClientRect();
        out.push(new DOMRect(r.left - hr.left - pad, r.top - hr.top - pad, r.width + 2 * pad, r.height + 2 * pad));
      }
    });
    return out;
  }

  // ---- loop ------------------------------------------------------------------------------
  // Full rate while something is being turned, dropped in or lifted; the ambient
  // life (people) runs at 30fps; nothing at all when the model is off screen or
  // the tab is hidden -- tracked separately, so one can't wake the loop while the
  // other still holds it.
  const ease = (t: number) => 1 - Math.pow(1 - t, 4); // ease-out-quart, no overshoot
  const clamp01 = (t: number) => Math.min(1, Math.max(0, t));
  intro.forEach(({ o }) => o.userData.y0 === undefined && (o.userData.y0 = o.position.y));
  const WIRE_START = 1.55;
  const WIRE_DUR = 2.6;
  const AMBIENT_MS = 1000 / 30;
  let t0 = -1;
  let prev = 0;
  let lastRender = 0;
  let wasAnimating = true;
  let shadowTick = 0;
  let dbgAnimating = 0;
  let introDone = reduced;
  let tabVisible = document.visibilityState === 'visible';
  let onScreen = false;
  let raf = 0;
  const live = () => tabVisible && onScreen;
  function invalidate() {
    if (!raf && live()) raf = requestAnimationFrame(frame);
  }

  function applyIntro(t: number) {
    for (const { o, d, kind } of intro) {
      const k = clamp01((t - d) / 0.7);
      if (kind === 'rise') introScale.set(o, 1 - Math.pow(1 - k, 3));
      else {
        o.position.y = o.userData.y0 + (1 - ease(k)) * 1.6;
        o.visible = k > 0;
      }
    }
    const people = clamp01((t - 1.2) / 0.5);
    for (const f of [sasha, barista]) f.group.visible = people > 0;
    lamps.forEach((l) => (l.intensity = 2.2 * clamp01((t - 1.3) / 0.6)));
    leds.emissiveIntensity = 3 * clamp01((t - 1.1) / 0.8);
    wireUniforms.uProgress.value = ((t - WIRE_START) / WIRE_DUR) * lineTotal;
  }

  function frame(now: number) {
    raf = 0;
    if (!live()) return;
    const ambient = introDone && !reduced;
    // Ambient-only frames are skipped down to 30fps.
    if (ambient && !wasAnimating && !dragging && now - lastRender < AMBIENT_MS - 2) {
      raf = requestAnimationFrame(frame);
      return;
    }
    if (t0 < 0) t0 = prev = now;
    const t = (now - t0) / 1000;
    const dt = Math.min(0.1, Math.max(0, (now - prev) / 1000));
    prev = now;
    lastRender = now;
    clockT = t;
    let animating = false;
    let shadows = false;

    if (!introDone) {
      applyIntro(t);
      if (t > WIRE_START + WIRE_DUR) {
        introDone = true;
        wireUniforms.uProgress.value = 1e6;
        introScale.clear();
        host.classList.add('is-ready');
        opts.onReady?.();
      }
      animating = shadows = true;
    }

    // Turning: follow the drag, glide on release, or ease to a button target.
    if (!dragging) {
      if (Math.abs(spin) > 0.02) {
        theta += spin * dt;
        thetaGoal = theta;
        spin *= Math.pow(0.04, dt);
        animating = true;
      } else if (Math.abs(thetaGoal - theta) > 0.001) {
        theta += (thetaGoal - theta) * (reduced ? 1 : Math.min(1, dt * 7));
        animating = true;
      }
    }
    placeCamera();
    updateWalls(dt, reduced);
    if (Object.values(walls).some((w) => Math.abs(w.level - w.target) > 0.01)) animating = shadows = true;

    for (const [k, s] of Object.entries(spotObjects)) {
      const goal = hot === k && !reduced ? 0.06 : 0;
      s.lift += (goal - s.lift) * 0.18;
      if (introDone) s.obj.position.y = (s.obj.userData.y0 ?? 0) + s.lift;
      if (Math.abs(goal - s.lift) > 0.001) animating = shadows = true;
    }

    if (ambient) {
      for (const g of guests) stepGuest(g, dt);
      stepStaff(t, dt);
      stepSitters(t);
      stepChat(dt);
      talk.tick(dt);
      // people move a few cm a frame: their shadows can follow at 15Hz
      if (++shadowTick % 2 === 0) shadows = true;
    }

    placePins();
    if (talk.count) talk.place(viewW, viewH, pinBoxes());
    if (shadows) renderer.shadowMap.needsUpdate = true;
    renderer.render(scene, camera);
    wasAnimating = animating || dragging;
    if (import.meta.env.DEV && animating) dbgAnimating++;
    if (animating || ambient) raf = requestAnimationFrame(frame);
  }

  if (reduced) {
    applyIntro(99);
    introScale.clear();
    stepStaff(0, 0);
    talk.pin(sashaSays, "Hello, I'm Sasha!");
    host.classList.add('is-ready');
    opts.onReady?.();
  }

  const ro = new ResizeObserver(fit);
  ro.observe(host);
  const io = new IntersectionObserver(([e]) => {
    onScreen = e.isIntersecting;
    prev = performance.now();
    invalidate();
  });
  io.observe(host);
  const onVis = () => {
    tabVisible = document.visibilityState === 'visible';
    prev = performance.now();
    invalidate();
  };
  document.addEventListener('visibilitychange', onVis);
  placeCamera();
  updateWalls(0, true);
  fit();

  if (import.meta.env.DEV) {
    // Dev-only bench: drives frame() on a fake 60Hz clock and reports how many
    // frames actually render and what each costs (gl.finish included). Works in a
    // background tab, where rAF never fires.
    const gl = renderer.getContext();
    const r0 = renderer.render.bind(renderer);
    let renders = 0;
    let ms = 0;
    let timing = false;
    renderer.render = (a, b) => {
      const st = performance.now();
      r0(a, b);
      if (timing) gl.finish();
      ms += performance.now() - st;
      renders++;
    };
    (host as HTMLElement & { __dbg?: unknown }).__dbg = {
      bench(seconds = 3, hz = 60, warm = 330) {
        const was = [tabVisible, onScreen];
        tabVisible = onScreen = true;
        let now = performance.now();
        for (let i = 0; i < warm; i++) {
          cancelAnimationFrame(raf);
          raf = 0;
          frame((now += 1000 / hz));
        }
        renders = 0;
        ms = 0;
        timing = true;
        const wall0 = performance.now();
        for (let i = 0; i < seconds * hz; i++) {
          cancelAnimationFrame(raf);
          raf = 0;
          frame((now += 1000 / hz));
        }
        timing = false;
        const wall = performance.now() - wall0;
        [tabVisible, onScreen] = was;
        // hand the clock back to real time
        t0 -= now - performance.now();
        prev = lastRender = performance.now();
        return { rendersPerSec: renders / seconds, msPerRender: +(ms / Math.max(1, renders)).toFixed(2), cpuMsPerSec: +(wall / seconds).toFixed(1), keepsLooping: raf !== 0, bubbles: talk.count };
      },
      state: () => ({ tabVisible, onScreen, raf, introDone, wasAnimating, hot, renders, dbgAnimating, prev, lastRender, theta, thetaGoal, spin, walls: Object.values(walls).map((w) => [w.level, w.target]), lifts: Object.values(spotObjects).map((o) => o.lift), guests: guests.map((g) => g.phase), bubbles: talk.count }),
      sasha: () => onScreenHead(sasha),
      theta: (a: number) => {
        thetaGoal = a;
        invalidate();
      },
    };
  }

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    io.disconnect();
    document.removeEventListener('visibilitychange', onVis);
    talk.destroy();
    renderer.dispose();
  };
}
