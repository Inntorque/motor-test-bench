import {createIconRenderer, createIconScene, ICON_SIZE} from './graphics/icon-scene.js';

export function animateIcons(entries) {
  const {renderer, outline} = createIconRenderer();
  const icons = entries.map(({id, element}) => {
    const canvas = document.createElement('canvas');
    canvas.width = canvas.height = ICON_SIZE;
    canvas.setAttribute('aria-hidden', 'true');
    const context = canvas.getContext('2d');
    context.imageSmoothingEnabled = false;
    element.append(canvas);
    return {...createIconScene(id), element, app: element.parentElement, context, visible: false, drawn: false};
  });
  const reduced = matchMedia('(prefers-reduced-motion: reduce)');
  let timer;
  let frame;
  let lastTime;
  const interval = 1000 / 24;
  const moving = icon => icon.visible && icon.app.matches(':hover') && !reduced.matches;

  function stop() {
    clearTimeout(timer);
    cancelAnimationFrame(frame);
    timer = frame = undefined;
    lastTime = undefined;
  }

  function draw(now) {
    const start = performance.now();
    const elapsed = lastTime === undefined ? 0 : (now - lastTime) / 1000;
    lastTime = now;
    for (const icon of icons) {
      if (!icon.visible) continue;
      const spinning = moving(icon);
      if (icon.drawn && !spinning) continue;
      if (spinning) icon.rotor.rotation[icon.axis] = (icon.rotor.rotation[icon.axis] + elapsed * Math.PI * 8) % (Math.PI * 2);
      outline.render(icon.scene, icon.camera);
      icon.context.clearRect(0, 0, ICON_SIZE, ICON_SIZE);
      icon.context.drawImage(renderer.domElement, 0, 0);
      icon.element.classList.add('is-animated');
      icon.drawn = true;
    }
    if (icons.some(moving)) {
      timer = setTimeout(() => {frame = requestAnimationFrame(draw);}, Math.max(0, interval - (performance.now() - start)));
    }
  }

  function update() {
    stop();
    if (!document.hidden && icons.some(icon => icon.visible && (!icon.drawn || moving(icon)))) frame = requestAnimationFrame(draw);
  }

  const observer = new IntersectionObserver(entries => {
    for (const entry of entries) icons.find(icon => icon.element === entry.target).visible = entry.isIntersecting;
    update();
  });
  for (const icon of icons) {
    observer.observe(icon.element);
    icon.app.addEventListener('pointerenter', update);
    icon.app.addEventListener('pointerleave', update);
  }
  document.addEventListener('visibilitychange', update);
  reduced.addEventListener('change', update);
  window.addEventListener('pagehide', stop);
  window.addEventListener('pageshow', update);
}
