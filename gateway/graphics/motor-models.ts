import * as THREE from 'three';
import {RoundedBoxGeometry} from 'three/addons/geometries/RoundedBoxGeometry.js';

const materials = () => {
  const ramp = new THREE.DataTexture(new Uint8Array([74, 138, 198, 255]), 4, 1, THREE.RedFormat);
  ramp.minFilter = ramp.magFilter = THREE.NearestFilter;
  ramp.needsUpdate = true;
  return Object.fromEntries(Object.entries({
    casing: '#708b98', rotor: '#5d8297', green: '#648b75', steel: '#d1dedb',
    satin: '#91acb5', brass: '#d5b979', dark: '#30494f',
  }).map(([name, color]) => {
    const material = new THREE.MeshToonMaterial({color, gradientMap: ramp});
    material.onBeforeCompile = (shader, renderer) => {
      shader.uniforms.grainScale = {value: renderer.getDrawingBufferSize(new THREE.Vector2()).x / 240};
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nuniform float grainScale;')
        .replace('#include <colorspace_fragment>', `
        #include <colorspace_fragment>
        vec2 grainPixel = floor(gl_FragCoord.xy / grainScale);
        float checker = mod(grainPixel.x, 2.0) + 2.0 * mod(grainPixel.y, 2.0);
        float dither = (checker - 1.5) / 4.0;
        gl_FragColor.rgb = floor(gl_FragColor.rgb * 20.0 + 0.5 + dither) / 20.0;
      `);
    };
    return [name, material];
  }));
};

function part(parent, name, geometry, material, position: [number, number, number] = [0, 0, 0]) {
  const mesh = new THREE.Mesh(geometry, material);
  mesh.name = name;
  mesh.position.set(...position);
  parent.add(mesh);
  return mesh;
}

function ring(parent, name, outer, inner, height, material, position, axis = 'x', bevel = .015) {
  const h = height / 2, b = Math.min(bevel, height / 3, (outer - inner) / 3);
  const points = [
    [inner, -h + b], [inner + b, -h], [outer - b, -h], [outer, -h + b],
    [outer, h - b], [outer - b, h], [inner + b, h], [inner, h - b], [inner, -h + b],
  ].map(([x, y]) => new THREE.Vector2(x, y));
  const mesh = part(parent, name, new THREE.LatheGeometry(points, 64), material, position);
  if (axis === 'x') mesh.rotation.z = -Math.PI / 2;
  return mesh;
}

function cylinder(parent, name, radius, height, material, position, axis = 'x', sides = 48) {
  const mesh = part(parent, name, new THREE.CylinderGeometry(radius, radius, height, sides), material, position);
  if (axis === 'x') mesh.rotation.z = -Math.PI / 2;
  return mesh;
}

function box(parent, name, size: [number, number, number], material, position, radius = .02) {
  return part(parent, name, new RoundedBoxGeometry(...size, 2, radius), material, position);
}

function radial(parent, name, geometry, material, count, radius, axial, axis = 'x', offset = 0) {
  const mesh = new THREE.InstancedMesh(geometry, material, count);
  mesh.name = name;
  const transform = new THREE.Object3D();
  for (let i = 0; i < count; i++) {
    const angle = offset + i * Math.PI * 2 / count;
    if (axis === 'x') {
      transform.position.set(axial, radius * Math.cos(angle), radius * Math.sin(angle));
      transform.rotation.set(angle, 0, 0);
    } else {
      transform.position.set(radius * Math.cos(angle), axial, radius * Math.sin(angle));
      transform.rotation.set(0, Math.PI / 2 - angle, 0);
    }
    transform.updateMatrix();
    mesh.setMatrixAt(i, transform.matrix);
  }
  parent.add(mesh);
  return mesh;
}

function bolt(parent, name, material, dark, position, axis = 'x', radius = .034, sign = 1) {
  cylinder(parent, name, radius, .024, material, position, axis, 6);
  const center = [...position];
  center[axis === 'x' ? 0 : 1] += sign * .0125;
  cylinder(parent, name + '-recess', radius * .38, .002, dark, center, axis, 6);
}

function dynamometer(m) {
  const model = new THREE.Group();
  model.name = 'dynamometer';
  const rotor = new THREE.Group();
  model.add(rotor);
  cylinder(model, 'motor-housing', .49, 1.12, m.casing, [.04, 0, 0]);
  ring(model, 'rear-end-cap', .51, 0, .11, m.casing, [.62, 0, 0]);
  radial(model, 'longitudinal-cooling-fins', new RoundedBoxGeometry(1.05, .10, .035, 1, .008), m.satin, 18, .52, .04);
  ring(model, 'rear-case-seam', .502, .485, .025, m.dark, [.53, 0, 0]);
  ring(model, 'front-flange', .53, .145, .12, m.satin, [-.55, 0, 0]);
  ring(model, 'machined-front-rim', .526, .465, .018, m.steel, [-.618, 0, 0], 'x', .004);
  ring(model, 'recessed-front-face', .46, .18, .025, m.casing, [-.612, 0, 0]);
  ring(model, 'bearing-seat', .245, .108, .04, m.steel, [-.64, 0, 0]);
  ring(model, 'bearing-seal', .188, .103, .018, m.dark, [-.665, 0, 0], 'x', .003);
  cylinder(rotor, 'output-shaft', .092, .67, m.brass, [-.96, 0, 0]);
  ring(rotor, 'shaft-end-chamfer', .092, 0, .025, m.brass, [-1.3, 0, 0], 'x', .006);
  box(rotor, 'shaft-keyway', [.46, .018, .052], m.dark, [-1.04, .092, 0], .003);
  box(rotor, 'shaft-index-mark', [.46, .045, .018], m.steel, [-1.04, 0, .092], .003);
  for (let i = 0; i < 4; i++) {
    const a = Math.PI / 4 + i * Math.PI / 2;
    bolt(model, 'face-bolt-' + i, m.steel, m.dark, [-.637, .402 * Math.cos(a), .402 * Math.sin(a)], 'x', .034, -1);
  }
  box(model, 'terminal-box', [.36, .17, .31], m.casing, [.12, .615, 0], .025);
  box(model, 'terminal-lid', [.36, .025, .31], m.satin, [.12, .708, 0], .012);
  for (const x of [-.36, .44]) {
    for (const z of [-.25, .25]) box(model, 'mounting-leg', [.17, .27, .17], m.casing, [x, -.53, z], .015);
  }
  box(model, 'mounting-base', [1.23, .09, .83], m.casing, [.04, -.7, 0], .035);
  for (const x of [-.43, .51]) {
    for (const z of [-.29, .29]) bolt(model, 'base-fastener', m.steel, m.dark, [x, -.646, z], 'y', .032);
  }
  return {model, rotor, axis: 'x'};
}

function sg6010(m) {
  const model = new THREE.Group();
  model.name = 'sg-6010hb';
  const rotor = new THREE.Group();
  model.add(rotor);
  ring(model, 'rotor-housing', .69, .12, .36, m.rotor, [-.04, 0, 0]);
  ring(model, 'rear-cap', .655, .11, .06, m.dark, [-.24, 0, 0]);
  radial(model, 'rotor-case-ribs', new THREE.BoxGeometry(.27, .035, .018), m.rotor, 28, .69, -.04);
  ring(model, 'machined-outer-flange', .715, .574, .055, m.steel, [.17, 0, 0]);
  ring(model, 'flange-seat', .59, .25, .035, m.dark, [.15, 0, 0]);
  radial(rotor, 'radial-face-sectors', new RoundedBoxGeometry(.026, .25, .12, 1, .012), m.rotor, 10, .424, .185);
  box(rotor, 'rotor-index-mark', [.018, .23, .085], m.brass, [.21, .424, 0], .006);
  ring(rotor, 'inner-rotor-ring', .29, .11, .06, m.satin, [.19, 0, 0]);
  ring(model, 'bearing-retainer', .235, .104, .045, m.steel, [.235, 0, 0]);
  ring(model, 'bearing-seal', .15, .095, .017, m.dark, [.263, 0, 0], 'x', .003);
  cylinder(rotor, 'output-shaft', .087, .87, m.brass, [.65, 0, 0]);
  ring(rotor, 'shaft-end-chamfer', .087, 0, .025, m.brass, [1.092, 0, 0], 'x', .006);
  box(rotor, 'shaft-keyway', [.5, .018, .048], m.dark, [.8, .087, 0], .003);
  box(rotor, 'shaft-index-mark', [.5, .045, .018], m.steel, [.8, 0, .087], .003);
  for (let i = 0; i < 4; i++) {
    const a = i * Math.PI / 2;
    ring(model, 'fastener-countersink', .043, .02, .004, m.dark, [.202, .642 * Math.cos(a), .642 * Math.sin(a)], 'x', .001);
    bolt(model, 'flange-fastener-' + i, m.steel, m.dark, [.208, .642 * Math.cos(a), .642 * Math.sin(a)], 'x', .03);
  }
  return {model, rotor, axis: 'x'};
}

function rm7010(m) {
  const model = new THREE.Group();
  model.name = 'rm-7010';
  const rotor = new THREE.Group();
  model.add(rotor);
  ring(model, 'hollow-motor-case', .78, .18, .35, m.green, [0, .19, 0], 'y', .025);
  ring(model, 'lower-case-seam', .777, .18, .026, m.dark, [0, .025, 0], 'y', .006);
  radial(model, 'vertical-case-fins', new RoundedBoxGeometry(.024, .26, .037, 1, .005), m.green, 32, .783, .19, 'y');
  ring(model, 'upper-machined-flange', .805, .62, .047, m.satin, [0, .369, 0], 'y', .01);
  ring(model, 'outer-edge-highlight', .803, .785, .006, m.steel, [0, .397, 0], 'y', .002);
  ring(model, 'annular-stator-seat', .623, .255, .025, m.dark, [0, .36, 0], 'y', .004);
  ring(rotor, 'annular-face', .607, .263, .017, m.green, [0, .378, 0], 'y', .004);
  radial(rotor, 'face-vent-sectors', new RoundedBoxGeometry(.044, .008, .105, 1, .008), m.dark, 12, .454, .39, 'y', Math.PI / 12);
  ring(model, 'central-bearing-seat', .295, .18, .053, m.steel, [0, .386, 0], 'y', .013);
  ring(model, 'through-bore-seal', .228, .181, .009, m.dark, [0, .417, 0], 'y', .002);
  for (let i = 0; i < 8; i++) {
    const a = i * Math.PI / 4;
    const position = [.706 * Math.cos(a), .399, .706 * Math.sin(a)];
    ring(model, 'rim-countersink', .036, .02, .006, m.dark, position, 'y', .002);
    bolt(model, 'rim-fastener-' + i, m.steel, m.dark, [position[0], .405, position[2]], 'y', .025);
  }
  return {model, rotor, axis: 'y'};
}

export const motorIconViews = {
  dynamometer: [-5, 3.2, 6],
  'sg-6010hb': [7, 2.6, 3.8],
  'rm-7010': [3, 3.6, 5],
  'foc-7010': [3, 3.6, 5],
  'foc-5014': [3, 3.6, 5],
  'foc-8513': [3, 3.6, 5],
};

export function createMotorIconModel(id) {
  const m = materials();
  if (id === 'foc-5014') m.green.color.set('#62879f');
  if (id === 'foc-8513') m.green.color.set('#8d7d9f');
  const icon = id === 'dynamometer' ? dynamometer(m) : id === 'sg-6010hb' ? sg6010(m) : rm7010(m);
  const {model} = icon;
  const bounds = new THREE.Box3().setFromObject(model);
  model.position.y -= bounds.min.y;
  model.updateMatrixWorld(true);
  return icon;
}
