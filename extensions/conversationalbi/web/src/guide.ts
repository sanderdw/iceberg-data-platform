import type { ModelDescription, ModelField, Reachable } from "./api";

// Questions the guide offers. They name metrics and fields exactly as the model spells them, so the
// agent maps them without guessing; they come from the model's metadata, never from the LLM.

export type Dimension = Reachable & { dataset: string; field: string; meta: ModelField };

const LABELS = /(^|_)(name|label|title|type|status|category|country|region|state|city|segment)(_|$)/;

// Every dimension a metric can be split by, with the field's metadata.
export function splits(model: ModelDescription, metric: string): Dimension[] {
  const home = model.metrics.find((m) => m.name === metric)?.dataset;
  if (!home) return [];
  return (model.dimensions[home] ?? []).flatMap((r) => {
    const [dataset, name] = r.field.split(".");
    const meta = model.datasets.find((d) => d.name === dataset)?.fields.find((f) => f.name === name);
    return meta ? [{ ...r, dataset, meta }] : [];
  });
}

// Join keys are poor answers to "by what": an id says less than the name it looks up.
function keys(model: ModelDescription) {
  const found = new Set<string>();
  for (const d of model.datasets) for (const k of d.primaryKey) found.add(`${d.name}.${k}`);
  for (const r of model.relationships) {
    r.fromColumns.forEach((c) => found.add(`${r.from}.${c}`));
    r.toColumns.forEach((c) => found.add(`${r.to}.${c}`));
  }
  return found;
}

const size = (model: ModelDescription, dataset: string) => model.datasets.find((d) => d.name === dataset)?.fields.length ?? 0;

// Unambiguous categories first by a label-like name, then the shortest join, then the smallest (lookup) dataset.
function categories(model: ModelDescription, metric: string) {
  const skip = keys(model);
  return splits(model, metric)
    .filter((d) => !d.ambiguous && !d.meta.time && !skip.has(d.field))
    .sort((a, b) => Number(LABELS.test(b.meta.name)) - Number(LABELS.test(a.meta.name))
      || (a.path?.length ?? 0) - (b.path?.length ?? 0) || size(model, a.dataset) - size(model, b.dataset));
}

function timeline(model: ModelDescription, metric: string) {
  return splits(model, metric)
    .filter((d) => !d.ambiguous && d.meta.time)
    .sort((a, b) => (a.path?.length ?? 0) - (b.path?.length ?? 0) || Number(b.meta.type === "date") - Number(a.meta.type === "date"))[0];
}

export function splitQuestion(metric: string, dimension: Dimension, via?: string[]) {
  const by = dimension.meta.time ? `per month of ${dimension.field}` : `by ${dimension.field}`;
  return `Show ${metric} ${by}${via?.length ? ` via ${via.join(" → ")}` : ""}.`;
}

// The owner's example questions; without them, a few built from the metrics and what they split by.
export function starterQuestions(model: ModelDescription, most = 4): string[] {
  if (model.examples.length) return model.examples.slice(0, most);
  const metrics = model.metrics.filter((m) => m.dataset).map((m) => m.name);
  if (!metrics.length) return [];
  const pick = (i: number) => metrics[i % metrics.length];
  const found: string[] = [];
  const time = timeline(model, pick(0));
  if (time) found.push(splitQuestion(pick(0), time));
  const first = categories(model, pick(1))[0];
  if (first) found.push(`Which ${first.field} has the highest ${pick(1)}?`);
  found.push(`What is the overall ${pick(2)}?`);
  const other = categories(model, pick(3)).find((d) => d.field !== first?.field);
  if (other) found.push(splitQuestion(pick(3), other));
  return [...new Set(found)].slice(0, most);
}
