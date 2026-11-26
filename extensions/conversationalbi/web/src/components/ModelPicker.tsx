import { useState } from "react";
import { sameRef, type ModelEntry, type ModelRef, type NotEnabled } from "../api";
import { settingsHref, useEnvironmentChange } from "./Settings";
import { Icon } from "./Icon";

type Props = {
  models: ModelEntry[];
  notEnabled: NotEnabled[];
  selected?: ModelRef | null;
  onSelect: (entry: ModelEntry) => void;
  onEnabled: () => void;
};

// Like the user portal's catalog list: team models first, then those shared with the team.
export function ModelPicker({ models, notEnabled, selected, onSelect, onEnabled }: Props) {
  const [busy, setBusy] = useState<string>();
  const [error, setError] = useState<string>();
  const groups: [string, ModelEntry[]][] = [
    ["Team models", models.filter((m) => !m.shared)],
    ["Shared with your team", models.filter((m) => m.shared)],
  ];
  // The same confirmation and call as Settings › Environments.
  const { change, dialog } = useEnvironmentChange(onEnabled);
  const enable = async (n: NotEnabled) => {
    setBusy(n.team + n.environment);
    setError(undefined);
    try {
      await change(n, n.environment, true);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(undefined);
    }
  };
  return (
    <nav className="models" aria-label="Semantic models">
      {groups.map(([label, entries]) => entries.length > 0 && (
        <section key={label} className="database-group">
          <div className="database-group-title">{label}</div>
          {entries.map((m) => {
            const active = sameRef(m.model, selected);
            return (
              <button key={m.key} type="button" className={`db${active ? " selected" : ""}`} aria-pressed={active} onClick={() => onSelect(m)}>
                <Icon kind="semantic-model" />
                <span className="database-label">
                  <strong>{m.model.name}</strong>
                  {m.shared && <span className="shared-badge">Shared · Read-only</span>}
                  <small>{m.databaseName} · {m.model.namespace.join(".")}</small>
                  {m.shared && <small>Owner: {m.teamName}</small>}
                  <small>{m.environment[0].toUpperCase() + m.environment.slice(1)}</small>
                </span>
              </button>
            );
          })}
        </section>
      ))}
      {models.length === 0 && <p className="hint">No semantic models yet. Publish one with notebook 06, or ask another team to share one.</p>}
      {notEnabled.length > 0 && (
        <section className="database-group">
          <div className="database-group-title">Not enabled</div>
          {notEnabled.map((n) => (
            <div key={n.team + n.environment} className="not-enabled">
              <small>{n.teamName} · {n.environment}</small>
              {n.canEnable ? (
                <button type="button" className="quiet" disabled={busy === n.team + n.environment} onClick={() => enable(n)}>Enable</button>
              ) : (
                <small>A team administrator enables it in <a href={settingsHref(n.team)}>Settings</a>.</small>
              )}
            </div>
          ))}
          {error && <p className="notice-error" role="alert">{error}</p>}
          {dialog}
        </section>
      )}
    </nav>
  );
}
