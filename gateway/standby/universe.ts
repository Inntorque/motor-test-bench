const skyVertex = `#version 300 es
void main() {
  vec2 p = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

const skyFragment = `#version 300 es
precision highp float;
uniform vec2 uResolution;
uniform vec2 uPointer;
uniform float uTime;
out vec4 color;
float hash(vec2 p) { p = fract(p * vec2(123.34,456.21));p += dot(p,p + 45.32);return fract(p.x * p.y); }
void main() {
  vec2 uv = gl_FragCoord.xy / uResolution;
  float aspect = uResolution.x / uResolution.y;
  vec2 p = (uv - 0.5) * vec2(aspect,1.0);
  vec2 center = vec2(aspect * 0.16,-0.025) + uPointer * 0.025;
  vec2 q = p - center;
  q = mat2(0.929,-0.37,0.37,0.929) * q;
  float noise = hash(floor(gl_FragCoord.xy) + floor(uTime * 8.0));
  float dust = exp(-abs(q.y - sin(q.x * 5.0 + uTime * 0.025) * 0.025) * 25.0);
  vec3 tint = mix(vec3(0.16,0.42,0.65),vec3(0.65,0.14,0.40),(1.0 - smoothstep(-0.5,0.5,q.y)));
  vec3 base = vec3(0.003,0.004,0.008) + noise * 0.012;
  base += tint * dust * noise * 0.028;
  base *= 0.55 + 0.45 * pow(max(0.0,1.0 - length(uv - 0.5)),0.7);
  color = vec4(base,1.0);
}`;

const dustVertex = `#version 300 es
precision highp float;
layout(location=0) in vec4 aSeed;
uniform vec2 uResolution;
uniform vec2 uPointer;
uniform float uTime;
out vec3 vColor;
out float vAlpha;
out vec2 vDelta;
vec2 turn(vec2 p,float a) { return mat2(cos(a),sin(a),-sin(a),cos(a)) * p; }
void main() {
  float aspect = uResolution.x / uResolution.y;
  vec2 center = vec2(aspect * 0.16,-0.025) + uPointer * 0.025;
  vec2 p;
  float size = aSeed.z > 0.96 ? 2.0 : 1.0;
  if (aSeed.w < 0.18) {
    p = vec2((fract(aSeed.x + uTime * (0.0005 + aSeed.z * 0.0005)) - 0.5) * aspect,aSeed.y - 0.5);
    p += uPointer * (0.006 + aSeed.z * 0.012);
    vColor = mix(vec3(0.53,0.60,0.76),vec3(0.94,0.91,0.86),aSeed.z);
    vAlpha = (0.12 + pow(aSeed.z,4.0) * 0.75) * (0.75 + 0.25 * sin(uTime * 0.6 + aSeed.x * 80.0));
  } else if (aSeed.w > 0.955) {
    float a = aSeed.y * 6.2831853 + uTime * 0.13;
    float r = 0.105 + (aSeed.x - 0.5) * 0.009;
    p = center + turn(vec2(cos(a) * r * 1.06,sin(a) * r),-0.16);
    vColor = mix(vec3(0.18,0.65,0.94),vec3(0.95,0.36,0.73),0.5 - 0.5 * sin(a));
    vAlpha = 0.12 + aSeed.z * 0.22;
    size = 1.0;
  } else {
    float r = 0.20 + pow(aSeed.x,0.56) * 1.56;
    float a = aSeed.y * 6.2831853;
    if (aSeed.w > 0.55 && aSeed.w < 0.86)
      a = floor(aSeed.y * 4.0) * 1.5707963 + log(r + 0.10) * 1.7 + (fract(aSeed.y * 4.0) - 0.5) * 0.65;
    a += uTime * 0.09 / (r + 0.25);
    float arm = 0.5 + 0.5 * sin(a * 4.0 - log(r + 0.06) * 7.0 - uTime * 0.035);
    float lane = 0.18 + pow(arm,3.0) * 0.82;
    float height = (aSeed.z - 0.5) * (0.012 + r * 0.018);
    if (aSeed.w < 0.28) height += pow(aSeed.z,2.0) * 0.48;
    vec2 orbit = vec2(cos(a) * r,sin(a) * r * 0.32 + height);
    orbit.y += max(sin(a),0.0) * exp(-pow(r - 0.32,2.0) * 70.0) * 0.12;
    float scale = mix(0.54,0.64,smoothstep(0.5,1.5,aspect));
    p = center + turn(orbit,0.38) * scale;
    float front = 0.5 - 0.5 * sin(a);
    vColor = mix(vec3(0.18,0.68,1.0),vec3(1.0,0.26,0.79),smoothstep(0.15,0.85,front));
    float rim = exp(-abs(r - 0.32) * 17.0);
    vColor = mix(vColor,vec3(0.75,0.89,1.0),rim * 0.65);
    vAlpha = (0.35 + lane * 0.65) * (0.7 + aSeed.z * 0.3);
    size = aSeed.z > 0.35 ? 2.0 : 1.0;
    if (aSeed.w < 0.28) { vColor = vec3(0.90,0.93,1.0);vAlpha = 0.35 + aSeed.z * 0.65;size = aSeed.z > 0.75 ? 2.0 : 1.0; }
    vAlpha *= (1.0 - smoothstep(1.45,1.80,r));
  }
  vDelta = p - center;
  vec2 pixel = floor((p / vec2(aspect,1.0) + 0.5) * uResolution) + 0.5;
  gl_Position = vec4(pixel / uResolution * 2.0 - 1.0,0.0,1.0);
  gl_PointSize = size;
}`;

const dustFragment = `#version 300 es
precision highp float;
in vec3 vColor;
in float vAlpha;
in vec2 vDelta;
out vec4 color;
void main() {
  if (length(vDelta) < 0.083) discard;
  color = vec4(vColor,vAlpha);
}`;

export function createUniverse(canvas: HTMLCanvasElement): () => void {
  const context = canvas.getContext('webgl2',{alpha:false,antialias:false,depth:false,stencil:false,powerPreference:'low-power'});
  if (!context) throw new Error('WebGL2 is unavailable');
  const gl = context;
  const shaders: WebGLShader[] = [],programs: WebGLProgram[] = [];
  let buffer: WebGLBuffer | null = null,vao: WebGLVertexArrayObject | null = null;
  function releaseGpu() {
    gl.useProgram(null);gl.bindVertexArray(null);gl.bindBuffer(gl.ARRAY_BUFFER,null);
    gl.deleteBuffer(buffer);gl.deleteVertexArray(vao);
    for (const program of programs) gl.deleteProgram(program);
    for (const shader of shaders) gl.deleteShader(shader);
    gl.getExtension('WEBGL_lose_context')?.loseContext();
  }
  function program(vertex: string,fragment: string) {
    const result = gl.createProgram();
    if (!result) throw new Error('Unable to allocate a shader program');
    programs.push(result);
    for (const [kind,source] of [[gl.VERTEX_SHADER,vertex],[gl.FRAGMENT_SHADER,fragment]] as const) {
      const shader = gl.createShader(kind);
      if (!shader) throw new Error('Unable to allocate a shader');
      shaders.push(shader);gl.shaderSource(shader,source);gl.compileShader(shader);
      if (!gl.getShaderParameter(shader,gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(shader) || 'Shader compilation failed');
      gl.attachShader(result,shader);
    }
    gl.linkProgram(result);
    if (!gl.getProgramParameter(result,gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(result) || 'Shader linking failed');
    return {program:result,resolution:gl.getUniformLocation(result,'uResolution'),pointer:gl.getUniformLocation(result,'uPointer'),time:gl.getUniformLocation(result,'uTime')};
  }
  try {
    const layers = [program(skyVertex,skyFragment),program(dustVertex,dustFragment)];
    const capacity = 110000,seeds = new Float32Array(capacity * 4);
    let random = 14989231;
    for (let i = 0; i < seeds.length; i++) {random ^= random << 13;random ^= random >>> 17;random ^= random << 5;seeds[i] = (random >>> 0) / 4294967296;}
    buffer = gl.createBuffer();vao = gl.createVertexArray();
    gl.bindVertexArray(vao);gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,seeds,gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,4,gl.FLOAT,false,0,0);
    gl.bindVertexArray(null);
    gl.blendFunc(gl.SRC_ALPHA,gl.ONE);
    const reduced = matchMedia('(prefers-reduced-motion: reduce)');
    let frame = 0,last = 0,lastRendered = 0,time = 0,count = capacity;
    let pointerX = 0,pointerY = 0,targetX = 0,targetY = 0;
    function stop() {cancelAnimationFrame(frame);frame = 0;last = lastRendered = 0;}
    function render() {
      const gpu = gl;
      pointerX += (targetX - pointerX) * 0.09;pointerY += (targetY - pointerY) * 0.09;
      gpu.viewport(0,0,canvas.width,canvas.height);
      for (const [index,item] of layers.entries()) {
        gpu.useProgram(item.program);gpu.uniform2f(item.resolution,canvas.width,canvas.height);
        gpu.uniform2f(item.pointer,pointerX,pointerY);gpu.uniform1f(item.time,time);
        if (index === 0) {gpu.disable(gpu.BLEND);gpu.bindVertexArray(null);gpu.drawArrays(gpu.TRIANGLES,0,3);}
        else {gpu.enable(gpu.BLEND);gpu.bindVertexArray(vao);gpu.drawArrays(gpu.POINTS,0,count);}
      }
    }
    function tick(now: number) {
      if (!last || now - last >= 1000 / 30) {
        const elapsed = lastRendered ? Math.min((now - lastRendered) / 1000,0.1) : 0;
        lastRendered = now;
        time += elapsed;last = now - (last ? (now - last) % (1000 / 30) : 0);render();
      }
      frame = requestAnimationFrame(tick);
    }
    function update() {
      stop();
      if (document.hidden) return;
      render();
      if (!reduced.matches) frame = requestAnimationFrame(tick);
    }
    function resize() {
      const width = innerWidth,height = innerHeight,ratio = Math.min(devicePixelRatio || 1,1.25,1920 / width,1080 / height);
      canvas.width = Math.max(1,Math.round(width * ratio));canvas.height = Math.max(1,Math.round(height * ratio));
      count = Math.min(capacity,Math.max(28000,Math.round(width * height / 12)));update();
    }
    function point(event: PointerEvent) {targetX = event.clientX / innerWidth - 0.5;targetY = 0.5 - event.clientY / innerHeight;}
    canvas.addEventListener('pointermove',point);window.addEventListener('resize',resize);
    document.addEventListener('visibilitychange',update);reduced.addEventListener('change',update);
    window.addEventListener('pagehide',stop);window.addEventListener('pageshow',update);
    resize();
    return () => {
      stop();canvas.removeEventListener('pointermove',point);window.removeEventListener('resize',resize);
      document.removeEventListener('visibilitychange',update);reduced.removeEventListener('change',update);
      window.removeEventListener('pagehide',stop);window.removeEventListener('pageshow',update);
      releaseGpu();canvas.width = canvas.height = 1;
    };
  } catch (error) {releaseGpu();throw error;}
}
