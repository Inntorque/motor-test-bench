import test from "node:test";
import assert from "node:assert/strict";
import { toCsv, formatDuration } from "../web/src/model.js";

test("historical CSV export retains fields and leaves unmeasured PSU channels blank", () => {
  const sample = {
    source: "simulation",
    receivedAt: "2026-10-09T00:00:00.000Z",
    t: 123.5,
    torque: null,
    speed: null,
    current: null,
    targetSpeed: 300,
    targetCurrent: 0.5,
    motorEnabled: false,
    loadEnabled: false,
    range: 50,
  };
  const csv = toCsv([sample]);
  assert.ok(csv.startsWith("\uFEFFsource,receivedAt,elapsedSeconds,gearboxOutputTorqueNm,gearboxOutputSpeedRpm,calculatedOutputPowerKW,brakeCurrentA,motorSupplyVoltageV,motorSupplyCurrentA,motorSupplyPowerW,brakeSupplyVoltageV,brakeSupplyCurrentA,brakeSupplyPowerW,motorTargetSpeedRpm,brakeTargetCurrentA,motorEnabled,brakeOutputEnabled,torqueSensorRangeNm\r\n"));
  assert.ok(csv.includes("simulation,2026-10-09T00:00:00.000Z,0,,,,,,,,,,,300,0.5,false,false,50\r\n"));
  assert.ok(toCsv([{ ...sample, source: 'demo,"old"\nrow' }]).includes('"demo,""old""\nrow"'));
});

test("hardware CSV exports only supplied live readings and leaves other channels empty", () => {
  const csv = toCsv([{
    source: "hardware",
    receivedAt: "2026-10-09T00:00:00.000Z",
    t: 8,
    torque: null,
    speed: null,
    current: null,
    motorSupplyVoltage: 24,
    motorSupplyCurrent: 0.25,
    motorSupplyPower: 6,
    brakeSupplyVoltage: null,
    brakeSupplyCurrent: null,
    brakeSupplyPower: null,
    targetSpeed: null,
    targetCurrent: null,
    motorEnabled: null,
    loadEnabled: null,
    range: 50,
  }]);
  assert.ok(csv.includes("hardware,2026-10-09T00:00:00.000Z,0,,,,,24,0.25,6,,,,,,,,50\r\n"));
});

test("CSV includes calculated shaft power from the real torque sensor", () => {
  const csv = toCsv([{
    source: "hardware",
    receivedAt: "2026-10-09T00:00:00.000Z",
    t: 1,
    torque: -12.34,
    speed: 250,
    shaftPowerKw: 0.323,
    current: null,
    motorSupplyVoltage: null,
    motorSupplyCurrent: null,
    motorSupplyPower: null,
    brakeSupplyVoltage: null,
    brakeSupplyCurrent: null,
    brakeSupplyPower: null,
    targetSpeed: null,
    targetCurrent: null,
    motorEnabled: null,
    loadEnabled: null,
    range: 50,
  }]);
  assert.ok(csv.includes("hardware,2026-10-09T00:00:00.000Z,0,-12.34,250,0.323,,,,,,,,,,,,50\r\n"));
});

test("historical elapsed time starts at the first saved sample", () => {
  const csv = toCsv([
    { t: 123.5, receivedAt: "2026-10-09T00:00:00.000Z" },
    { t: 124, receivedAt: "2026-10-09T00:00:00.500Z" },
  ]).split("\r\n");
  assert.equal(csv[1].split(",")[2], "0");
  assert.equal(csv[2].split(",")[2], "0.5");
});

test("duration formatting handles invalid and fractional input", () => {
  assert.equal(formatDuration(0), "00:00");
  assert.equal(formatDuration(125.9), "02:05");
  assert.equal(formatDuration(-3), "00:00");
  assert.equal(formatDuration(Infinity), "00:00");
});
