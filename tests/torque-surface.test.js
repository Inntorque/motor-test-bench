import test from "node:test";
import assert from "node:assert/strict";
import { setImmediate as settle } from "node:timers/promises";
import { createTorqueSurface } from "../web/src/torque-surface.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function fakeShader(failure = null) {
  const calls = [];
  return {
    calls,
    getFailureReason: () => failure,
    pause: () => calls.push(["pause"]),
    resume: () => calls.push(["resume"]),
    resize: (...size) => calls.push(["resize", ...size]),
    destroy: () => calls.push(["destroy"]),
  };
}

test("inactive surfaces stay lazy and rapid toggles share a pending initialization", async () => {
  const module = deferred();
  const creation = deferred();
  const shader = fakeShader();
  let loads = 0;
  let creates = 0;
  const surface = createTorqueSurface(
    {},
    {
      load: () => {
        loads++;
        return module.promise;
      },
    },
  );
  surface.setActive(false);
  surface.resize(300, 180);
  await settle();
  assert.equal(loads, 0);

  surface.setActive(true);
  surface.setActive(false);
  surface.setActive(true);
  assert.equal(loads, 1);
  module.resolve({
    createShader: () => {
      creates++;
      return creation.promise;
    },
  });
  await settle();
  surface.setActive(false);
  surface.setActive(true);
  assert.equal(creates, 1);
  assert.equal(loads, 1);
  creation.resolve(shader);
  await settle();
  assert.deepEqual(shader.calls, [["resize", 300, 180], ["resume"]]);
  surface.destroy();
});

test("deactivation while loading skips GPU creation and later activation can initialize", async () => {
  const module = deferred();
  const shader = fakeShader();
  let creates = 0;
  const states = [];
  const surface = createTorqueSurface(
    {},
    {
      load: () => module.promise,
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  surface.setActive(false);
  module.resolve({
    createShader: async () => {
      creates++;
      return shader;
    },
  });
  await settle();
  assert.equal(creates, 0);
  assert.equal(states.at(-1), "static");
  surface.setActive(true);
  await settle();
  assert.equal(creates, 1);
  assert.deepEqual(shader.calls, [["resume"]]);
  surface.destroy();
});

test("unmount before the module loads never creates a shader or reports later state", async () => {
  const module = deferred();
  const states = [];
  let creates = 0;
  const surface = createTorqueSurface(
    {},
    {
      load: () => module.promise,
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  surface.destroy();
  module.resolve({
    createShader: async () => {
      creates++;
      return fakeShader();
    },
  });
  await settle();
  surface.setActive(true);
  assert.equal(creates, 0);
  assert.deepEqual(states, ["loading"]);
});

test("unmount during GPU initialization destroys the eventual instance without resuming", async () => {
  const creation = deferred();
  const shader = fakeShader();
  const states = [];
  let creates = 0;
  const surface = createTorqueSurface(
    {},
    {
      load: async () => ({
        createShader: () => {
          creates++;
          return creation.promise;
        },
      }),
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  await settle();
  assert.equal(creates, 1);
  surface.destroy();
  creation.resolve(shader);
  await settle();
  surface.destroy();
  assert.deepEqual(shader.calls, [["destroy"]]);
  assert.deepEqual(states, ["loading"]);
});

test("initialization reads the latest paused state and passes CSS sizes and safe options", async () => {
  const creation = deferred();
  const shader = fakeShader();
  const canvas = {};
  const states = [];
  let argumentsReceived;
  const surface = createTorqueSurface(canvas, {
    load: async () => ({
      createShader: (...args) => {
        argumentsReceived = args;
        return creation.promise;
      },
    }),
    onState: (state) => states.push(state),
  });
  surface.resize(300, 180);
  surface.setActive(true);
  await settle();
  surface.resize(480, 200);
  surface.setActive(false);
  creation.resolve(shader);
  await settle();

  assert.equal(argumentsReceived[0], canvas);
  assert.equal(argumentsReceived[2].disableTelemetry, true);
  assert.equal(argumentsReceived[2].observeElement, false);
  assert.equal(argumentsReceived[2].colorSpace, "srgb");
  assert.equal(typeof argumentsReceived[2].onError, "function");
  assert.deepEqual(shader.calls, [["resize", 480, 200], ["pause"]]);
  assert.equal(states.at(-1), "paused");
  surface.setActive(true);
  assert.deepEqual(shader.calls.at(-1), ["resume"]);
  assert.equal(states.at(-1), "running");
  surface.setActive(false);
  assert.deepEqual(shader.calls.at(-1), ["pause"]);
  surface.destroy();
});

for (const failingStage of ["module loading", "GPU initialization"]) {
  test(`${failingStage} rejection selects static fallback without repeated retries`, async () => {
    const states = [];
    let loads = 0;
    const surface = createTorqueSurface(
      {},
      {
        load: async () => {
          loads++;
          if (failingStage === "module loading")
            throw new Error("import unavailable");
          return {
            createShader: async () => {
              throw new Error("GPU unavailable");
            },
          };
        },
        onState: (state) => states.push(state),
      },
    );
    surface.setActive(true);
    await settle();
    assert.equal(states.at(-1), "fallback");
    surface.setActive(false);
    surface.setActive(true);
    await settle();
    assert.equal(loads, 1);
    surface.destroy();
  });
}

test("onError during initialization disposes the returned shader and stays in fallback", async () => {
  const creation = deferred();
  const shader = fakeShader();
  const states = [];
  let options;
  const surface = createTorqueSurface(
    {},
    {
      load: async () => ({
        createShader: (_canvas, _preset, settings) => {
          options = settings;
          return creation.promise;
        },
      }),
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  await settle();
  options.onError("no-adapter");
  creation.resolve(shader);
  await settle();
  surface.setActive(true);
  assert.deepEqual(shader.calls, [["destroy"]]);
  assert.equal(states.at(-1), "fallback");
  surface.destroy();
});

test("device loss while paused destroys the shader before automatic recovery can resume it", async () => {
  const shader = fakeShader();
  const states = [];
  let options;
  const surface = createTorqueSurface(
    {},
    {
      load: async () => ({
        createShader: async (_canvas, _preset, settings) => {
          options = settings;
          return shader;
        },
      }),
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  await settle();
  surface.setActive(false);
  options.onError("device lost");
  surface.setActive(true);
  surface.resize(640, 360);
  assert.deepEqual(shader.calls, [["resume"], ["pause"], ["destroy"]]);
  assert.equal(states.at(-1), "fallback");
  surface.destroy();
});

test("a resolved shader with a GPU failure reason is disposed even without onError", async () => {
  const shader = fakeShader("unsupported");
  const states = [];
  const surface = createTorqueSurface(
    {},
    {
      load: async () => ({ createShader: async () => shader }),
      onState: (state) => states.push(state),
    },
  );
  surface.setActive(true);
  await settle();
  assert.deepEqual(shader.calls, [["destroy"]]);
  assert.equal(states.at(-1), "fallback");
  surface.setActive(true);
  assert.deepEqual(shader.calls, [["destroy"]]);
  surface.destroy();
});
