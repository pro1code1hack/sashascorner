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
