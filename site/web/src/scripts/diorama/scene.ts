// The café as a model you can turn round. Built from the owner's photos: a long
// room with the counter at the back under two TV menus, the drinks fridge beside
// it, white tables with velvet cushions, the white pillar with the chess table,
// the toilet door, and the front window carrying the logo.
//
// Four objects are doors into the site: the counter (menu), the round table
// (booking), the A-frame board by the entrance (visit) and the picture frames
// (Instagram). Each has an HTML link pinned over it, so the page works by
// keyboard and for crawlers; the 3D is the enhancement, never the only way in.
//
// Turning: drag, or the two buttons. Whichever walls stand between the camera and
// the room drop to a stub, Sims-style, so the room is always open to view.
//
// People: customers come in through the front door, queue at the counter, carry
// a cup to a free chair, sit, and leave. Off under reduced motion.
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
  cakeCase,
  chair,
  cup,
  cyl,
  espressoMachine,
  ficus,
  gameShelf,
  glass,
  grinder,
  kraftBag,
  mat,
  person,
  pillar,
  placemat,
  rbox,
  roundTable,
  shadowed,
  smallPlant,
  squareTable,
} from './props';

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
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.NeutralToneMapping; // keeps brand hues honest
  renderer.toneMappingExposure = 1.02;
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;

  const scene = new THREE.Scene();
  const pmrem = new THREE.PMREMGenerator(renderer);
  scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
  scene.environmentIntensity = 0.32;

  const root = new THREE.Group();
  scene.add(root);

  // Intro choreography: [object, delay seconds, kind]
  const intro: Array<{ o: THREE.Object3D; d: number; kind: 'drop' | 'rise' }> = [];
  const drop = <O extends THREE.Object3D>(o: O, d: number): O => (intro.push({ o, d, kind: 'drop' }), o);

  // Seats people can take: where the seat is, which way it faces, and a spot
  // beside it to walk to first.
  type Seat = { pos: THREE.Vector3; face: number; approach: THREE.Vector3; taken: boolean };
  const seats: Seat[] = [];
  const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);
  function seatChair(parent: THREE.Group, x: number, z: number, ry: number, cushion?: string) {
    parent.add(at(chair(cushion), x, 0, z, ry));
    const wx = parent.position.x + x;
    const wz = parent.position.z + z;
    const side = v3(Math.cos(ry), 0, -Math.sin(ry)); // chair's local +x
    seats.push({ pos: v3(wx, 0, wz), face: ry, approach: v3(wx + side.x * 0.45, 0, wz + side.z * 0.45), taken: false });
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
  const zBack = -D2; // inner face
  const counter = new THREE.Group();
  const cx = 1.55;
  const cz = -2.95;
  counter.add(at(rbox(3.4, 0.98, 0.64, mat('#1f1c19', 0.6), 0.03), cx, 0.49, cz));
  counter.add(at(rbox(3.42, 0.3, 0.02, mat('#3a3430', 0.25, 0.1), 0.01), cx, 0.82, cz + 0.325));
  const slats = new THREE.InstancedMesh(new THREE.BoxGeometry(0.035, 0.62, 0.03), mat(C.oak, 0.55), 42);
  const m4 = new THREE.Matrix4();
  for (let i = 0; i < 42; i++) slats.setMatrixAt(i, m4.makeTranslation(cx - 1.64 + i * 0.08, 0.35, cz + 0.33));
  counter.add(slats);
  counter.add(at(rbox(3.5, 0.05, 0.74, mat(C.white, 0.35), 0.015), cx, 1.005, cz + 0.02));
  counter.add(at(espressoMachine(), 2.05, 1.03, cz - 0.02));
  counter.add(at(grinder(), 2.85, 1.03, cz - 0.05));
  counter.add(at(cakeCase(), 0.55, 1.03, cz + 0.02));
  counter.add(at(rbox(0.22, 0.16, 0.02, mat(C.ink, 0.3), 0.01), 1.3, 1.14, cz + 0.05, -0.4));
  counter.add(at(smallPlant(), 3.1, 1.03, cz + 0.15));
  counter.add(at(rbox(3.4, 0.92, 0.46, mat('#1f1c19', 0.6), 0.03), cx, 0.46, zBack + 0.24));
  counter.add(at(rbox(3.45, 0.05, 0.5, mat(C.white, 0.35), 0.015), cx, 0.945, zBack + 0.24));
  for (let i = 0; i < 6; i++) counter.add(at(glass(i % 2 ? C.caramel : '#d9c7a8', 0.12), 0.3 + i * 0.13, 0.97, zBack + 0.3));
  const half = Math.ceil(opts.menuRows.length / 2);
  const screens: Array<[string, Array<[string, string]>, number]> = [
    ['Signatures', opts.menuRows.slice(0, half), cx - 0.64],
    ['Coffee', opts.menuRows.slice(half), cx + 0.64],
  ];
  for (const [title, rows, x] of screens) {
    const tv = new THREE.Group();
    tv.add(at(rbox(1.22, 0.71, 0.05, mat(C.ink, 0.35, 0.2), 0.015), 0, 0, 0));
    const scr = new THREE.Mesh(new THREE.PlaneGeometry(1.16, 0.65), new THREE.MeshBasicMaterial({ map: T.menuScreen(title, rows), toneMapped: false }));
    scr.position.z = 0.027;
    tv.add(scr);
    tv.position.set(x, 2.25, zBack + 0.07);
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
  fridge.add(at(rbox(0.5, 0.12, 0.02, mat('#dfeff4', 0.3), 0.01), 0, 1.86, 0.305));
  const board = new THREE.Mesh(new THREE.PlaneGeometry(0.48, 0.96), new THREE.MeshStandardMaterial({ map: T.pricesBoard(), roughness: 0.95 }));
  board.position.set(-0.302, 1.3, 0);
  board.rotation.y = -Math.PI / 2;
  fridge.add(board);
  root.add(shadowed(drop(at(fridge, -0.75, 0, zBack + 0.35), 0.55)));

  // The white tree painted on the wall, back-left, and a rubber plant beneath.
  const tree = new THREE.Mesh(
    new THREE.PlaneGeometry(1.6, 2.0),
    new THREE.MeshStandardMaterial({ map: T.treeMural(), transparent: true, roughness: 0.95, depthWrite: false }),
  );
  tree.position.set(-2.4, 2.02, zBack + 0.012);
  back.decor.add(tree);
  back.decor.add(at(ficus(1.5), -3.0, 0, zBack + 0.45));

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

  // Toilet door, back of the left wall.
  const loo = new THREE.Group();
  loo.add(at(rbox(0.08, 2.25, 1.05, darkWood, 0.02), xLeft + 0.03, 1.125, -3.0));
  loo.add(at(rbox(0.04, 2.1, 0.9, mat('#2e2119', 0.55), 0.015), xLeft + 0.07, 1.06, -3.0));
  loo.add(at(cyl(0.012, 0.012, 0.14, mat('#b89a62', 0.3, 0.9), 8), xLeft + 0.11, 1.0, -2.65));
  loo.add(at(rbox(0.02, 0.1, 0.4, mat(C.ink, 0.5), 0.01), xLeft + 0.02, 2.45, -3.0));
  left.decor.add(loo);

  // Frames (-> Instagram) with the owner's photos, and the gold stars.
  const frames = new THREE.Group();
  const loader = new THREE.TextureLoader();
  const photos: Array<[string, number, number]> = [
    ['/img/cafe/cat-print.webp', -1.75, 2.3],
    ['/img/cafe/blue-and-green-matcha.webp', -1.2, 2.05],
    ['/img/cafe/muffin-and-cake.webp', -1.75, 1.55],
  ];
  for (const [src, z, y] of photos) {
    const t = loader.load(src, () => invalidate());
    t.colorSpace = THREE.SRGBColorSpace;
    frames.add(at(rbox(0.035, 0.56, 0.4, darkWood, 0.01), xLeft + 0.02, y, z));
    const ph = new THREE.Mesh(new THREE.PlaneGeometry(0.33, 0.49), new THREE.MeshStandardMaterial({ map: t, roughness: 0.8 }));
    ph.position.set(xLeft + 0.04, y, z);
    ph.rotation.y = Math.PI / 2;
    frames.add(ph);
  }
  const starShape = new THREE.Shape();
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * TAU + Math.PI / 2;
    const rr = i % 2 ? 0.018 : 0.09;
    if (i) starShape.lineTo(Math.cos(a) * rr, Math.sin(a) * rr);
    else starShape.moveTo(Math.cos(a) * rr, Math.sin(a) * rr);
  }
  const starGeo = new THREE.ExtrudeGeometry(starShape, { depth: 0.01, bevelEnabled: false });
  for (const [z, y, sc] of [
    [-1.15, 2.72, 1],
    [-0.75, 1.6, 0.7],
    [-2.2, 1.95, 0.6],
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
  win.add(at(rbox(0.07, 2.4, 0.26, darkWood, 0.02), DOOR.a0, 1.2, fz));
  win.add(at(rbox(0.07, 2.4, 0.26, darkWood, 0.02), DOOR.a1, 1.2, fz));
  win.add(at(rbox(DOOR.a1 - DOOR.a0, 0.08, 0.26, darkWood, 0.02), (DOOR.a0 + DOOR.a1) / 2, 2.4, fz));

  // ---- right wall: board-game shelf ------------------------------------------------
  right.decor.add(at(gameShelf(), W2 - 0.25, 0, 1.55, Math.PI / 2));

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

  // ---- floor: tables ------------------------------------------------------------------
  const grey = '#8f9496';
  const green = '#6d8a72';
  // Two-seaters along the left wall.
  const wallTables: THREE.Group[] = [];
  for (const [z, cupColor] of [
    [-1.3, C.sage],
    [0.3, C.paper],
    [1.9, C.olive700],
  ] as const) {
    const g = new THREE.Group();
    g.position.set(-2.95, 0, z);
    g.add(squareTable(0.66));
    g.add(at(placemat(), 0, 0.77, 0));
    g.add(at(cup(cupColor), -0.05, 0.776, 0.05));
    seatChair(g, 0.58, 0, -Math.PI / 2, z > 0 ? green : grey);
    wallTables.push(g);
    root.add(shadowed(drop(g, 0.95 + wallTables.length * 0.07)));
  }
  // Round tables down the middle (the first is the booking hotspot).
  const rounds: THREE.Group[] = [];
  for (const [x, z] of [
    [-0.75, 0.1],
    [-0.75, 2.5],
  ] as const) {
    const g = new THREE.Group();
    g.position.set(x, 0, z);
    g.add(roundTable());
    seatChair(g, 0, -0.64, 0, grey);
    seatChair(g, 0.58, 0.32, (-2 * Math.PI) / 3, green);
    seatChair(g, -0.58, 0.32, (2 * Math.PI) / 3, grey);
    rounds.push(g);
    root.add(shadowed(drop(g, 1.05 + rounds.length * 0.08)));
  }
  const table = rounds[0];
  table.add(at(cup(C.sage), 0.12, 0.776, -0.08));
  const plate = new THREE.Group();
  plate.add(at(cyl(0.12, 0.1, 0.012, mat(C.paper, 0.4), 24), 0, 0.006, 0));
  const slice = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.08, 16, 1, false, 0, Math.PI / 3), mat('#c79363', 0.7));
  slice.position.y = 0.05;
  plate.add(slice);
  table.add(at(plate, -0.1, 0.776, 0.15));
  rounds[1].add(at(glass('#5b5fa8', 0.14), 0.1, 0.776, 0)); // blue matcha

  // Right side: pillar, chess table, a four-top.
  root.add(shadowed(drop(at(pillar(H - 0.02), W2 - 0.55, 0, 0.35), 0.9)));
  const chess = new THREE.Group();
  chess.position.set(2.7, 0, 1.65);
  chess.add(squareTable(0.7));
  const cb = new THREE.Mesh(new THREE.PlaneGeometry(0.44, 0.44), new THREE.MeshStandardMaterial({ map: T.chessboard(), roughness: 0.5 }));
  cb.rotation.x = -Math.PI / 2;
  cb.position.y = 0.771;
  chess.add(cb);
  for (let i = 0; i < 10; i++) {
    const white = i < 5;
    const pc = new THREE.Group();
    pc.add(at(cyl(0.012, 0.018, 0.035, mat(white ? C.white : C.ink, 0.4), 10), 0, 0.017, 0));
    pc.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.013, 10, 8), mat(white ? C.white : C.ink, 0.4)), 0, 0.045, 0));
    chess.add(at(pc, -0.19 + (i % 5) * 0.09, 0.771, white ? 0.16 : -0.16 + (i === 7 ? 0.1 : 0)));
  }
  seatChair(chess, 0, 0.62, Math.PI, grey);
  seatChair(chess, 0, -0.62, 0, green);
  root.add(shadowed(drop(chess, 1.2)));
  const four = new THREE.Group();
  four.position.set(2.6, 0, -1.25);
  four.add(squareTable(0.8));
  four.add(at(placemat(), -0.15, 0.77, 0));
  four.add(at(placemat(), 0.2, 0.77, 0.1));
  four.add(at(glass(C.caramel, 0.14), 0.2, 0.776, 0.1));
  seatChair(four, -0.66, 0, Math.PI / 2, grey);
  seatChair(four, 0.66, 0, -Math.PI / 2, green);
  seatChair(four, 0, 0.66, Math.PI, green);
  root.add(shadowed(drop(four, 1.25)));

  // Entrance: mat and the A-frame board (-> /visit).
  const entrance = new THREE.Group();
  entrance.add(at(rbox(1.0, 0.02, 0.7, mat('#8f7650', 1), 0.008), (DOOR.a0 + DOOR.a1) / 2, 0.01, D2 - 0.45));
  entrance.add(at(aFrame(T.aFrameBoard()), 0.2, 0, D2 - 0.55, -0.5));
  root.add(shadowed(drop(entrance, 0.8)));
  root.add(shadowed(drop(at(ficus(1.2), -3.05, 0, 3.55), 1.3)));

  // Kraft bag on the counter end -- the packaging from the brand book.
  T.svgCard('/brand/logo.svg', '#c9a57a').then((decal) => {
    const bag = kraftBag(decal);
    bag.position.set(0.05, 1.03, -2.8);
    bag.rotation.y = 0.35;
    root.add(shadowed(bag));
    invalidate();
  });

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
  const barista = person('#ece7da', '#e2b594', '#3b2a1e');
  barista.group.position.set(1.15, 0, -3.45);
  root.add(barista.group);

  const looks: Array<[string, string, string]> = [
    [C.caramel, '#f0c7a4', '#5a3a24'],
    [C.olive700, '#c68d64', '#1d1a17'],
    ['#8f9aa6', '#f3d2b8', '#c9a15a'],
    [C.coffee, '#8a5a3c', '#1d1a17'],
    [C.sage, '#e9bf9c', '#7a4a2e'],
  ];
  type Phase = 'wait' | 'walk' | 'order' | 'sit' | 'leaving';
  interface Guest {
    p: ReturnType<typeof person>;
    phase: Phase;
    path: THREE.Vector3[];
    timer: number;
    seat: Seat | null;
    then: Phase;
  }
  const DOOR_IN = v3((DOOR.a0 + DOOR.a1) / 2, 0, D2 + 0.1);
  const AISLE_X = 1.1;
  const guests: Guest[] = [];
  const guestCount = opts.reducedMotion ? 0 : 4;
  for (let i = 0; i < guestCount; i++) {
    const [coat, skin, hair] = looks[i % looks.length];
    const p = person(coat, skin, hair);
    p.group.visible = false;
    root.add(p.group);
    guests.push({ p, phase: 'wait', path: [], timer: 0.5 + i * 3.4, seat: null, then: 'order' });
  }
  function sitPose(p: ReturnType<typeof person>, k: number) {
    p.legs.forEach((l) => (l.rotation.x = -k * 1.45));
    p.group.children[0].position.y = 0.72 - k * 0.2; // body
    p.group.children[1].position.y = 1.16 - k * 0.2; // head
    p.group.children[2].position.y = 1.17 - k * 0.2; // hair
    p.cup.position.y = 0.72 - k * 0.2;
  }
  // Under reduced motion a few people are simply sitting, so the room isn't empty.
  if (opts.reducedMotion) {
    [0, 3, 7].forEach((si, i) => {
      const s = seats[si];
      if (!s) return;
      const [coat, skin, hair] = looks[i];
      const p = person(coat, skin, hair);
      p.group.position.copy(s.pos);
      p.group.rotation.y = s.face;
      sitPose(p, 1);
      root.add(p.group);
    });
  }
  // Walk via the aisle so nobody cuts through a table.
  const routeTo = (from: THREE.Vector3, to: THREE.Vector3) => [v3(AISLE_X, 0, from.z), v3(AISLE_X, 0, to.z), to.clone()];
  const exitPath = (from: THREE.Vector3) => [...routeTo(from, v3(DOOR_IN.x, 0, D2 - 0.5)), DOOR_IN.clone()];
  let clockT = 0;

  function stepGuest(g: Guest, dt: number) {
    const G = g.p.group;
    if (g.phase === 'wait') {
      g.timer -= dt;
      if (g.timer > 0) return;
      G.visible = true;
      G.position.copy(DOOR_IN);
      sitPose(g.p, 0);
      g.p.cup.visible = false;
      const queued = guests.filter((o) => o !== g && o.then === 'order' && o.phase !== 'wait').length;
      const spot = v3(1.45 + queued * 0.08, 0, -2.2 + queued * 0.6);
      g.path = [v3(DOOR_IN.x, 0, D2 - 0.6), ...routeTo(v3(AISLE_X, 0, D2 - 0.6), spot)];
      g.phase = 'walk';
      g.then = 'order';
      return;
    }
    if (g.phase === 'walk' || g.phase === 'leaving') {
      const target = g.path[0];
      if (!target) {
        if (g.phase === 'leaving') {
          G.visible = false;
          g.phase = 'wait';
          g.then = 'order';
          g.timer = 3 + Math.random() * 5;
          return;
        }
        g.phase = g.then;
        if (g.phase === 'order') {
          g.timer = 2.4;
          G.rotation.y = Math.PI; // face the counter
        } else if (g.phase === 'sit' && g.seat) {
          G.position.copy(g.seat.pos);
          G.rotation.y = g.seat.face;
          g.timer = 9 + Math.random() * 7;
        }
        return;
      }
      const d = target.clone().sub(G.position);
      d.y = 0;
      const dist = d.length();
      if (dist < 0.02) {
        g.path.shift();
        return;
      }
      d.normalize();
      G.position.addScaledVector(d, Math.min(dist, 0.95 * dt));
      let dr = Math.atan2(d.x, d.z) - G.rotation.y;
      dr = ((((dr + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
      G.rotation.y += dr * Math.min(1, dt * 10);
      const swing = Math.sin(clockT * 9) * 0.5;
      g.p.legs[0].rotation.x = swing;
      g.p.legs[1].rotation.x = -swing;
      G.position.y = Math.abs(Math.sin(clockT * 9)) * 0.025;
      return;
    }
    if (g.phase === 'order') {
      g.timer -= dt;
      g.p.legs.forEach((l) => (l.rotation.x *= 0.8));
      G.position.y = 0;
      if (g.timer > 0) return;
      g.p.cup.visible = true;
      g.then = 'sit';
      const free = seats.filter((s) => !s.taken);
      const seat = free[Math.floor(Math.random() * free.length)];
      if (!seat) {
        g.path = exitPath(G.position);
        g.phase = 'leaving';
        return;
      }
      seat.taken = true;
      g.seat = seat;
      g.path = routeTo(G.position, seat.approach);
      g.phase = 'walk';
      return;
    }
    if (g.phase === 'sit') {
      g.timer -= dt;
      sitPose(g.p, 1);
      G.position.y = 0;
      if (g.timer > 0 || !g.seat) return;
      sitPose(g.p, 0);
      G.position.copy(g.seat.approach);
      g.seat.taken = false;
      g.seat = null;
      g.p.cup.visible = false;
      g.path = exitPath(G.position);
      g.phase = 'leaving';
    }
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
    const w = host.clientWidth;
    const h = host.clientHeight;
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
    menu: { obj: counter, anchor: v3(1.6, 1.9, -2.9), lift: 0 },
    book: { obj: table, anchor: v3(-0.75, 1.35, 0.1), lift: 0 },
    visit: { obj: entrance, anchor: v3(0.2, 1.2, D2 - 0.55), lift: 0 },
    instagram: { obj: frames, anchor: v3(-W2 + 0.05, 2.75, -1.5), wall: 'left', lift: 0 },
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
    const hits = ray.intersectObjects(objs, true);
    if (!hits.length) return null;
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
    const w = host.clientWidth;
    const h = host.clientHeight;
    for (const [k, s] of Object.entries(spotObjects)) {
      const el = pins.get(k);
      if (!el) continue;
      el.classList.toggle('is-away', !!s.wall && !walls[s.wall].decor.visible);
      v.copy(s.anchor);
      root.localToWorld(v);
      v.project(camera);
      el.style.transform = `translate3d(${((v.x + 1) / 2) * w}px, ${((1 - v.y) / 2) * h}px, 0)`;
    }
  }

  // ---- loop ------------------------------------------------------------------------------
  const ease = (t: number) => 1 + 2.2 * Math.pow(t - 1, 3) + 1.2 * Math.pow(t - 1, 2); // easeOutBack-ish
  const clamp01 = (t: number) => Math.min(1, Math.max(0, t));
  intro.forEach(({ o }) => o.userData.y0 === undefined && (o.userData.y0 = o.position.y));
  const WIRE_START = 1.55;
  const WIRE_DUR = 2.6;
  let t0 = -1;
  let prev = 0;
  let introDone = opts.reducedMotion;
  let visible = true;
  let raf = 0;
  function invalidate() {
    if (!raf && visible) raf = requestAnimationFrame(frame);
  }

  function applyIntro(t: number) {
    for (const { o, d, kind } of intro) {
      const k = clamp01((t - d) / 0.7);
      if (kind === 'rise') introScale.set(o, 1 - Math.pow(1 - k, 3));
      else {
        o.position.y = o.userData.y0 + (1 - ease(k)) * 1.6 * (k < 1 ? 1 : 0);
        o.visible = k > 0;
      }
    }
    lamps.forEach((l) => (l.intensity = 2.2 * clamp01((t - 1.3) / 0.6)));
    leds.emissiveIntensity = 3 * clamp01((t - 1.1) / 0.8);
    wireUniforms.uProgress.value = ((t - WIRE_START) / WIRE_DUR) * lineTotal;
  }

  function frame(now: number) {
    raf = 0;
    if (t0 < 0) t0 = prev = now;
    const t = (now - t0) / 1000;
    const dt = Math.min(0.05, (now - prev) / 1000);
    prev = now;
    clockT = t;
    let animating = false;

    if (!introDone) {
      applyIntro(t);
      if (t > WIRE_START + WIRE_DUR) {
        introDone = true;
        wireUniforms.uProgress.value = 1e6;
        introScale.clear();
        host.classList.add('is-ready');
        opts.onReady?.();
      }
      animating = true;
    }

    // Turning: follow the drag, glide on release, or ease to a button target.
    if (!dragging) {
      if (Math.abs(spin) > 0.02) {
        theta += spin * dt;
        thetaGoal = theta;
        spin *= Math.pow(0.04, dt);
        animating = true;
      } else if (Math.abs(thetaGoal - theta) > 0.001) {
        theta += (thetaGoal - theta) * (opts.reducedMotion ? 1 : Math.min(1, dt * 7));
        animating = true;
      }
    }
    placeCamera();
    updateWalls(dt, opts.reducedMotion);
    if (Object.values(walls).some((w) => Math.abs(w.level - w.target) > 0.01)) animating = true;

    for (const [k, s] of Object.entries(spotObjects)) {
      const goal = hot === k && !opts.reducedMotion ? 0.06 : 0;
      s.lift += (goal - s.lift) * 0.18;
      if (introDone) s.obj.position.y = (s.obj.userData.y0 ?? 0) + s.lift;
      if (Math.abs(goal - s.lift) > 0.001) animating = true;
    }

    if (introDone) for (const g of guests) stepGuest(g, dt);
    if (!opts.reducedMotion) {
      // the barista works the line between the machine and the till
      barista.group.position.x = 1.15 + Math.sin(t * 0.5) * 0.35;
      barista.group.rotation.y = Math.sin(t * 0.5) * 0.3; // facing the customers
    }

    placePins();
    renderer.render(scene, camera);
    if ((animating || guests.length > 0) && visible) raf = requestAnimationFrame(frame);
  }

  if (opts.reducedMotion) {
    applyIntro(99);
    introScale.clear();
    host.classList.add('is-ready');
    opts.onReady?.();
  }

  const ro = new ResizeObserver(fit);
  ro.observe(host);
  const io = new IntersectionObserver(([e]) => {
    visible = e.isIntersecting;
    prev = performance.now();
    if (visible) invalidate();
  });
  io.observe(host);
  const onVis = () => {
    visible = document.visibilityState === 'visible';
    prev = performance.now();
    if (visible) invalidate();
  };
  document.addEventListener('visibilitychange', onVis);
  placeCamera();
  updateWalls(0, true);
  fit();

  return () => {
    cancelAnimationFrame(raf);
    ro.disconnect();
    io.disconnect();
    document.removeEventListener('visibilitychange', onVis);
    renderer.dispose();
  };
}
