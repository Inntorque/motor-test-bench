import test from "node:test";
import assert from "node:assert/strict";
import {
  STORE_KEY,
  readRecords,
  saveRecords,
  mergeRecords,
  relativeSamples,
  eventCsv,
} from "../web/src/records.js";
import { createDemoState, sampleFromState } from "../web/src/model.js";

const START = Date.parse("2026-10-09T00:00:00.000Z");

function record(id = "trial-1", startedAt = START) {
  return {
    id,
    name: "加载试验",
    range: 50,
    startedAt,
    finishedAt: startedAt + 1000,
    duration: 1,
    samples: [
      sampleFromState({ ...createDemoState(), t: 10 }, startedAt),
      sampleFromState({ ...createDemoState(), t: 11 }, startedAt + 1000),
    ],
  };
}

function storageWith(value = null) {
  const data = new Map(value === null ? [] : [[STORE_KEY, value]]);
  return {
    getItem: (key) => data.get(key) ?? null,
    setItem: (key, value) => data.set(key, String(value)),
  };
}

test("reads legacy v1 records without requiring config or events", () => {
  const legacy = record();
  assert.deepEqual(readRecords(storageWith(JSON.stringify([legacy]))), {
    records: [legacy],
    error: "",
    blocked: false,
  });
  assert.deepEqual(readRecords(storageWith()), {
    records: [],
    error: "",
    blocked: false,
  });
});

test("rejects unsafe record and sample shapes before rendering or export", () => {
  const cases = [
    (r) => (r.samples = []),
    (r) => (r.name = { unsafe: "render" }),
    (r) => (r.id = " "),
    (r) => (r.startedAt = "garbage"),
    (r) => (r.finishedAt = null),
    (r) => (r.duration = -1),
    (r) => (r.range = 75),
    (r) => (r.samples[0].source = "hardware"),
    (r) => (r.samples[0].receivedAt = "0"),
    (r) => (r.samples[0].receivedAt = "2026-02-30T00:00:00.000Z"),
    (r) => (r.samples[0].t = "10"),
    (r) => (r.samples[1].t = 9),
    (r) => (r.samples[0].torque = "0"),
    (r) => delete r.samples[0].current,
    (r) => (r.samples[0].targetSpeed = { unsafe: "value" }),
    (r) => (r.samples[0].motorEnabled = "true"),
    (r) => (r.samples[0].range = 100),
  ];
  for (const mutate of cases) {
    const invalid = record();
    mutate(invalid);
    const raw = JSON.stringify([invalid]);
    const storage = storageWith(raw);
    const result = readRecords(storage);
    assert.equal(result.blocked, true, mutate.toString());
    assert.deepEqual(result.records, [], mutate.toString());
    assert.equal(storage.getItem(STORE_KEY), raw);
  }
  const missing = record();
  missing.samples[0].torque = null;
  missing.samples[0].speed = null;
  assert.equal(
    readRecords(storageWith(JSON.stringify([missing]))).blocked,
    false,
    "disconnected readbacks remain valid records",
  );
});

test("accepts optional configuration and validates event fields and order", () => {
  const current = {
    ...record(),
    config: { ratio: null, supply: "待确认", metadata: { simulation: true } },
    events: [
      { at: new Date(START).toISOString(), t: 0, text: "开始记录" },
      { at: new Date(START + 1000).toISOString(), t: 1, text: "停止记录" },
    ],
  };
  assert.equal(
    readRecords(storageWith(JSON.stringify([current]))).blocked,
    false,
  );
  for (const replacement of [null, [], "config"])
    assert.equal(
      readRecords(
        storageWith(JSON.stringify([{ ...current, config: replacement }])),
      ).blocked,
      true,
    );
  for (const replacement of [
    null,
    {},
    [{ at: new Date(START).toISOString(), t: 0, text: {} }],
    [{ at: "bad", t: 0, text: "bad date" }],
    [{ at: new Date(START).toISOString(), t: -1, text: "bad elapsed" }],
    [...current.events].reverse(),
  ])
    assert.equal(
      readRecords(
        storageWith(JSON.stringify([{ ...current, events: replacement }])),
      ).blocked,
      true,
    );
});

test("unreadable original data is never overwritten by a new save", async () => {
  const valid = record("retained");
  const fresh = record("fresh", START + 2000);
  for (const raw of [
    "invalid json",
    "{}",
    JSON.stringify([valid, { id: "broken", samples: [] }]),
  ]) {
    const storage = storageWith(raw);
    const result = await saveRecords([fresh], storage, {});
    assert.equal(result.saved, false);
    assert.match(result.error, /导出/);
    assert.ok(result.records.some((r) => r.id === "fresh"));
    assert.equal(storage.getItem(STORE_KEY), raw);
    if (raw.includes("retained"))
      assert.ok(result.records.some((r) => r.id === "retained"));
  }
});

test("read and quota failures preserve in-memory records and prior storage", async () => {
  const blocked = {
    getItem() {
      throw new Error("storage disabled");
    },
    setItem() {
      assert.fail("must not overwrite unreadable storage");
    },
  };
  assert.equal(readRecords(blocked).blocked, true);
  const readFailure = await saveRecords([record()], blocked, {});
  assert.equal(readFailure.saved, false);
  assert.equal(readFailure.records.length, 1);
  const old = record("saved");
  const fresh = record("fresh", START + 2000);
  const storage = storageWith(JSON.stringify([old]));
  storage.setItem = () => {
    throw new Error("QuotaExceededError");
  };
  const result = await saveRecords([fresh], storage, {});
  assert.equal(result.saved, false);
  assert.match(result.error, /及时导出/);
  assert.deepEqual(
    result.records.map((r) => r.id),
    ["fresh", "saved"],
  );
  assert.deepEqual(JSON.parse(storage.getItem(STORE_KEY)), [old]);
});

test("merges records by ID with first-list precedence and descending start time", () => {
  const old = record("old");
  const recent = record("recent", START + 3000);
  const updated = { ...old, name: "当前内存版本" };
  const incoming = [updated];
  const stored = [old, recent];
  assert.deepEqual(mergeRecords(incoming, stored), [recent, updated]);
  assert.deepEqual(incoming, [updated]);
  assert.deepEqual(stored, [old, recent]);
});

test("concurrent saves share an exclusive lock and retain both tabs' records", async () => {
  const storage = storageWith();
  let tail = Promise.resolve();
  const requests = [];
  const locks = {
    request(name, options, callback) {
      requests.push({ name, options });
      const result = tail.then(() => callback());
      tail = result.catch(() => {});
      return result;
    },
  };
  const a = record("tab-a");
  const b = record("tab-b", START + 1000);
  const results = await Promise.all([
    saveRecords([a], storage, locks),
    saveRecords([b], storage, locks),
  ]);
  assert.ok(results.every((result) => result.saved && !result.warning));
  assert.deepEqual(
    JSON.parse(storage.getItem(STORE_KEY)).map((r) => r.id),
    ["tab-b", "tab-a"],
  );
  assert.deepEqual(requests, [
    { name: STORE_KEY, options: { mode: "exclusive" } },
    { name: STORE_KEY, options: { mode: "exclusive" } },
  ]);
});

test("without Web Locks a save still merges latest storage and reports limitation", async () => {
  const storage = storageWith(JSON.stringify([record("other-tab")]));
  const result = await saveRecords([record("new", START + 2000)], storage, {});
  assert.equal(result.saved, true);
  assert.equal(result.error, "");
  assert.match(result.warning, /只在一个页面/);
  assert.deepEqual(
    result.records.map((r) => r.id),
    ["new", "other-tab"],
  );
});

test("lock failure does not silently fall back to an unlocked overwrite", async () => {
  const original = record("original");
  const storage = storageWith(JSON.stringify([original]));
  const locks = {
    request: async () => {
      throw new Error("lock unavailable");
    },
  };
  const result = await saveRecords(
    [record("new", START + 1000)],
    storage,
    locks,
  );
  assert.equal(result.saved, false);
  assert.match(result.error, /保存锁/);
  assert.equal(result.records.length, 2);
  assert.deepEqual(JSON.parse(storage.getItem(STORE_KEY)), [original]);
});

test("invalid incoming records are not serialized into storage", async () => {
  const storage = storageWith();
  const bad = { ...record(), name: { invalid: true } };
  const result = await saveRecords([bad], storage, {});
  assert.equal(result.saved, false);
  assert.equal(storage.getItem(STORE_KEY), null);
});

test("relative samples start each trial at zero without changing stored data", () => {
  const original = record().samples;
  const before = structuredClone(original);
  const relative = relativeSamples(original);
  assert.deepEqual(
    relative.map((s) => s.t),
    [0, 1],
  );
  assert.deepEqual(original, before);
  assert.notEqual(relative[0], original[0]);
  assert.equal(relative[0].receivedAt, original[0].receivedAt);
  assert.deepEqual(relativeSamples([]), []);
});

test("event CSV includes source, escaped text and neutralized spreadsheet formulas", () => {
  const at = new Date(START).toISOString();
  const csv = eventCsv([
    { at, t: 0, text: '启动,"电机"\n完成' },
    ...[
      "=1+1",
      "+SUM(A1:A2)",
      "-2+3",
      "@SUM(A1)",
      "  =SUM(A1)",
      "\tformula",
    ].map((text, index) => ({ at, t: (index + 1) / 10, text })),
  ]);
  assert.ok(csv.startsWith("\uFEFFsource,receivedAt,elapsedSeconds,event\r\n"));
  assert.ok(
    csv.includes(
      'simulation,2026-10-09T00:00:00.000Z,0,"启动,""电机""\n完成"\r\n',
    ),
  );
  for (const text of [
    "=1+1",
    "+SUM(A1:A2)",
    "-2+3",
    "@SUM(A1)",
    "  =SUM(A1)",
    "\tformula",
  ])
    assert.ok(csv.includes(`,'${text}\r\n`));
  assert.ok(csv.includes(",0.1,'=1+1\r\n"), "elapsed time stays numeric");
  assert.throws(() => eventCsv([{ at, t: 0, text: {} }]), TypeError);
});
