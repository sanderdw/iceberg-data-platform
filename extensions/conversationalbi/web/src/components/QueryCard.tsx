import { formatAsOf } from "../results";

// How a governed query shows up in the chat: what was asked, how it was joined, and the SQL that ran.
type Dimension = { field: string; via?: string[]; grain?: string | null };
type Filter = { field?: string | null; metric?: string | null; op: string; value?: unknown };
export type QueryParameters = { metrics?: string[]; dimensions?: Dimension[]; filters?: Filter[]; limit?: number };

type QueryOutcome = {
  resultId?: string;
  rowCount?: number;
  truncated?: boolean;
  dataAsOf?: string | null;
  sql?: string;
  joinPaths?: Record<string, string[]>;
  metrics?: { name: string; definition: string }[];
  error?: string;
  code?: string;
  detail?: { options?: string[][] };
};

function parse(result?: string): QueryOutcome | undefined {
  if (!result) return undefined;
  try {
    return JSON.parse(result) as QueryOutcome;
  } catch {
    return { error: result };
  }
}

function filterText(f: Filter) {
  const value = Array.isArray(f.value) ? f.value.join(", ") : f.value === undefined || f.value === null ? "" : String(f.value);
  return `${f.field ?? f.metric} ${f.op.replace("_", " ")} ${value}`.trim();
}

export function QueryCard({ status, parameters, result }: { status: string; parameters: QueryParameters; result?: string }) {
  const outcome = parse(result);
  const running = status !== "complete";
  return (
    <section className={`query-card${outcome?.error ? " failed" : ""}`}>
      <header className="gen-head">
        <span className="eyebrow">{running ? "QUERY · RUNNING" : outcome?.error ? `QUERY · ${outcome.code?.toUpperCase() ?? "FAILED"}` : "QUERY"}</span>
        {outcome?.rowCount !== undefined && (
          <span className="eyebrow">
            {outcome.rowCount} ROWS{outcome.truncated ? " · TRUNCATED" : ""}
            {formatAsOf(outcome.dataAsOf) && ` · DATA AS OF ${formatAsOf(outcome.dataAsOf)}`}
          </span>
        )}
      </header>
      <div className="chips">
        {(parameters.metrics ?? []).map((m) => <span key={m} className="chip metric">{m}</span>)}
        {(parameters.dimensions ?? []).map((d, i) => (
          <span key={`${d.field}-${i}`} className="chip">
            BY {d.field}{d.grain ? ` · ${d.grain.toUpperCase()}` : ""}
          </span>
        ))}
        {(parameters.filters ?? []).map((f, i) => <span key={i} className="chip quiet">WHERE {filterText(f)}</span>)}
      </div>
      {outcome?.error && (
        <p className="query-error">
          {outcome.error}
          {outcome.detail?.options && <> Options: {outcome.detail.options.map((o) => o.join(" → ")).join("; ")}.</>}
        </p>
      )}
      {outcome?.joinPaths && Object.keys(outcome.joinPaths).length > 0 && (
        <dl className="facts">
          {Object.entries(outcome.joinPaths).map(([field, path]) => (
            <div key={field}>
              <dt>{field}</dt>
              <dd>{path.length ? path.join(" → ") : "same dataset"}</dd>
            </div>
          ))}
        </dl>
      )}
      {outcome?.metrics?.map((m) => (
        <p key={m.name} className="hint"><span className="mono">{m.name}</span> — {m.definition}</p>
      ))}
      {outcome?.sql && (
        <details>
          <summary>SQL</summary>
          <pre>{outcome.sql}</pre>
        </details>
      )}
    </section>
  );
}
