// This is a decorative renderer only. It never receives measurement data.
export const TORQUE_PRESET = {
  components: [
    {
      type: "FlowingGradient",
      id: "torque-light",
      props: {
        colorA: "#172d48",
        colorB: "#61919f",
        colorC: "#20324d",
        colorD: "#a77550",
        colorSpace: "linear",
        speed: 0.14,
        distortion: 0.65,
        seed: 8,
      },
    },
  ],
};

// Keep cancellation outside the asynchronous library initialization: switching
// views while a GPU adapter is being requested must still release that device.
export function createTorqueSurface(
  canvas,
  { onState = () => {}, load = () => import("shaders/js") } = {},
) {
  let instance;
  let requested = false;
  let active = false;
  let disposed = false;
  let failed = false;
  let size;

  const report = (state) => {
    if (!disposed) onState(state);
  };
  const fallback = () => {
    failed = true;
    instance?.destroy();
    instance = undefined;
    report("fallback");
  };
  const sync = () => {
    if (!instance || disposed || failed) return;
    if (active) instance.resume();
    else instance.pause();
    report(active ? "running" : "paused");
  };
  const initialize = async () => {
    requested = true;
    report("loading");
    try {
      const { createShader } = await load();
      if (disposed || !active) {
        requested = false;
        report("static");
        return;
      }
      const created = await createShader(canvas, TORQUE_PRESET, {
        disableTelemetry: true,
        observeElement: false,
        colorSpace: "srgb",
        // GPU loss uses the static surface, rather than starting an automatic
        // device rebuild that could ignore a recording-time pause.
        onError: fallback,
      });
      if (disposed || failed || created.getFailureReason()) {
        created.destroy();
        if (!disposed && !failed) fallback();
        return;
      }
      instance = created;
      if (size) instance.resize(...size);
      sync();
    } catch {
      fallback();
    }
  };

  return {
    setActive(value) {
      if (disposed || failed) return;
      active = value;
      if (instance) sync();
      else if (active && !requested) void initialize();
    },
    resize(width, height) {
      if (disposed || failed || width <= 0 || height <= 0) return;
      size = [width, height];
      instance?.resize(width, height);
    },
    destroy() {
      disposed = true;
      active = false;
      instance?.destroy();
      instance = undefined;
    },
  };
}
