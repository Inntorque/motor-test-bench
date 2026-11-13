import * as THREE from 'three';
const canvas = document.querySelector('#scene');
let renderer;
try {renderer = new THREE.WebGLRenderer({canvas, antialias:false, alpha:false});}
catch {document.querySelector('#scene-status').textContent='WebGL 不可用，数值显示仍可使用';}
let angle = 0;
export function setRotor(degrees) {angle = degrees * Math.PI / 180;}
if (renderer) {
  renderer.setPixelRatio(1);
  renderer.setClearColor(0x111a22);
  const scene = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-4.8,4.8,3.4,-3.4,.1,100);
  camera.position.set(5,6,7);camera.lookAt(0,0,0);
  scene.add(new THREE.AmbientLight(0xffffff,2));
  const lamp = new THREE.DirectionalLight(0xffffff,3);lamp.position.set(3,7,4);scene.add(lamp);
  const box = new THREE.BoxGeometry(1,1,1);
  const materials = {};
  function block(parent,color,x,y,z,sx,sy,sz,rotation=0) {
    const material = materials[color] ||= new THREE.MeshLambertMaterial({color,flatShading:true});
    const m = new THREE.Mesh(box, material);m.position.set(x,y,z);m.scale.set(sx,sy,sz);m.rotation.y=rotation;parent.add(m);return m;
  }
  function ring(parent,n,r,y,color,size,height) {
    for(let i=0;i<n;i++){let a=i/n*Math.PI*2;block(parent,color,Math.cos(a)*r,y,Math.sin(a)*r,size,height,size,a);}
  }
  block(scene,0x24303c,0,-.65,0,5.4,.15,5.4);
  for(let i=-3;i<=3;i++){
    block(scene,0x304251,i*.8,-.56,0,.025,.02,5.2);
    block(scene,0x304251,0,-.56,i*.8,5.2,.02,.025);
  }
  ring(scene,48,2.1,-.25,0x586b7c,.28,.45);
  for(let i=0;i<24;i++){
    let a=i/24*Math.PI*2;
    block(scene,i%2?0xbd7952:0xe4a875,Math.cos(a)*1.77,-.03,Math.sin(a)*1.77,.32,.48,.32,a);
  }
  const rotor = new THREE.Group();scene.add(rotor);
  ring(rotor,48,1.34,.25,0x85cfc5,.23,.32);
  for(let i=0;i<20;i++){
    let a=i/20*Math.PI*2;
    block(rotor,i%2?0xec8fae:0x61bbaa,Math.cos(a)*1.22,.25,Math.sin(a)*1.22,.25,.40,.25,a);
  }
  ring(rotor,32,.82,.18,0x344d5e,.19,.38);
  block(rotor,0xf5df99,1.46,.55,0,.36,.13,.18);
  block(scene,0x7182a0,0,-.75,-2.4,1.1,.16,.65);
  block(scene,0x1e293a,0,-.62,-2.4,.5,.12,.32);
  let last = 0;
  function frame(now){
    requestAnimationFrame(frame);
    if(document.hidden || now-last<33)return;last=now;
    const w=Math.max(1,Math.floor(canvas.clientWidth/2))*3,h=Math.max(1,Math.floor(canvas.clientHeight/2))*3;
    if(canvas.width!==w || canvas.height!==h){renderer.setSize(w,h,false);camera.left=-3.4*w/h;camera.right=3.4*w/h;camera.updateProjectionMatrix();}
    rotor.rotation.y=-angle;
    renderer.render(scene,camera);
  }
  requestAnimationFrame(frame);
}
