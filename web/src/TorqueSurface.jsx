import { useEffect, useRef, useState } from "react";
import { createTorqueSurface } from "./torque-surface.js";

export function useReducedMotion() {
  const [reduced, setReduced] = useState(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );
  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReduced(preference.matches);
    preference.addEventListener("change", update);
    update();
    return () => preference.removeEventListener("change", update);
  }, []);
  return reduced;
}

export default function TorqueSurface({ enabled, paused }) {
  const host = useRef(null);
  const canvas = useRef(null);
  const renderer = useRef(null);
  const [surfaceState, setSurfaceState] = useState("static");
  const [visible, setVisible] = useState(!document.hidden);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    if (!navigator.gpu) {
      setSurfaceState("fallback");
      return;
    }
    const surface = createTorqueSurface(canvas.current, {
      onState: setSurfaceState,
    });
    renderer.current = surface;
    const resize = new ResizeObserver(([entry]) => {
      surface.resize(entry.contentRect.width, entry.contentRect.height);
    });
    resize.observe(host.current);
    const intersection = new IntersectionObserver(([entry]) => {
      setInView(entry.isIntersecting);
    });
    intersection.observe(host.current);
    const visibility = () => setVisible(!document.hidden);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      resize.disconnect();
      intersection.disconnect();
      document.removeEventListener("visibilitychange", visibility);
      surface.destroy();
      renderer.current = null;
    };
  }, []);

  useEffect(() => {
    renderer.current?.setActive(enabled && !paused && visible && inView);
  }, [enabled, paused, visible, inView]);

  return (
    <div
      ref={host}
      className="torque-surface"
      aria-hidden="true"
      data-state={surfaceState}
      data-enabled={enabled}
    >
      <canvas ref={canvas} />
    </div>
  );
}
