import { useState } from "react";
import type { ModelDescription } from "../api";
import { type Dimension, splitQuestion, splits, starterQuestions } from "../guide";

type Props = {
  model?: ModelDescription;
  error?: string;
  busy: boolean;
  onAsk: (question: string) => void;
  onClose: () => void;
};

const GRAINS = "DAY · WEEK · MONTH · QUARTER · YEAR";

// What a person can ask of the selected model, from the owner's own text: its metrics, what each can be
// split by, the guidance and how the datasets join. Every question it offers names fields as the model does.
export function ModelGuide({ model, error, busy, onAsk, onClose }: Props) {
  const [picked, setPicked] = useState<string | null>(null);
  const metric = model?.metrics.find((m) => m.name === picked);
  return (
    <aside className="guide" aria-label="Model guide">
      <div className="panel-heading">
        <h2>Model guide</h2>
        <button type="button" className="quiet" onClick={onClose}>Close</button>
      </div>
      {error && <p className="notice-error" role="alert">{error}</p>}
      {!model && !error && <p className="hint">[ LOADING ]</p>}
      {model && (
        <div className="guide-body">
          <section>
            <div className="eyebrow">About</div>
            {model.description ? <p>{model.description}</p> : <p className="hint">The owner gave no description.</p>}
            <div className="chips">
              {model.datasets.map((d) => <span key={d.name} className="chip" title={d.description}>{d.name} · {d.table}</span>)}
            </div>
            {model.unreadableTables.length > 0 && (
              <p className="notice-error">Not readable, so not answerable: {model.unreadableTables.join(", ")}.</p>
            )}
            {model.problems.map((p) => <p key={p.metric} className="notice-error">{p.error}</p>)}
          </section>

          <section>
            <div className="eyebrow">{model.examples.length ? "Try asking · from the owner" : "Try asking"}</div>
            <div className="guide-questions">
              {starterQuestions(model, 6).map((q) => (
                <button key={q} type="button" className="question" disabled={busy} onClick={() => onAsk(q)}>{q}</button>
              ))}
            </div>
          </section>

          <section>
            <div className="eyebrow">Metrics · {model.metrics.length}</div>
            <p className="hint">Pick a metric to see what it can be split by.</p>
            <ul className="guide-list">
              {model.metrics.map((m) => (
                <li key={m.name}>
                  <button type="button" className={`guide-item${m.name === picked ? " selected" : ""}`} aria-pressed={m.name === picked}
                    onClick={() => setPicked(m.name === picked ? null : m.name)}>
                    <span className="guide-name">
                      <strong className="mono">{m.name}</strong>
                      {m.unit && <span className="chip">{m.unit}</span>}
                    </span>
                    {m.description && <span className="guide-text">{m.description}</span>}
                    {m.synonyms.length > 0 && <span className="guide-text">Also: {m.synonyms.join(", ")}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </section>

          <section>
            <div className="eyebrow">{metric ? `Split ${metric.name} by` : "Dimensions"}</div>
            {metric ? <Splits model={model} metric={metric.name} busy={busy} onAsk={onAsk} /> : <Dimensions model={model} />}
          </section>

          {model.instructions && (
            <details>
              <summary>OWNER GUIDANCE</summary>
              <ul className="guidance">
                {model.instructions.split("\n").filter((l) => l.trim()).map((line, i) => <li key={i}>{line}</li>)}
              </ul>
            </details>
          )}

          {model.relationships.length > 0 && (
            <details>
              <summary>HOW THE DATASETS JOIN</summary>
              <dl className="facts guide-joins">
                {model.relationships.map((r) => (
                  <div key={r.name}>
                    <dt>{r.name}</dt>
                    <dd>{r.from}.{r.fromColumns.join(", ")} → {r.to}.{r.toColumns.join(", ")}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </div>
      )}
    </aside>
  );
}

function Field({ name, meta }: { name: string; meta: Dimension["meta"] }) {
  return (
    <span className="guide-name">
      <span className="mono">{name}</span>
      {meta.time && <span className="chip">{GRAINS}</span>}
    </span>
  );
}

// Every dimension, by dataset, before a metric is picked.
function Dimensions({ model }: { model: ModelDescription }) {
  const groups = model.datasets.map((d) => ({ ...d, fields: d.fields.filter((f) => f.dimension) })).filter((d) => d.fields.length);
  return (
    <>
      {groups.map((d) => (
        <div key={d.name} className="guide-group">
          <div className="database-group-title">{d.name}</div>
          <ul className="guide-list">
            {d.fields.map((f) => (
              <li key={f.name} className="guide-row">
                <Field name={f.name} meta={f} />
                {f.description && <span className="guide-text">{f.description}</span>}
                {f.synonyms.length > 0 && <span className="guide-text">Also: {f.synonyms.join(", ")}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </>
  );
}

// What one metric splits by, with the join path; datasets it cannot reach would count its rows twice.
function Splits({ model, metric, busy, onAsk }: { model: ModelDescription; metric: string; busy: boolean; onAsk: (q: string) => void }) {
  const found = splits(model, metric);
  const reached = new Set(found.map((d) => d.dataset));
  const home = model.metrics.find((m) => m.name === metric)?.dataset;
  const unreached = model.datasets.filter((d) => !reached.has(d.name)).map((d) => d.name);
  // A dataset is reached the same way for all its fields, so the join path heads its group.
  const how = (d: Dimension) => d.dataset === home ? "the metric's own dataset"
    : d.ambiguous ? "reachable in several ways: pick one" : `via ${d.path?.join(" → ")}`;
  return (
    <>
      {[...reached].map((dataset) => {
        const fields = found.filter((d) => d.dataset === dataset);
        return (
          <div key={dataset} className="guide-group">
            <div className="database-group-title">{dataset} · {how(fields[0])}</div>
            <ul className="guide-list">
              {fields.map((d) => (
                <li key={d.field} className={`guide-row split${d.ambiguous ? " several" : ""}`}>
                  <Field name={d.meta.name} meta={d.meta} />
                  <span className="guide-asks">
                    {d.ambiguous ? d.via?.map((path) => (
                      <button key={path.join()} type="button" className="ask" disabled={busy} title={splitQuestion(metric, d, path)}
                        onClick={() => onAsk(splitQuestion(metric, d, path))}>
                        Ask via {path.at(-1)}
                      </button>
                    )) : (
                      <button type="button" className="ask" disabled={busy} title={splitQuestion(metric, d)}
                        onClick={() => onAsk(splitQuestion(metric, d))}>Ask</button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
      {unreached.length > 0 && (
        <p className="hint">Not with {metric}: {unreached.join(", ")}. {home} does not look them up, so joining them could count its rows more than once; ask about them in a separate question.</p>
      )}
    </>
  );
}
