import type { ReactNode } from "react";
import type { Result } from "../api";
import { formatAsOf } from "../results";

// The card every generative component renders in: eyebrow, optional title, body, and provenance.
export function Frame(props: {
  eyebrow: string;
  title?: string;
  status?: "loading";
  result?: Result;
  note?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="gen-card" aria-busy={props.status === "loading"}>
      <header className="gen-head">
        <span className="eyebrow">{props.eyebrow}</span>
        {props.actions}
      </header>
      {props.title && <h3 className="gen-title">{props.title}</h3>}
      {props.status === "loading" ? <p className="loading">[LOADING…]</p> : props.children}
      {props.result && <Provenance result={props.result} note={props.note} />}
    </section>
  );
}

export function Provenance({ result, note }: { result: Result; note?: string }) {
  const metrics = result.metrics.map((m) => `${m.name}${m.unit ? ` (${m.unit})` : ""}`).join(" · ");
  return (
    <footer className="provenance">
      <span>{metrics}</span>
      <span>
        {result.rowCount} ROWS{result.truncated ? " · TRUNCATED" : ""}{result.shared ? " · SHARED MODEL" : ""}
      </span>
      {formatAsOf(result.dataAsOf) && <span>DATA AS OF {formatAsOf(result.dataAsOf)}</span>}
      {note && <span>{note}</span>}
    </footer>
  );
}

export function Problem({ text }: { text: string }) {
  return (
    <section className="gen-card problem" role="alert">
      <span className="eyebrow">CANNOT SHOW THIS</span>
      <p>{text}</p>
    </section>
  );
}
