export const STORE_KEY = "inntorque.demo-recordings.v1";

const DATE_PATTERN =
  /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2})$/;
const NO_LOCK_WARNING = "此浏览器不支持多页面保存锁，请只在一个页面记录试验。";

function isObject(value) {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype ||
      Object.getPrototypeOf(value) === null)
  );
}

function isIsoDate(value) {
  if (
    typeof value !== "string" ||
    !DATE_PATTERN.test(value) ||
    !Number.isFinite(Date.parse(value))
  )
    return false;
  // Date.parse normalizes dates such as February 30. Reject those instead of
  // silently assigning a different day to a stored trial or sample.
  const [year, month, day] = value.slice(0, 10).split("-").map(Number);
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= days[month - 1];
}

function isTimestamp(value) {
  return (
    (typeof value === "number" &&
      Number.isFinite(value) &&
      Number.isFinite(new Date(value).getTime())) ||
    isIsoDate(value)
  );
}

function isJsonValue(value) {
  if (value === null || typeof value === "string" || typeof value === "boolean")
    return true;
  if (typeof value === "number") return Number.isFinite(value);
  if (Array.isArray(value)) return value.every(isJsonValue);
  return isObject(value) && Object.values(value).every(isJsonValue);
}

function isEvent(event) {
  return (
    isObject(event) &&
    isIsoDate(event.at) &&
    Number.isFinite(event.t) &&
    event.t >= 0 &&
    typeof event.text === "string"
  );
}

function isOrdered(items) {
  return items.every(
    (item, index) => index === 0 || item.t >= items[index - 1].t,
  );
}

function isRecord(record) {
  if (
    !isObject(record) ||
    typeof record.id !== "string" ||
    record.id.trim() === "" ||
    typeof record.name !== "string" ||
    ![50, 100].includes(record.range) ||
    !isTimestamp(record.startedAt) ||
    !Number.isFinite(record.duration) ||
    record.duration < 0 ||
    (record.finishedAt !== undefined && !isTimestamp(record.finishedAt)) ||
    !Array.isArray(record.samples) ||
    record.samples.length === 0
  )
    return false;

  const validSamples = record.samples.every(
    (sample) =>
      isObject(sample) &&
      sample.source === "simulation" &&
      isIsoDate(sample.receivedAt) &&
      Number.isFinite(sample.t) &&
      sample.t >= 0 &&
      ["torque", "speed", "current", "targetSpeed", "targetCurrent"].every(
        (key) => sample[key] === null || Number.isFinite(sample[key]),
      ) &&
      typeof sample.motorEnabled === "boolean" &&
      typeof sample.loadEnabled === "boolean" &&
      sample.range === record.range,
  );
  return (
    validSamples &&
    isOrdered(record.samples) &&
    (record.config === undefined ||
      (isObject(record.config) && isJsonValue(record.config))) &&
    (record.events === undefined ||
      (Array.isArray(record.events) &&
        record.events.every(isEvent) &&
        isOrdered(record.events)))
  );
}

// The first occurrence wins, so a newly completed in-memory record takes
// precedence over a stored copy of the same record. Inputs are never mutated.
export function mergeRecords(...lists) {
  const records = new Map();
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const record of list) {
      if (record && typeof record.id === "string" && !records.has(record.id))
        records.set(record.id, record);
    }
  }
  return [...records.values()].sort(
    (a, b) =>
      new Date(b.startedAt).getTime() - new Date(a.startedAt).getTime() ||
      a.id.localeCompare(b.id),
  );
}

// Resolve browser storage inside the try block: access itself can throw when
// browser privacy settings disable localStorage.
export function readRecords(storage) {
  try {
    const target = storage ?? globalThis.localStorage;
    const raw = target.getItem(STORE_KEY);
    const data = raw === null ? [] : JSON.parse(raw);
    if (!Array.isArray(data))
      return {
        records: [],
        error: "本地记录格式无法识别，原始数据已保留；新记录可暂存并导出。",
        blocked: true,
      };
    const records = data.filter(isRecord);
    const blocked = records.length !== data.length;
    return {
      records: mergeRecords(records),
      error: blocked
        ? "部分本地记录无法读取，原始数据已保留；暂停覆盖保存，新记录仍可导出。"
        : "",
      blocked,
    };
  } catch {
    return {
      records: [],
      error: "无法读取浏览器本地记录，未覆盖原始数据；新记录仍可导出。",
      blocked: true,
    };
  }
}

export async function saveRecords(records, storage, locks) {
  const incoming = Array.isArray(records) ? records.filter(isRecord) : [];
  if (!Array.isArray(records) || incoming.length !== records.length)
    return {
      records: mergeRecords(incoming),
      error: "记录数据校验失败，未覆盖浏览器记录；有效记录仍可导出。",
      saved: false,
      warning: "",
    };

  const persist = () => {
    const existing = readRecords(storage);
    const merged = mergeRecords(incoming, existing.records);
    if (existing.blocked)
      return {
        records: merged,
        error: existing.error,
        saved: false,
        warning: "",
      };
    try {
      const target = storage ?? globalThis.localStorage;
      target.setItem(STORE_KEY, JSON.stringify(merged));
      return { records: merged, error: "", saved: true, warning: "" };
    } catch {
      return {
        records: merged,
        error: "浏览器本地保存失败，请及时导出；记录仍保留在此页面。",
        saved: false,
        warning: "",
      };
    }
  };

  try {
    const lockManager = locks ?? globalThis.navigator?.locks;
    if (typeof lockManager?.request === "function")
      return await lockManager.request(
        STORE_KEY,
        { mode: "exclusive" },
        persist,
      );
    const result = persist();
    return { ...result, warning: result.saved ? NO_LOCK_WARNING : "" };
  } catch {
    const existing = readRecords(storage);
    return {
      records: mergeRecords(incoming, existing.records),
      error: "无法取得本地记录保存锁，未覆盖已有记录；请导出当前记录。",
      saved: false,
      warning: "",
    };
  }
}

export function relativeSamples(samples) {
  if (!Array.isArray(samples) || samples.length === 0) return [];
  const start = samples[0].t;
  return samples.map((sample) => ({ ...sample, t: sample.t - start }));
}

function csvCell(value) {
  const text = String(value);
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

export function eventCsv(events) {
  if (!Array.isArray(events) || !events.every(isEvent))
    throw new TypeError("Invalid recording events");
  const rows = ["source,receivedAt,elapsedSeconds,event"];
  for (const event of events) {
    // Event names may include operator-entered trial names. Prevent spreadsheet
    // formula execution while preserving numeric elapsed-time cells.
    const text = /^[\s]*[=+\-@]|^[\t\r\n]/.test(event.text)
      ? `'${event.text}`
      : event.text;
    rows.push(["simulation", event.at, event.t, text].map(csvCell).join(","));
  }
  return `\uFEFF${rows.join("\r\n")}\r\n`;
}
