// Retain every sample; copy only the retained portion of the bounded history.
export function appendSamples(previous, incoming, limit) {
  if (!incoming.length) return previous;
  if (incoming.length >= limit) return incoming.slice(-limit);
  return previous.slice(Math.max(0, previous.length + incoming.length - limit)).concat(incoming);
}

// One pass over raw data preserves the same per-pixel extrema and axis peaks.
export function plotColumns(samples, metric, start, seconds, width) {
  const columns = new Map();
  let peak = 0;
  for (const sample of samples) {
    const value = sample[metric];
    if (!(sample.t >= start) || !Number.isFinite(value)) continue;
    peak = Math.max(peak, Math.abs(value));
    const key = Math.floor((sample.t - start) / seconds * width);
    const bucket = columns.get(key);
    if (!bucket) columns.set(key, { first: sample, last: sample, min: sample, max: sample });
    else {
      bucket.last = sample;
      if (value < bucket.min[metric]) bucket.min = sample;
      if (value > bucket.max[metric]) bucket.max = sample;
    }
  }
  const plotted = [];
  for (const bucket of columns.values()) {
    const points = [bucket.first];
    for (const point of [bucket.min, bucket.max, bucket.last]) if (!points.includes(point)) points.push(point);
    points.sort((a, b) => a.t - b.t);
    for (const point of points) plotted.push(point);
  }
  return { peak, plotted };
}
