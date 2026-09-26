// Little people, in the same soft low-poly language as the furniture: capsule body,
// round head, a hair shape that does most of the characterisation, arms that can
// lift a cup, and two-part legs that fold properly onto a chair or a bar stool.
// No faces -- at this scale a face only ever reads as uncanny.
import * as THREE from 'three';
import { at, cyl, mat } from './props';
import { C } from './textures';

export type HairStyle = 'short' | 'crop' | 'ponytail' | 'bun' | 'long' | 'curly';
export interface Look {
  top: string;
  skin: string;
  hair: string;
  style: HairStyle;
  legs?: string;
  apron?: string;
}

export interface Figure {
  group: THREE.Group; // at the feet, facing +z
  hips: THREE.Group; // the whole body above the feet; lifted to sit
  upper: THREE.Group; // torso, head and arms, pivoting at the hip joint
  head: THREE.Group; // turns to look about
  thighs: [THREE.Group, THREE.Group];
  shins: [THREE.Group, THREE.Group];
  arms: [THREE.Group, THREE.Group]; // shoulder pivots, [right, left]
  cup: THREE.Object3D; // held in the right hand
  tail: THREE.Object3D | null; // a ponytail, which swings
  seat: number; // current seat height, 0 when standing
}

const sphere = new THREE.SphereGeometry(1, 16, 12);
const headGeo = new THREE.SphereGeometry(0.105, 18, 14);
const torsoGeo = new THREE.CapsuleGeometry(0.135, 0.3, 6, 14);
const armGeo = new THREE.CapsuleGeometry(0.038, 0.26, 4, 8);
const thighGeo = new THREE.CapsuleGeometry(0.052, 0.16, 4, 8);
const shinGeo = new THREE.CapsuleGeometry(0.046, 0.18, 4, 8);
const capGeo = new THREE.SphereGeometry(0.113, 18, 10, 0, Math.PI * 2, 0, Math.PI / 1.9);
const apronGeo = new THREE.CylinderGeometry(0.142, 0.15, 0.42, 16, 1, true, -Math.PI / 2.4, Math.PI / 1.2);

const HIP_Y = 0.44;

function blob(m: THREE.Material, sx: number, sy: number, sz: number, x: number, y: number, z: number) {
  const o = new THREE.Mesh(sphere, m);
  o.scale.set(sx, sy, sz);
  o.position.set(x, y, z);
  return o;
}

export function figure(look: Look): Figure {
  const g = new THREE.Group();
  const hips = new THREE.Group();
  g.add(hips);
  // torso, head and arms lean together about the hip joint
  const upper = new THREE.Group();
  hips.add(upper);
  const top = mat(look.top, 0.85);
  const skin = mat(look.skin, 0.7);
  const hairM = mat(look.hair, 0.9);
  const legM = mat(look.legs ?? '#2b2a26', 0.8);

  const torso = new THREE.Mesh(torsoGeo, top);
  torso.position.y = HIP_Y + 0.3;
  torso.castShadow = true;
  upper.add(torso);
  if (look.apron) {
    const apron = new THREE.Mesh(apronGeo, mat(look.apron, 0.9));
    apron.position.y = HIP_Y + 0.2;
    apron.scale.set(1.04, 1, 1.08);
    upper.add(apron);
    upper.add(at(cyl(0.004, 0.004, 0.3, mat(look.apron, 0.9), 4), 0, HIP_Y + 0.52, 0.09)); // neck strap hint
  }

  const head = new THREE.Group();
  head.position.y = HIP_Y + 0.69;
  upper.add(head);
  const h = new THREE.Mesh(headGeo, skin);
  h.castShadow = true;
  head.add(h);
  let tail: THREE.Object3D | null = null;
  const cap = new THREE.Mesh(capGeo, hairM);
  cap.position.set(0, 0.012, -0.008);
  cap.rotation.x = -0.28;
  head.add(cap);
  switch (look.style) {
    case 'crop':
      cap.scale.set(0.98, 0.8, 0.98);
      break;
    case 'curly':
      for (let i = 0; i < 7; i++) {
        const a = (i / 7) * Math.PI * 2;
        head.add(blob(hairM, 0.05, 0.05, 0.05, Math.cos(a) * 0.08, 0.06 + (i % 2) * 0.02, Math.sin(a) * 0.08 - 0.02));
      }
      break;
    case 'bun':
      head.add(blob(hairM, 0.058, 0.055, 0.058, 0, 0.11, -0.07));
      break;
    case 'ponytail': {
      const pivot = new THREE.Group();
      pivot.position.set(0, 0.05, -0.1);
      pivot.add(blob(hairM, 0.036, 0.03, 0.03, 0, 0, 0)); // the tie
      pivot.add(blob(hairM, 0.04, 0.11, 0.04, 0, -0.1, -0.03));
      head.add(pivot);
      tail = pivot;
      break;
    }
    case 'long':
      head.add(blob(hairM, 0.105, 0.16, 0.06, 0, -0.07, -0.06));
      break;
  }

  const arms: THREE.Group[] = [];
  for (const x of [0.165, -0.165]) {
    const pivot = new THREE.Group();
    pivot.position.set(x, HIP_Y + 0.5, 0);
    const arm = new THREE.Mesh(armGeo, top);
    arm.position.y = -0.15;
    pivot.add(arm);
    pivot.add(blob(skin, 0.038, 0.038, 0.038, 0, -0.32, 0));
    pivot.rotation.z = x > 0 ? 0.08 : -0.08;
    upper.add(pivot);
    arms.push(pivot);
  }
  const cupObj = new THREE.Group();
  cupObj.add(at(cyl(0.034, 0.027, 0.085, mat(C.paper, 0.4), 12), 0, 0, 0));
  cupObj.add(at(cyl(0.035, 0.035, 0.02, mat(C.sage, 0.5), 12), 0, 0.005, 0));
  cupObj.position.set(0, -0.34, 0.05);
  cupObj.visible = false;
  arms[0].add(cupObj);

  upper.position.y = HIP_Y;
  for (const c of upper.children) c.position.y -= HIP_Y;

  const thighs: THREE.Group[] = [];
  const shins: THREE.Group[] = [];
  for (const x of [0.068, -0.068]) {
    const hip = new THREE.Group();
    hip.position.set(x, HIP_Y, 0);
    const thigh = new THREE.Mesh(thighGeo, legM);
    thigh.position.y = -0.1;
    hip.add(thigh);
    const knee = new THREE.Group();
    knee.position.y = -0.2;
    const shin = new THREE.Mesh(shinGeo, legM);
    shin.position.y = -0.11;
    knee.add(shin);
    knee.add(at(new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.04, 0.13), mat('#221f1b', 0.7)), 0, -0.225, 0.025));
    hip.add(knee);
    hips.add(hip);
    thighs.push(hip);
    shins.push(knee);
  }
  return {
    group: g,
    hips,
    upper,
    head,
    thighs: thighs as [THREE.Group, THREE.Group],
    shins: shins as [THREE.Group, THREE.Group],
    arms: arms as [THREE.Group, THREE.Group],
    cup: cupObj,
    tail,
    seat: 0,
  };
}

/** Seat heights: the top of a chair cushion, and of a bar stool. */
export const CHAIR = 0.55;
export const STOOL = 0.76;

/** Fold onto a seat of height `seat` (0 = stand). */
export function sit(f: Figure, seat: number) {
  f.seat = seat;
  if (!seat) {
    f.hips.position.y = 0;
    f.hips.position.z = 0;
    for (let i = 0; i < 2; i++) {
      f.thighs[i].rotation.x = 0;
      f.shins[i].rotation.x = 0;
    }
    return;
  }
  // torso bottom sits on the seat; thighs forward, shins down (or to the footrest)
  f.hips.position.y = seat - HIP_Y + 0.04;
  f.hips.position.z = -0.06;
  const high = seat > 0.65;
  for (let i = 0; i < 2; i++) {
    f.thighs[i].rotation.x = high ? -1.35 : -1.55;
    f.shins[i].rotation.x = high ? 0.75 : 1.5;
  }
}

/** One step of the walk cycle. */
export function walkPose(f: Figure, phase: number) {
  const s = Math.sin(phase);
  f.thighs[0].rotation.x = s * 0.5;
  f.thighs[1].rotation.x = -s * 0.5;
  f.shins[0].rotation.x = Math.max(0, -s) * 0.6;
  f.shins[1].rotation.x = Math.max(0, s) * 0.6;
  f.arms[0].rotation.x = -s * 0.35;
  f.arms[1].rotation.x = s * 0.35;
  f.hips.position.y = Math.abs(Math.cos(phase)) * 0.02;
  if (f.tail) f.tail.rotation.z = Math.sin(phase) * 0.25;
}

export function standStill(f: Figure) {
  for (let i = 0; i < 2; i++) {
    f.thighs[i].rotation.x *= 0.8;
    f.shins[i].rotation.x *= 0.8;
    f.arms[i].rotation.x *= 0.8;
  }
  f.hips.position.y *= 0.8;
}

/**
 * Seated idle: a slow sip every so often, a glance around, a small lean. `t` is
 * the clock, `seed` staggers people so nobody moves in unison.
 */
export function idle(f: Figure, t: number, seed: number, cupInHand: boolean) {
  const cycle = 7 + (seed % 5);
  const u = ((t + seed * 1.7) % cycle) / cycle;
  // sip: raise the right arm through the middle of the cycle
  const sip = cupInHand ? Math.max(0, Math.sin(Math.min(1, Math.max(0, (u - 0.55) / 0.3)) * Math.PI)) : 0;
  f.arms[0].rotation.x = -0.5 - sip * 1.6;
  f.arms[0].rotation.z = 0.08 + sip * 0.25;
  f.arms[1].rotation.x = -0.45;
  f.head.rotation.y = Math.sin(t * 0.4 + seed) * 0.45 * (1 - sip);
  f.head.rotation.x = sip * 0.25;
  f.upper.rotation.x = 0.04 + Math.sin(t * 0.25 + seed * 2) * 0.05;
  if (f.tail) f.tail.rotation.z = Math.sin(t * 0.9 + seed) * 0.08;
}
