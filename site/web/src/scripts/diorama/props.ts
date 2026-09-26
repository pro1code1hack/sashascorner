// Furniture and objects, built from primitives. Everything is soft-edged
// (RoundedBox, lathe profiles) so the room reads as a crafted model, not CAD.
import * as THREE from 'three';
import { RoundedBoxGeometry } from 'three/addons/geometries/RoundedBoxGeometry.js';
import { C } from './textures';

const matCache = new Map<string, THREE.MeshStandardMaterial>();
export function mat(color: string, roughness = 0.78, metalness = 0): THREE.MeshStandardMaterial {
  const key = `${color}|${roughness}|${metalness}`;
  let m = matCache.get(key);
  if (!m) {
    m = new THREE.MeshStandardMaterial({ color, roughness, metalness });
    matCache.set(key, m);
  }
  return m;
}

export function shadowed<T extends THREE.Object3D>(o: T, cast = true, receive = true): T {
  o.traverse((c) => {
    if ((c as THREE.Mesh).isMesh) {
      c.castShadow = cast;
      c.receiveShadow = receive;
    }
  });
  return o;
}

export function rbox(
  w: number,
  h: number,
  d: number,
  material: THREE.Material,
  r = Math.min(w, h, d) * 0.18,
): THREE.Mesh {
  const g = new RoundedBoxGeometry(w, h, d, 3, Math.min(r, Math.min(w, h, d) / 2 - 1e-4));
  return new THREE.Mesh(g, material);
}

export function cyl(rt: number, rb: number, h: number, material: THREE.Material, seg = 28): THREE.Mesh {
  return new THREE.Mesh(new THREE.CylinderGeometry(rt, rb, h, seg), material);
}

export function at<T extends THREE.Object3D>(o: T, x: number, y: number, z: number, ry = 0): T {
  o.position.set(x, y, z);
  o.rotation.y = ry;
  return o;
}

// ---------------------------------------------------------------------------

/** White wooden café chair with a velvet seat pad and back cushion, as in the café. */
export function chair(cushion: string = '#8f9496'): THREE.Group {
  const g = new THREE.Group();
  const wood = mat(C.white, 0.55);
  const pad = mat(cushion, 0.95);
  g.add(at(rbox(0.42, 0.05, 0.42, wood, 0.02), 0, 0.46, 0));
  for (const [x, z] of [
    [-0.17, -0.17],
    [0.17, -0.17],
    [-0.17, 0.17],
    [0.17, 0.17],
  ] as const) {
    g.add(at(rbox(0.04, 0.46, 0.04, wood, 0.01), x, 0.23, z));
  }
  g.add(at(rbox(0.04, 0.5, 0.04, wood, 0.01), -0.17, 0.72, -0.18));
  g.add(at(rbox(0.04, 0.5, 0.04, wood, 0.01), 0.17, 0.72, -0.18));
  g.add(at(rbox(0.38, 0.1, 0.03, wood, 0.012), 0, 0.9, -0.18));
  g.add(at(rbox(0.38, 0.07, 0.38, pad, 0.03), 0, 0.515, 0.01));
  g.add(at(rbox(0.34, 0.26, 0.06, pad, 0.03), 0, 0.68, -0.14));
  return g;
}

/** Round green woven placemat. */
export function placemat(): THREE.Mesh {
  return cyl(0.17, 0.17, 0.006, mat('#3f5a45', 1), 28);
}

export function roundTable(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.46, 0.46, 0.045, mat(C.white, 0.45), 40), 0, 0.745, 0));
  g.add(at(cyl(0.035, 0.035, 0.72, mat(C.white, 0.45), 12), 0, 0.37, 0));
  g.add(at(cyl(0.22, 0.25, 0.03, mat(C.white, 0.45), 24), 0, 0.015, 0));
  g.add(at(placemat(), 0, 0.77, 0));
  return g;
}

export function squareTable(w = 0.62): THREE.Group {
  const g = new THREE.Group();
  g.add(at(rbox(w, 0.045, w, mat(C.white, 0.45), 0.015), 0, 0.745, 0));
  const leg = mat(C.white, 0.45);
  const o = w / 2 - 0.05;
  for (const [x, z] of [
    [-o, -o],
    [o, -o],
    [-o, o],
    [o, o],
  ] as const) {
    g.add(at(rbox(0.04, 0.72, 0.04, leg, 0.01), x, 0.36, z));
  }
  return g;
}

/** The brand cup: sage glaze, like the blob in the logo. */
export function cup(color: string = C.sage, withSaucer = true): THREE.Group {
  const g = new THREE.Group();
  const pts = [
    new THREE.Vector2(0.0, 0.0),
    new THREE.Vector2(0.03, 0.0),
    new THREE.Vector2(0.045, 0.02),
    new THREE.Vector2(0.052, 0.06),
    new THREE.Vector2(0.056, 0.085),
    new THREE.Vector2(0.05, 0.085),
    new THREE.Vector2(0.046, 0.06),
    new THREE.Vector2(0.0, 0.07),
  ];
  const glaze = new THREE.MeshStandardMaterial({ color, roughness: 0.35 });
  g.add(new THREE.Mesh(new THREE.LatheGeometry(pts, 24), glaze));
  const coffee = new THREE.Mesh(new THREE.CircleGeometry(0.047, 20), mat(C.coffee, 0.4));
  coffee.rotation.x = -Math.PI / 2;
  coffee.position.y = 0.074;
  g.add(coffee);
  const handle = new THREE.Mesh(new THREE.TorusGeometry(0.022, 0.006, 8, 16, Math.PI * 1.2), glaze);
  handle.position.set(0.058, 0.05, 0);
  handle.rotation.z = -Math.PI * 0.6;
  g.add(handle);
  if (withSaucer) g.add(at(cyl(0.085, 0.07, 0.012, glaze, 24), 0, 0.006, 0));
  return g;
}

export function glass(fill: string, h = 0.13): THREE.Group {
  const g = new THREE.Group();
  const drink = cyl(0.036, 0.03, h * 0.82, new THREE.MeshStandardMaterial({ color: fill, roughness: 0.3 }), 16);
  drink.position.y = h * 0.41;
  const shell = cyl(0.042, 0.035, h, new THREE.MeshStandardMaterial({
    color: '#ffffff',
    roughness: 0.05,
    transparent: true,
    opacity: 0.22,
  }), 18);
  shell.position.y = h / 2;
  g.add(drink, shell);
  return g;
}

export function pendant(cord: number): { group: THREE.Group; bulb: THREE.Mesh } {
  const g = new THREE.Group();
  g.add(at(cyl(0.006, 0.006, cord, mat(C.ink), 6), 0, -cord / 2, 0));
  const shadePts = [
    new THREE.Vector2(0.02, 0),
    new THREE.Vector2(0.05, -0.02),
    new THREE.Vector2(0.14, -0.14),
    new THREE.Vector2(0.17, -0.2),
  ];
  const shade = new THREE.Mesh(
    new THREE.LatheGeometry(shadePts, 32),
    new THREE.MeshStandardMaterial({ color: C.olive900, roughness: 0.6, side: THREE.DoubleSide }),
  );
  shade.position.y = -cord;
  g.add(shade);
  const bulb = new THREE.Mesh(
    new THREE.SphereGeometry(0.045, 16, 12),
    new THREE.MeshStandardMaterial({ color: '#fff3d6', emissive: '#ffc983', emissiveIntensity: 2.2 }),
  );
  bulb.position.y = -cord - 0.15;
  g.add(bulb);
  return { group: g, bulb };
}

/** Rubber plant -- the ficus from the brand book's mood page. */
export function ficus(height = 1.55): THREE.Group {
  const g = new THREE.Group();
  const potMat = mat(C.caramel, 0.85);
  g.add(at(cyl(0.2, 0.16, 0.36, potMat, 28), 0, 0.18, 0));
  g.add(at(cyl(0.205, 0.205, 0.04, potMat, 28), 0, 0.36, 0));
  g.add(at(cyl(0.18, 0.18, 0.02, mat('#4a3522', 1), 20), 0, 0.37, 0));
  const stem = mat('#5a4a32', 0.9);
  const leaf = new THREE.MeshStandardMaterial({ color: C.leaf, roughness: 0.42 });
  const leafGeo = new THREE.SphereGeometry(1, 14, 10);
  let seed = 11;
  const r = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  for (let s = 0; s < 3; s++) {
    const a = (s / 3) * Math.PI * 2 + 0.4;
    const top = height * (0.75 + r() * 0.25);
    const st = cyl(0.012, 0.018, top - 0.36, stem, 6);
    st.position.set(Math.cos(a) * 0.04, 0.36 + (top - 0.36) / 2, Math.sin(a) * 0.04);
    st.rotation.z = Math.cos(a) * 0.08;
    st.rotation.x = Math.sin(a) * 0.08;
    g.add(st);
    for (let k = 0; k < 7; k++) {
      const y = 0.62 + (k / 7) * (top - 0.6) + r() * 0.05;
      const la = a + k * 2.4 + r();
      const l = new THREE.Mesh(leafGeo, leaf);
      const len = 0.13 + r() * 0.05;
      l.scale.set(len, 0.012, len * 0.45);
      l.position.set(Math.cos(la) * (0.08 + len * 0.7), y, Math.sin(la) * (0.08 + len * 0.7));
      l.rotation.y = -la;
      l.rotation.z = 0.35 + r() * 0.4;
      g.add(l);
    }
  }
  return g;
}

export function smallPlant(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.07, 0.055, 0.11, mat(C.paper, 0.6), 18), 0, 0.055, 0));
  const leaf = new THREE.MeshStandardMaterial({ color: '#6f8452', roughness: 0.5 });
  const geo = new THREE.SphereGeometry(1, 10, 8);
  for (let i = 0; i < 9; i++) {
    const a = i * 2.39;
    const l = new THREE.Mesh(geo, leaf);
    l.scale.set(0.05, 0.012, 0.028);
    l.position.set(Math.cos(a) * 0.045, 0.13 + (i % 3) * 0.02, Math.sin(a) * 0.045);
    l.rotation.y = -a;
    l.rotation.z = 0.6;
    g.add(l);
  }
  return g;
}

export function espressoMachine(): THREE.Group {
  const g = new THREE.Group();
  const body = mat(C.paper, 0.35, 0.05);
  const chrome = mat(C.chrome, 0.18, 0.95);
  g.add(at(rbox(0.78, 0.44, 0.5, body, 0.05), 0, 0.22 + 0.06, 0));
  g.add(at(rbox(0.8, 0.06, 0.52, chrome, 0.02), 0, 0.03, 0));
  g.add(at(rbox(0.72, 0.02, 0.2, chrome, 0.008), 0, 0.075, 0.2));
  g.add(at(rbox(0.8, 0.03, 0.52, mat(C.olive900, 0.5), 0.01), 0, 0.515, 0));
  for (const x of [-0.19, 0.19]) {
    g.add(at(cyl(0.045, 0.05, 0.07, chrome, 16), x, 0.23, 0.27));
    const handle = cyl(0.014, 0.012, 0.18, mat(C.ink, 0.5), 8);
    handle.rotation.x = Math.PI / 2.3;
    handle.position.set(x, 0.19, 0.36);
    g.add(handle);
  }
  const wand = cyl(0.008, 0.008, 0.22, chrome, 8);
  wand.position.set(0.36, 0.2, 0.27);
  wand.rotation.z = 0.2;
  g.add(wand);
  for (let i = 0; i < 4; i++) {
    const cp = cup(i % 2 ? C.sage : C.paper, false);
    cp.scale.setScalar(0.8);
    g.add(at(cp, -0.27 + i * 0.18, 0.53, -0.08));
  }
  return g;
}

export function grinder(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(rbox(0.18, 0.34, 0.24, mat(C.ink, 0.45, 0.2), 0.03), 0, 0.17, 0));
  const hopper = cyl(0.1, 0.05, 0.18, new THREE.MeshStandardMaterial({
    color: '#fff',
    transparent: true,
    opacity: 0.25,
    roughness: 0.05,
  }), 18);
  hopper.position.y = 0.43;
  g.add(hopper);
  g.add(at(cyl(0.07, 0.045, 0.1, mat('#3b2616', 0.9), 14), 0, 0.39, 0));
  return g;
}

/** Glass cake case with a Kyiv cake on show. */
export function cakeCase(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(rbox(0.72, 0.03, 0.46, mat(C.white, 0.4), 0.01), 0, 0.015, 0));
  const glassMat = new THREE.MeshStandardMaterial({
    color: '#ffffff',
    roughness: 0.04,
    transparent: true,
    opacity: 0.16,
    depthWrite: false,
  });
  const case_ = rbox(0.7, 0.36, 0.44, glassMat, 0.02);
  case_.position.y = 0.23;
  case_.castShadow = false;
  g.add(case_);
  // Kyiv cake: meringue, buttercream, cocoa top, hazelnut crumb.
  const kyiv = new THREE.Group();
  const layers = ['#e8d6b5', '#c79363', '#e8d6b5', '#c79363', '#e8d6b5'];
  layers.forEach((c, i) => kyiv.add(at(cyl(0.11, 0.11, 0.022, mat(c, 0.7), 28), 0, 0.011 + i * 0.022, 0)));
  kyiv.add(at(cyl(0.112, 0.112, 0.012, mat('#5d3a22', 0.8), 28), 0, 0.116, 0));
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    kyiv.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.014, 8, 6), mat(C.paper, 0.5)), Math.cos(a) * 0.08, 0.13, Math.sin(a) * 0.08));
  }
  g.add(at(kyiv, -0.16, 0.05, 0));
  for (let i = 0; i < 3; i++) {
    const m = new THREE.Group();
    m.add(at(cyl(0.035, 0.028, 0.04, mat(C.caramel, 0.8), 14), 0, 0.02, 0));
    m.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.042, 12, 8, 0, Math.PI * 2, 0, Math.PI / 2), mat('#8a5a33', 0.8)), 0, 0.04, 0));
    g.add(at(m, 0.08 + (i % 2) * 0.09, 0.05, -0.1 + i * 0.1));
  }
  return g;
}

export function kraftBag(decal: THREE.Texture): THREE.Group {
  const g = new THREE.Group();
  const kraft = mat('#c9a57a', 0.95);
  const front = new THREE.MeshStandardMaterial({ map: decal, roughness: 0.95 });
  const box = new THREE.Mesh(new THREE.BoxGeometry(0.16, 0.22, 0.09), [kraft, kraft, kraft, kraft, front, kraft]);
  box.position.y = 0.11;
  g.add(box);
  const h = new THREE.Mesh(new THREE.TorusGeometry(0.035, 0.005, 6, 16, Math.PI), mat('#a88258', 0.9));
  h.position.set(0, 0.22, 0);
  g.add(h);
  return g;
}

/**
 * A little customer: capsule body, round head, two legs that swing when walking
 * and fold when sitting. Colours are passed in so a queue doesn't look cloned.
 */
export function person(coat: string, skin: string, hair: string): {
  group: THREE.Group;
  legs: THREE.Object3D[];
  cup: THREE.Object3D;
} {
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.15, 0.36, 6, 14), mat(coat, 0.85));
  body.position.y = 0.72;
  g.add(body);
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.115, 18, 14), mat(skin, 0.7));
  head.position.y = 1.16;
  g.add(head);
  const hairCap = new THREE.Mesh(new THREE.SphereGeometry(0.122, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2.1), mat(hair, 0.9));
  hairCap.position.y = 1.17;
  hairCap.rotation.x = -0.25;
  g.add(hairCap);
  const legs: THREE.Object3D[] = [];
  for (const x of [-0.065, 0.065]) {
    const pivot = new THREE.Group();
    pivot.position.set(x, 0.42, 0);
    const leg = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.3, 4, 8), mat('#2b2a26', 0.8));
    leg.position.y = -0.2;
    pivot.add(leg);
    g.add(pivot);
    legs.push(pivot);
  }
  const cupObj = cyl(0.035, 0.028, 0.08, mat(C.sage, 0.4), 12);
  cupObj.position.set(0.17, 0.72, 0.1);
  cupObj.visible = false;
  g.add(cupObj);
  return { group: shadowed(g, true, false), legs, cup: cupObj };
}

/** White square pillar with a dark panelled base, as in the café. */
export function pillar(h: number): THREE.Group {
  const g = new THREE.Group();
  g.add(at(rbox(0.5, h, 0.5, mat(C.white, 0.9), 0.01), 0, h / 2, 0));
  g.add(at(rbox(0.56, 1.0, 0.56, mat(C.darkWood, 0.6), 0.015), 0, 0.5, 0));
  g.add(at(rbox(0.6, 0.06, 0.6, mat(C.darkWood, 0.6), 0.015), 0, 1.02, 0));
  return g;
}

/** Galvanised shelf with board games, like the one by the pillar. */
export function gameShelf(): THREE.Group {
  const g = new THREE.Group();
  const steel = mat('#b9bbbd', 0.35, 0.8);
  for (const [x, z] of [
    [-0.3, -0.15],
    [0.3, -0.15],
    [-0.3, 0.15],
    [0.3, 0.15],
  ] as const)
    g.add(at(cyl(0.012, 0.012, 1.3, steel, 6), x, 0.65, z));
  const boxes = ['#c44b3b', '#e0c24a', '#3c6aa8', '#e6e2d6', '#6b8f5a'];
  for (let i = 0; i < 4; i++) {
    const y = 0.15 + i * 0.36;
    g.add(at(rbox(0.64, 0.02, 0.34, steel, 0.005), 0, y, 0));
    for (let k = 0; k < 2; k++)
      g.add(at(rbox(0.26, 0.06 + ((i + k) % 3) * 0.02, 0.24, mat(boxes[(i * 2 + k) % boxes.length], 0.7), 0.01), -0.15 + k * 0.3, y + 0.05, 0));
  }
  return g;
}

export function aFrame(board: THREE.Texture): THREE.Group {
  const g = new THREE.Group();
  const wood = mat(C.darkWood, 0.6);
  const face = new THREE.MeshStandardMaterial({ map: board, roughness: 0.95 });
  for (const s of [1, -1]) {
    const leaf = new THREE.Group();
    leaf.add(at(rbox(0.5, 0.8, 0.03, wood, 0.01), 0, 0.4, 0));
    const p = new THREE.Mesh(new THREE.PlaneGeometry(0.42, 0.66), face);
    p.position.set(0, 0.42, 0.017 * s);
    if (s < 0) p.rotation.y = Math.PI;
    leaf.add(p);
    leaf.rotation.x = 0.18 * s;
    leaf.position.z = 0.07 * s;
    g.add(leaf);
  }
  return g;
}

// ---- v2: the window bar, the big table and the counter clutter -------------------

/** Bar stool: velvet seat on a dark post with a brass footring. */
export function barStool(cushion: string): THREE.Group {
  const g = new THREE.Group();
  const frame = mat(C.darkWood, 0.5);
  g.add(at(cyl(0.2, 0.22, 0.03, frame, 24), 0, 0.015, 0));
  g.add(at(cyl(0.028, 0.028, 0.68, frame, 10), 0, 0.36, 0));
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.15, 0.012, 6, 24), mat('#b89a62', 0.3, 0.9));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.3;
  g.add(ring);
  g.add(at(cyl(0.18, 0.17, 0.07, mat(cushion, 0.95), 24), 0, 0.72, 0));
  return g;
}

/** The big round table in the middle of the room. */
export function bigRoundTable(r = 0.78): THREE.Group {
  const g = new THREE.Group();
  const white = mat(C.white, 0.45);
  g.add(at(cyl(r, r, 0.05, white, 56), 0, 0.745, 0));
  g.add(at(cyl(0.07, 0.07, 0.72, white, 14), 0, 0.37, 0));
  g.add(at(cyl(0.34, 0.38, 0.035, white, 28), 0, 0.018, 0));
  return g;
}

/** Till: a small tablet on a stand, and a card reader beside it. */
export function till(): THREE.Group {
  const g = new THREE.Group();
  const dark = mat(C.ink, 0.35, 0.2);
  g.add(at(cyl(0.05, 0.06, 0.02, dark, 16), 0, 0.01, 0));
  g.add(at(cyl(0.012, 0.012, 0.14, dark, 8), 0, 0.08, 0));
  const tab = rbox(0.26, 0.18, 0.015, dark, 0.008);
  tab.position.set(0, 0.2, 0.01);
  tab.rotation.x = -0.45;
  g.add(tab);
  const screen = new THREE.Mesh(new THREE.PlaneGeometry(0.23, 0.15), new THREE.MeshBasicMaterial({ color: '#dfe7e0', toneMapped: false }));
  screen.position.set(0, 0.2, 0.019);
  screen.rotation.x = -0.45;
  g.add(screen);
  const reader = rbox(0.07, 0.03, 0.12, mat('#2a2a28', 0.4), 0.01);
  reader.position.set(0.22, 0.015, 0.08);
  reader.rotation.y = -0.3;
  g.add(reader);
  return g;
}

/** A stack of takeaway cups, upside down. */
export function cupStack(color: string, n = 7): THREE.Group {
  const g = new THREE.Group();
  const m = mat(color, 0.6);
  for (let i = 0; i < n; i++) g.add(at(cyl(0.04, 0.05, 0.1, m, 14), 0, 0.05 + i * 0.022, 0));
  return g;
}

/** Glass jar with a lid, holding biscuits, beans or boba. */
export function jar(fill: string, h = 0.2): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.065, 0.065, h * 0.62, mat(fill, 0.8), 16), 0, h * 0.31, 0));
  g.add(at(cyl(0.07, 0.07, h, new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.05, transparent: true, opacity: 0.2 }), 18), 0, h / 2, 0));
  g.add(at(cyl(0.072, 0.072, 0.025, mat(C.darkWood, 0.6), 18), 0, h + 0.012, 0));
  return g;
}

/** A row of syrup bottles with pumps. */
export function syrups(colors: string[]): THREE.Group {
  const g = new THREE.Group();
  const pump = mat(C.ink, 0.4);
  colors.forEach((c, i) => {
    const b = new THREE.Group();
    b.add(at(cyl(0.035, 0.035, 0.22, mat(c, 0.3), 12), 0, 0.11, 0));
    b.add(at(cyl(0.014, 0.03, 0.05, mat(c, 0.3), 10), 0, 0.245, 0));
    b.add(at(cyl(0.008, 0.008, 0.07, pump, 6), 0, 0.3, 0));
    b.add(at(rbox(0.05, 0.015, 0.02, pump, 0.005), 0.015, 0.34, 0));
    g.add(at(b, i * 0.085, 0, 0));
  });
  return g;
}

export function milkJug(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.045, 0.055, 0.12, mat(C.chrome, 0.2, 0.95), 16), 0, 0.06, 0));
  const spout = cyl(0.004, 0.02, 0.04, mat(C.chrome, 0.2, 0.95), 8);
  spout.position.set(0.05, 0.11, 0);
  spout.rotation.z = -0.9;
  g.add(spout);
  return g;
}

/** A cake stand under a dome, with slices set out. */
export function cakeStand(): THREE.Group {
  const g = new THREE.Group();
  const white = mat(C.white, 0.4);
  g.add(at(cyl(0.06, 0.08, 0.1, white, 16), 0, 0.05, 0));
  g.add(at(cyl(0.17, 0.17, 0.015, white, 28), 0, 0.105, 0));
  const slice = (color: string, top: string, a: number) => {
    const s = new THREE.Group();
    s.add(at(new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.06, 10, 1, false, 0, Math.PI / 4), mat(color, 0.7)), 0, 0.03, 0));
    s.add(at(new THREE.Mesh(new THREE.CylinderGeometry(0.1, 0.1, 0.01, 10, 1, false, 0, Math.PI / 4), mat(top, 0.7)), 0, 0.064, 0));
    s.rotation.y = a;
    return s;
  };
  g.add(at(slice('#e8d6b5', '#5d3a22', 0), 0, 0.113, 0));
  g.add(at(slice('#f0e2c8', '#c96f6a', 2.1), 0, 0.113, 0));
  g.add(at(slice('#d9c38f', '#8fa05a', 4.2), 0, 0.113, 0));
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(0.17, 20, 10, 0, Math.PI * 2, 0, Math.PI / 2),
    new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.04, transparent: true, opacity: 0.16, depthWrite: false }),
  );
  dome.position.y = 0.113;
  dome.scale.y = 0.9;
  g.add(dome);
  return g;
}

// ---- 2026-09-26: the right-hand side, from the owner's photos ----------------------

/** White spindle-back chair on light-wood legs, as along the mural wall. */
export function spindleChair(): THREE.Group {
  const g = new THREE.Group();
  const white = mat(C.white, 0.5);
  const oak = mat(C.oak, 0.55);
  g.add(at(rbox(0.42, 0.05, 0.4, white, 0.02), 0, 0.46, 0));
  for (const [x, z] of [
    [-0.16, -0.15],
    [0.16, -0.15],
    [-0.16, 0.15],
    [0.16, 0.15],
  ] as const) {
    const leg = cyl(0.018, 0.014, 0.45, oak, 8);
    leg.position.set(x, 0.225, z);
    leg.rotation.x = z > 0 ? 0.06 : -0.06;
    g.add(leg);
  }
  for (let i = 0; i < 5; i++) g.add(at(cyl(0.009, 0.009, 0.42, white, 6), -0.14 + i * 0.07, 0.69, -0.18));
  g.add(at(rbox(0.4, 0.05, 0.035, white, 0.015), 0, 0.92, -0.18));
  return g;
}

/** Tall dracaena: a fan of long, spiky leaves out of a grey pot. */
export function dracaena(height = 1.7): THREE.Group {
  const g = new THREE.Group();
  const pot = mat('#9a9c98', 0.8);
  g.add(at(cyl(0.2, 0.16, 0.4, pot, 20), 0, 0.2, 0));
  g.add(at(cyl(0.18, 0.18, 0.02, mat('#3a2a1c', 1), 16), 0, 0.39, 0));
  g.add(at(cyl(0.02, 0.03, height * 0.45, mat('#6b5a3e', 0.9), 6), 0, 0.4 + height * 0.22, 0));
  const leaf = mat('#3f5a34', 0.6);
  const geo = new THREE.ConeGeometry(0.02, 1, 4);
  let seed = 5;
  const r = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  const top = 0.4 + height * 0.45;
  for (let i = 0; i < 26; i++) {
    const a = i * 2.39;
    const tilt = 0.25 + r() * 0.9;
    const len = 0.45 + r() * 0.45;
    const l = new THREE.Mesh(geo, leaf);
    l.scale.set(1, len, 0.35);
    // lean out from the crown along (a, tilt)
    l.position.set(Math.cos(a) * Math.sin(tilt) * len * 0.5, top + Math.cos(tilt) * len * 0.5, Math.sin(a) * Math.sin(tilt) * len * 0.5);
    l.rotation.set(0, -a, 0);
    l.rotateZ(-tilt);
    g.add(l);
  }
  return g;
}

/** Black bentwood coat stand with curled hooks. */
export function coatStand(): THREE.Group {
  const g = new THREE.Group();
  const black = mat('#141412', 0.45);
  g.add(at(cyl(0.025, 0.03, 1.8, black, 10), 0, 0.9, 0));
  for (let k = 0; k < 4; k++) {
    const a = (k / 4) * Math.PI * 2 + 0.4;
    const foot = new THREE.Mesh(new THREE.TorusGeometry(0.18, 0.015, 6, 12, Math.PI / 2), black);
    foot.position.set(Math.cos(a) * 0.18, 0.18, Math.sin(a) * 0.18);
    foot.rotation.y = -a;
    g.add(foot);
    const hook = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.014, 6, 14, Math.PI * 1.3), black);
    hook.position.set(Math.cos(a) * 0.1, 1.68, Math.sin(a) * 0.1);
    hook.rotation.y = -a;
    hook.rotation.z = -0.4;
    g.add(hook);
  }
  return g;
}

/** White stair banister: a newel post and a sloping handrail over spindles. */
export function banister(len = 0.9): THREE.Group {
  const g = new THREE.Group();
  const white = mat(C.white, 0.45);
  g.add(at(rbox(0.08, 1.05, 0.08, white, 0.01), 0, 0.525, 0));
  const rail = rbox(0.06, 0.05, len, white, 0.015);
  rail.position.set(0, 0.95, len / 2);
  g.add(rail);
  const n = Math.max(3, Math.round(len / 0.12));
  for (let i = 1; i <= n; i++) g.add(at(rbox(0.025, 0.9, 0.025, white, 0.005), 0, 0.47, (i / (n + 1)) * len));
  return g;
}

/** A stack of board-game boxes, slightly askew. */
export function gameStack(): THREE.Group {
  const g = new THREE.Group();
  const boxes: Array<[string, number, number, number]> = [
    ['#e6e2d6', 0.26, 0.2, 0.05],
    ['#3c3a8a', 0.24, 0.18, 0.04],
    ['#c44b3b', 0.25, 0.19, 0.05],
    ['#141412', 0.2, 0.16, 0.04],
    ['#e0c24a', 0.22, 0.17, 0.035],
  ];
  let y = 0;
  boxes.forEach(([c, w, d, h], i) => {
    g.add(at(rbox(w, h, d, mat(c, 0.7), 0.006), (i % 2 ? 0.01 : -0.01), y + h / 2, 0, (i - 2) * 0.08));
    y += h;
  });
  return g;
}

/** A Halloween paper lantern: orange, ribbed, with a face. */
export function pumpkin(r = 0.12): THREE.Group {
  const g = new THREE.Group();
  const orange = mat('#e07a2a', 0.8);
  for (let k = 0; k < 6; k++) {
    const s = new THREE.Mesh(new THREE.SphereGeometry(r, 12, 10), orange);
    s.scale.set(0.55, 0.85, 1);
    s.position.set(Math.cos((k / 6) * Math.PI * 2) * r * 0.45, r * 0.85, Math.sin((k / 6) * Math.PI * 2) * r * 0.45);
    s.rotation.y = -(k / 6) * Math.PI * 2;
    g.add(s);
  }
  const face = mat('#1b1712', 0.8);
  for (const dx of [-0.35, 0.35]) g.add(at(rbox(r * 0.25, r * 0.22, 0.01, face, 0.002), dx * r, r * 1.05, r * 0.98));
  g.add(at(rbox(r * 0.8, r * 0.14, 0.01, face, 0.002), 0, r * 0.62, r * 0.99));
  g.add(at(cyl(0.01, 0.012, 0.05, mat('#3a2a1c', 0.8), 6), 0, r * 1.7, 0));
  return g;
}

/** A hanging pot with trailing strands, off a wall bracket. */
export function hangingPlant(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.08, 0.06, 0.1, mat(C.ink, 0.5), 12), 0, 0, 0));
  const leaf = mat('#5c7a3c', 0.6);
  const geo = new THREE.SphereGeometry(0.03, 6, 5);
  for (let s = 0; s < 7; s++) {
    const a = (s / 7) * Math.PI * 2;
    const len = 5 + (s % 3) * 3;
    for (let k = 0; k < len; k++) {
      const l = new THREE.Mesh(geo, leaf);
      l.scale.set(1, 0.6, 0.6);
      l.position.set(Math.cos(a) * 0.07, 0.04 - k * 0.055, Math.sin(a) * 0.07 * 0.5);
      g.add(l);
    }
  }
  return g;
}

/** The acrylic tiered pastry display on the counter, with chalk price tags. */
export function pastryDisplay(tag: THREE.Texture): THREE.Group {
  const g = new THREE.Group();
  const acrylic = new THREE.MeshStandardMaterial({ color: '#e8f0f2', roughness: 0.05, transparent: true, opacity: 0.28, depthWrite: false });
  const W = 0.42;
  const D = 0.34;
  g.add(at(rbox(W, 0.62, D, acrylic, 0.01), 0, 0.31, 0));
  const tagMat = new THREE.MeshStandardMaterial({ map: tag, roughness: 0.9 });
  const pastries = ['#c98f4f', '#d9b27a', '#b5733c', '#e2c28c'];
  for (let i = 0; i < 3; i++) {
    const y = 0.05 + i * 0.19;
    g.add(at(rbox(W - 0.02, 0.01, D - 0.02, mat('#b98c60', 0.6), 0.004), 0, y, 0));
    for (let k = 0; k < 3; k++) {
      const p = new THREE.Mesh(new THREE.SphereGeometry(0.045, 10, 8), mat(pastries[(i + k) % 4], 0.8));
      p.scale.set(1.3, 0.55, 0.9);
      p.position.set(-0.12 + k * 0.12, y + 0.03, -0.02);
      g.add(p);
    }
    const t = new THREE.Mesh(new THREE.PlaneGeometry(0.09, 0.05), tagMat);
    t.position.set(-0.1 + (i % 2) * 0.18, y + 0.06, D / 2 + 0.005);
    g.add(t);
  }
  return g;
}

/** Glass-box cake showcase: whole cakes on stands above, slices on the shelf below. */
export function cakeShowcase(): THREE.Group {
  const g = new THREE.Group();
  const W = 0.78;
  const D = 0.44;
  const Hh = 0.46;
  g.add(at(rbox(W, 0.04, D, mat(C.white, 0.4), 0.01), 0, 0.02, 0));
  const glassMat = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.04, transparent: true, opacity: 0.14, depthWrite: false });
  const box = rbox(W - 0.01, Hh, D - 0.01, glassMat, 0.012);
  box.position.y = 0.04 + Hh / 2;
  box.castShadow = false;
  g.add(box);
  const edge = mat('#c9b27a', 0.3, 0.8);
  for (const [x, z] of [
    [-W / 2, -D / 2],
    [W / 2, -D / 2],
    [-W / 2, D / 2],
    [W / 2, D / 2],
  ] as const)
    g.add(at(rbox(0.012, Hh, 0.012, edge, 0.003), x, 0.04 + Hh / 2, z));
  g.add(at(rbox(W, 0.012, D, edge, 0.003), 0, 0.04 + Hh, 0));
  // middle glass shelf
  g.add(at(rbox(W - 0.03, 0.008, D - 0.03, glassMat, 0.002), 0, 0.22, 0));
  const stand = (y: number) => {
    const s = new THREE.Group();
    s.add(at(cyl(0.02, 0.035, 0.05, mat(C.white, 0.3), 12), 0, 0.025, 0));
    s.add(at(cyl(0.1, 0.1, 0.008, mat(C.white, 0.3), 24), 0, 0.054, 0));
    s.position.y = y;
    return s;
  };
  const cake = (layers: string[], top: string, r = 0.085) => {
    const k = new THREE.Group();
    layers.forEach((c, i) => k.add(at(cyl(r, r, 0.024, mat(c, 0.7), 24), 0, 0.012 + i * 0.024, 0)));
    const ht = layers.length * 0.024;
    k.add(at(cyl(r + 0.002, r + 0.002, 0.012, mat(top, 0.6), 24), 0, ht + 0.006, 0));
    for (let i = 0; i < 6; i++) {
      const a = (i / 6) * Math.PI * 2;
      k.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.012, 8, 6), mat(i % 2 ? '#c44b5b' : C.paper, 0.5)), Math.cos(a) * r * 0.7, ht + 0.02, Math.sin(a) * r * 0.7));
    }
    return k;
  };
  // top: three whole cakes on stands (Kyiv, raspberry, lemon)
  const tops: Array<[string[], string]> = [
    [['#e8d6b5', '#c79363', '#e8d6b5', '#c79363'], '#5d3a22'],
    [['#f3d9de', '#e79aa8', '#f3d9de'], '#d45a74'],
    [['#f6ecc2', '#e9cf6a', '#f6ecc2'], '#f4e7a8'],
  ];
  tops.forEach(([l, t], i) => {
    const s = stand(0.228);
    s.add(at(cake(l, t), 0, 0.058, 0));
    g.add(at(s, -0.25 + i * 0.25, 0, 0));
  });
  // bottom: slices in a row, varied
  const sliceCols: Array<[string, string]> = [
    ['#4a2c1c', '#6b3f26'],
    ['#e8d6b5', '#8fa05a'],
    ['#f3d9de', '#d45a74'],
    ['#efe3c8', '#c98f4f'],
    ['#4a2c1c', '#e8d6b5'],
  ];
  sliceCols.forEach(([body, top], i) => {
    const sl = new THREE.Group();
    const w = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.06, 12, 1, false, 0, Math.PI / 4), mat(body, 0.7));
    w.position.y = 0.03;
    sl.add(w);
    const tp = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.01, 12, 1, false, 0, Math.PI / 4), mat(top, 0.6));
    tp.position.y = 0.065;
    sl.add(tp);
    sl.add(at(cyl(0.05, 0.05, 0.006, mat(C.paper, 0.4), 16), 0.02, 0.003, 0.02));
    g.add(at(sl, -0.3 + i * 0.15, 0.045, 0.02, 2.2 + i * 0.3));
  });
  return g;
}

/** An open laptop: thin dark base and an angled lid with a lit screen. */
export function laptop(): THREE.Group {
  const g = new THREE.Group();
  const shell = mat('#3a3b3e', 0.35, 0.6);
  g.add(at(rbox(0.3, 0.012, 0.21, shell, 0.004), 0, 0.006, 0));
  const lid = new THREE.Group();
  lid.add(at(rbox(0.3, 0.2, 0.008, shell, 0.003), 0, 0.1, 0));
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(0.27, 0.17), new THREE.MeshBasicMaterial({ color: '#cfe0f0', toneMapped: false }));
  scr.position.set(0, 0.1, -0.0045);
  scr.rotation.y = Math.PI;
  lid.add(scr);
  lid.position.set(0, 0.012, 0.105);
  lid.rotation.x = -0.28; // leaning back, away from the person
  g.add(lid);
  return g;
}

/** Glass jar of cookies with a wooden lid. */
export function cookieJar(): THREE.Group {
  const g = new THREE.Group();
  const glassMat = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.05, transparent: true, opacity: 0.22, depthWrite: false });
  g.add(at(cyl(0.075, 0.075, 0.2, glassMat, 18), 0, 0.1, 0));
  g.add(at(cyl(0.078, 0.078, 0.03, mat(C.oak, 0.6), 18), 0, 0.215, 0));
  for (let i = 0; i < 6; i++) g.add(at(cyl(0.05, 0.05, 0.014, mat(i % 2 ? '#c98f4f' : '#8a5a33', 0.8), 14), 0, 0.02 + i * 0.028, 0));
  return g;
}

/** Tip jar: a small glass with a paper label and a few coins. */
export function tipJar(): THREE.Group {
  const g = new THREE.Group();
  const glassMat = new THREE.MeshStandardMaterial({ color: '#ffffff', roughness: 0.05, transparent: true, opacity: 0.25, depthWrite: false });
  g.add(at(cyl(0.045, 0.04, 0.12, glassMat, 14), 0, 0.06, 0));
  g.add(at(cyl(0.036, 0.036, 0.02, mat('#c9a44c', 0.3, 0.8), 12), 0, 0.012, 0));
  g.add(at(rbox(0.06, 0.035, 0.004, mat(C.paper, 0.6), 0.001), 0, 0.07, 0.044));
  return g;
}

/** A small vase of flowers. */
export function flowers(): THREE.Group {
  const g = new THREE.Group();
  g.add(at(cyl(0.04, 0.03, 0.1, mat('#6d8a72', 0.4), 12), 0, 0.05, 0));
  const stem = mat('#4b5a36', 0.8);
  const petals = ['#f3d9de', '#f1efe8', '#e79aa8', '#f4e7a8', '#f3d9de'];
  petals.forEach((c, i) => {
    const a = (i / petals.length) * Math.PI * 2;
    const x = Math.cos(a) * 0.04;
    const z = Math.sin(a) * 0.04;
    const st = cyl(0.004, 0.004, 0.14, stem, 4);
    st.position.set(x / 2, 0.16, z / 2);
    st.rotation.set(z * 4, 0, -x * 4);
    g.add(st);
    g.add(at(new THREE.Mesh(new THREE.SphereGeometry(0.028, 8, 6), mat(c, 0.7)), x, 0.23 + (i % 2) * 0.02, z));
  });
  return g;
}

/** A small easel sign that stands on a counter. */
export function counterSign(face: THREE.Texture): THREE.Group {
  const g = new THREE.Group();
  g.add(at(rbox(0.2, 0.15, 0.012, mat(C.oak, 0.6), 0.004), 0, 0.08, 0));
  const p = new THREE.Mesh(new THREE.PlaneGeometry(0.18, 0.13), new THREE.MeshStandardMaterial({ map: face, roughness: 0.95 }));
  p.position.set(0, 0.08, 0.007);
  g.add(p);
  g.rotation.x = -0.2;
  return g;
}
