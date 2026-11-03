import * as THREE from 'three';
import {OutlineEffect} from 'three/addons/effects/OutlineEffect.js';
import {createMotorIconModel, motorIconViews} from './motor-models.js';

export const ICON_SIZE = 480;

export function createIconRenderer() {
  const renderer = new THREE.WebGLRenderer({alpha: true, antialias: false, powerPreference: 'low-power'});
  renderer.setSize(ICON_SIZE, ICON_SIZE);
  renderer.setPixelRatio(1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const outline = new OutlineEffect(renderer, {
    defaultThickness: .007,
    defaultColor: new THREE.Color('#3f5a63').toArray(),
  });
  outline.autoClear = true;
  return {renderer, outline};
}

export function createIconScene(id) {
  const scene = new THREE.Scene();
  const {model, rotor, axis} = createMotorIconModel(id);
  scene.add(model);
  scene.add(new THREE.AmbientLight(0xffffff, .8));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(-3, 6, 5);
  scene.add(key);
  const bounds = new THREE.Box3().setFromObject(model);
  const center = bounds.getCenter(new THREE.Vector3());
  const camera = new THREE.OrthographicCamera(-2, 2, 2, -2, .1, 40);
  const direction = new THREE.Vector3(...motorIconViews[id]).normalize();
  camera.position.copy(center).addScaledVector(direction, 10);
  camera.lookAt(center);
  camera.updateMatrixWorld(true);
  let size = 0;
  for (const x of [bounds.min.x, bounds.max.x]) {
    for (const y of [bounds.min.y, bounds.max.y]) {
      for (const z of [bounds.min.z, bounds.max.z]) {
        const p = new THREE.Vector3(x, y, z).applyMatrix4(camera.matrixWorldInverse);
        size = Math.max(size, Math.abs(p.x), Math.abs(p.y));
      }
    }
  }
  const half = size * 1.08;
  const projectedBounds = new THREE.Box3();
  const instance = new THREE.Matrix4();
  const transform = new THREE.Matrix4();
  const point = new THREE.Vector3();
  model.traverse(mesh => {
    if (!mesh.isMesh) return;
    const positions = mesh.geometry.getAttribute('position');
    const count = mesh.isInstancedMesh ? mesh.count : 1;
    for (let i = 0; i < count; i++) {
      transform.multiplyMatrices(camera.matrixWorldInverse, mesh.matrixWorld);
      if (mesh.isInstancedMesh) {
        mesh.getMatrixAt(i, instance);
        transform.multiply(instance);
      }
      for (let vertex = 0; vertex < positions.count; vertex++) {
        projectedBounds.expandByPoint(point.fromBufferAttribute(positions, vertex).applyMatrix4(transform));
      }
    }
  });
  const projectedCenter = projectedBounds.getCenter(new THREE.Vector3());
  camera.left = projectedCenter.x - half;
  camera.right = projectedCenter.x + half;
  camera.bottom = projectedCenter.y - half;
  camera.top = projectedCenter.y + half;
  camera.updateProjectionMatrix();
  return {scene, camera, rotor, axis};
}
