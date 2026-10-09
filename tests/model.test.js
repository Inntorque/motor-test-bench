import test from "node:test";
import assert from "node:assert/strict";
import {
  createDemoState,
  stepDemo,
  validateSetpoint,
  sampleFromState,
  toCsv,
  formatDuration,
} from "../web/src/model.js";

test("disabled outputs stay at rest even with non-zero setpoints", () => {
  const initial = {
    ...createDemoState(),
    targetSpeed: 1200,
    targetCurrent: 1.5,
  };
  const next = stepDemo(initial, 10);
  assert.equal(next.t, 10);
  assert.equal(next.speed, 0);
  assert.equal(next.current, 0);
  assert.equal(next.torque, 0);
  assert.equal(initial.t, 0);
});

test("sensor disconnection hides measurements without stopping the simulation", () => {
  const initial = {
    ...createDemoState(),
    motorEnabled: true,
    loadEnabled: true,
    sensorConnected: false,
  };
  const next = stepDemo(initial, 1);
  assert.ok(next.speed > 0 && next.current > 0 && next.torque > 0);
  const sample = sampleFromState(next, "2026-10-09T00:00:00Z");
  assert.equal(sample.torque, null);
  assert.equal(sample.speed, null);
  assert.ok(sample.current > 0);
  assert.equal(sample.receivedAt, "2026-10-09T00:00:00.000Z");
});

test("disconnected motor and supply cannot energize the demo, with supply readback missing", () => {
  const state = stepDemo(
    {
      ...createDemoState(100),
      motorEnabled: true,
      loadEnabled: true,
      motorConnected: false,
      supplyConnected: false,
    },
    1,
  );
  assert.equal(state.speed, 0);
  assert.equal(state.current, 0);
  assert.equal(state.torque, 0);
  assert.equal(sampleFromState(state, 0).current, null);
});

test("setpoints reject empty and non-finite values and enforce fixed demo boundaries", () => {
  for (const value of ["", "  ", null, true, NaN, Infinity, "not a number"]) {
    assert.equal(validateSetpoint(value, "speed").ok, false);
  }
  for (const [kind, max] of [
    ["speed", 1500],
    ["current", 2],
  ]) {
    assert.equal(validateSetpoint(0, kind).ok, true);
    assert.deepEqual(validateSetpoint(String(max), kind), {
      ok: true,
      value: max,
      error: "",
    });
    assert.equal(validateSetpoint(-0.01, kind).ok, false);
    assert.equal(validateSetpoint(max + 0.01, kind).ok, false);
  }
  assert.equal(validateSetpoint(1, "torque").ok, false);
});

test("current setpoints use hundredths without rejecting normal floating-point noise", () => {
  for (const value of [0.504, "0.504", 0.001, 1.999]) {
    const result = validateSetpoint(value, "current");
    assert.equal(result.ok, false);
    assert.equal(result.value, null);
    assert.match(result.error, /最多保留两位小数/);
  }
  for (const [input, expected] of [
    ["0.5000", 0.5],
    ["1.01", 1.01],
    [0.1 + 0.2, 0.3],
    [2, 2],
  ]) {
    assert.deepEqual(validateSetpoint(input, "current"), {
      ok: true,
      value: expected,
      error: "",
    });
  }
  assert.equal(validateSetpoint(0.504, "speed").ok, true);
});

test("CSV has a simulation marker, all fields, blank missing measurements and proper escaping", () => {
  const sample = sampleFromState(
    { ...createDemoState(), sensorConnected: false, supplyConnected: false },
    0,
  );
  const csv = toCsv([sample]);
  assert.ok(
    csv.startsWith(
      "\uFEFFsource,receivedAt,t,torque,speed,current,targetSpeed,targetCurrent,motorEnabled,loadEnabled,range\r\n",
    ),
  );
  assert.ok(
    csv.includes(
      "simulation,1970-01-01T00:00:00.000Z,0,,,,300,0.5,false,false,50\r\n",
    ),
  );
  const escaped = toCsv([{ ...sample, source: 'simulation,"demo"\r\nrow' }]);
  assert.ok(escaped.includes('"simulation,""demo""\r\nrow"'));
});

test("elapsed time formatting is stable and dt must be valid", () => {
  assert.equal(formatDuration(0), "00:00");
  assert.equal(formatDuration(125.9), "02:05");
  assert.equal(formatDuration(-3), "00:00");
  assert.equal(formatDuration(3600), "60:00");
  assert.throws(() => stepDemo(createDemoState(), -1), RangeError);
  assert.throws(() => stepDemo(createDemoState(), Infinity), RangeError);
});
