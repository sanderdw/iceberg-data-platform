import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api } from "../api";

// The Settings page every extension shows (contracts/bridge/v1/settings in the platform), opened from Settings in
// the header. Its first option, Environments, is where Conversational BI is enabled for each of the caller's
// teams, with Enable and Disable for team administrators; pages about one team environment show its "not enabled"
// notice. Same markup, texts and stylesheet (settings.css) as the reference script, in React. Environments only
// calls the operations every extension shares:
//   GET    /api/v1/teams/{team}/environments                  list_environments
//   POST   /api/v1/teams/{team}/environments/{environment}    enable_environment
//   DELETE /api/v1/teams/{team}/environments/{environment}    disable_environment

export const ENVIRONMENTS = ["development", "acceptance", "production"] as const;
export type Environment = (typeof ENVIRONMENTS)[number];
export const LABELS: Record<string, string> = { development: "Development", acceptance: "Acceptance", production: "Production" };
const ADMINS = ["admin", "bucket-admin"];
const TITLE = "Conversational BI";
const ENABLES = "Its chat and MCP server answer questions from the team's semantic models in this environment, as the team's service account.";
const DISABLES = "The team's members can no longer ask about its semantic models in this environment, in the chat or through MCP.";
export const HREF = "#/settings";

export type Membership = { team: string; teamName: string; role: string };
export type EnvironmentEntry = {
  team: string;
  environment: string;
  enabled: boolean;
  enabledBy: string | null;
  databases: { id: string; name: string }[];
};

const isAdmin = (membership: Membership) => ADMINS.includes(membership.role);
export const settingsHref = (team?: string | null, section = "environments") =>
  `${HREF}/${encodeURIComponent(section)}${team ? `?team=${encodeURIComponent(team)}` : ""}`;
const path = (team: string, environment?: string) =>
  `/api/v1/teams/${encodeURIComponent(team)}/environments${environment ? "/" + encodeURIComponent(environment) : ""}`;

type Question = { title: string; text: string; action: string; danger: boolean; resolve: (ok: boolean) => void };

// A modal confirmation: render `dialog`, then `await confirm(...)` resolves to true when confirmed.
export function useConfirm() {
  const [question, setQuestion] = useState<Question>();
  const ref = useRef<HTMLDialogElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const confirm = useCallback((title: string, text: string, action: string, danger = false) =>
    new Promise<boolean>((resolve) => setQuestion({ title, text, action, danger, resolve })), []);
  useEffect(() => {
    const dialog = ref.current;
    if (!question || !dialog) return;
    if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
    else dialog.setAttribute("open", "");
    cancel.current?.focus();
  }, [question]);
  const answer = (ok: boolean) => {
    if (!question) return;
    question.resolve(ok);
    setQuestion(undefined);
  };
  const dialog = question ? (
    <dialog className="environments-dialog" ref={ref} onCancel={(event) => { event.preventDefault(); answer(false); }}>
      <form method="dialog" onSubmit={(event) => event.preventDefault()}>
        <h2>{question.title}</h2>
        <p>{question.text}</p>
        <div className="environments-dialog-actions">
          <button type="button" className="quiet" ref={cancel} onClick={() => answer(false)}>Cancel</button>
          <button type="button" className={question.danger ? "danger" : ""} onClick={() => answer(true)}>{question.action}</button>
        </div>
      </form>
    </dialog>
  ) : null;
  return { confirm, dialog };
}

// Asks, then enables or disables; resolves to the environment as list_environments shows it, or null when
// cancelled. A failed request rejects, for the caller to show.
export function useEnvironmentChange(onChange?: (entry: EnvironmentEntry) => void) {
  const { confirm, dialog } = useConfirm();
  const change = useCallback(async (membership: Pick<Membership, "team" | "teamName">, environment: string, on: boolean) => {
    const where = `${membership.teamName} in ${LABELS[environment]}`;
    const ok = on
      ? await confirm(`Enable ${TITLE} for ${where}?`,
        `${ENABLES} The platform creates the team's service account for ${TITLE} in ${LABELS[environment]} now; ` +
        "a team administrator can disable it again.", "Enable", false)
      : await confirm(`Disable ${TITLE} for ${where}?`,
        `${DISABLES} The team's service account for ${TITLE} in ${LABELS[environment]} is revoked.`, "Disable", true);
    if (!ok) return null;
    const entry = await api<EnvironmentEntry>(path(membership.team, environment), { method: on ? "POST" : "DELETE" });
    onChange?.(entry);
    return entry;
  }, [confirm, onChange]);
  return { change, dialog };
}

// The header link to #/settings.
export function SettingsLink({ current }: { current: boolean }) {
  return <a className="settings-link" href={HREF} aria-current={current ? "page" : undefined}>Settings</a>;
}

// The "not enabled" notice, with Enable for administrators.
export function EnvironmentsNotice({ membership, environment, onChange }:
  { membership: Membership; environment: string; onChange?: (entry: EnvironmentEntry) => void }) {
  const { change, dialog } = useEnvironmentChange(onChange);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const enable = async () => {
    setBusy(true);
    setError("");
    try { await change(membership, environment, true); } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };
  return (
    <div className="environments-notice" role="status">
      <span>{`${TITLE} is not enabled for ${membership.teamName} in ${LABELS[environment]}.`}</span>
      {isAdmin(membership) ? (
        <button type="button" className="quiet" disabled={busy} onClick={enable}
          aria-label={`Enable ${TITLE} for ${membership.teamName} in ${LABELS[environment]}`}>Enable</button>
      ) : (
        <span>A team administrator enables it in <a href={settingsHref(membership.team)}>Settings</a>.</span>
      )}
      <span className="environments-error">{error}</span>
      {dialog}
    </div>
  );
}

function Cell({ membership, environment, entry, details, onAction }: {
  membership: Membership; environment: string; entry?: EnvironmentEntry;
  details?: (entry: EnvironmentEntry, membership: Membership) => ReactNode; onAction: (on: boolean) => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const on = Boolean(entry?.enabled);
  const databases = (entry?.databases ?? []).map((d) => d.name);
  const act = async () => {
    setBusy(true);
    await onAction(!on);
    setBusy(false);
  };
  return (
    <div className={`environments-cell ${on ? "on" : "off"}`}>
      <div className="environments-cell-head">
        <strong>{LABELS[environment]}</strong>
        <span className="environments-state">{entry ? (on ? "On" : "Off") : "…"}</span>
      </div>
      {entry && (
        <>
          <p>{on ? `Enabled by ${entry.enabledBy || "a team administrator"}` : "Not enabled"}</p>
          <p>{databases.length ? `Databases: ${databases.join(", ")}` : "No databases yet"}</p>
          {on && details ? details(entry, membership) : null}
          {isAdmin(membership) && (
            <button type="button" className={on ? "danger" : "quiet"} disabled={busy} onClick={act}
              aria-label={`${on ? "Disable" : "Enable"} ${TITLE} for ${membership.teamName} in ${LABELS[environment]}`}>
              {on ? "Disable" : "Enable"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

function Team({ membership, details, onChange }: {
  membership: Membership; details?: (entry: EnvironmentEntry, membership: Membership) => ReactNode;
  onChange?: (entry: EnvironmentEntry) => void;
}) {
  const [entries, setEntries] = useState<EnvironmentEntry[] | null>(null);
  const [error, setError] = useState("");
  const { change, dialog } = useEnvironmentChange(onChange);
  const refresh = useCallback(async () => {
    try {
      setEntries((await api<{ environments: EnvironmentEntry[] }>(path(membership.team))).environments);
    } catch (e) {
      setEntries(null);
      setError((e as Error).message);
    }
  }, [membership.team]);
  useEffect(() => { void refresh(); }, [refresh]);
  const act = (environment: string) => async (on: boolean) => {
    setError("");
    try {
      if (await change(membership, environment, on)) await refresh();
    } catch (e) { setError((e as Error).message); }
  };
  return (
    <section className="environments-team" id={`environments-${membership.team}`}>
      <h3>{membership.teamName}</h3>
      <div className="environments-grid">
        {ENVIRONMENTS.map((environment) => (
          <Cell key={environment} membership={membership} environment={environment} details={details}
            entry={entries?.find((e) => e.environment === environment)} onAction={act(environment)} />
        ))}
      </div>
      <p className="environments-error" role="alert">{error}</p>
      {!isAdmin(membership) && <p className="environments-hint">A team administrator of this team enables and disables it.</p>}
      {dialog}
    </section>
  );
}

// The first option: where Conversational BI is on, per team and environment, and what that means.
function Environments({ memberships, details, onChange }: {
  memberships: Membership[]; details?: (entry: EnvironmentEntry, membership: Membership) => ReactNode;
  onChange?: (entry: EnvironmentEntry) => void;
}) {
  const facts: [string, string][] = [
    ["When it is on", `${ENABLES} It works through a service account of its own for that team and environment, ` +
      "which reaches only the team's databases there and the data shared with the team there."],
    ["When it is turned off", `${DISABLES} The service account is revoked at once.`],
    ["Who can change it", "Team administrators of the team. Every member of the team sees where it is on."],
  ];
  return (
    <>
      <p className="eyebrow">ENVIRONMENTS</p>
      <h2>{`Turn ${TITLE} on per team environment`}</h2>
      <p className="settings-lead">{`${TITLE} only works for a team in an environment (development, acceptance or ` +
        "production) where a team administrator turned it on. Each team decides this per environment, so a team can " +
        "try it in development long before it lets it touch production."}</p>
      <dl className="settings-facts">
        {facts.map(([term, text]) => <div key={term}><dt>{term}</dt><dd>{text}</dd></div>)}
      </dl>
      {memberships.length
        ? memberships.map((m) => <Team key={m.team} membership={m} details={details} onChange={onChange} />)
        : <p>You are not a member of a team yet.</p>}
      <p className="settings-small">AI agents and scripts do the same with <code>enable_environment</code> and{" "}
        <code>disable_environment</code>, or with POST and DELETE on{" "}
        <code>{"/api/v1/teams/{team}/environments/{environment}"}</code>.</p>
    </>
  );
}

// The page: its settings, with Environments first, and the chosen one.
export function SettingsPage({ memberships, focusTeam, section, onChange, details }: {
  memberships: Membership[]; focusTeam?: string | null; section?: string | null;
  onChange?: (entry: EnvironmentEntry) => void; details?: (entry: EnvironmentEntry, membership: Membership) => ReactNode;
}) {
  useEffect(() => {
    if (focusTeam) document.getElementById(`environments-${focusTeam}`)?.scrollIntoView?.({ block: "start" });
  }, [focusTeam]);
  const options = [{ id: "environments", title: "Environments", summary: `Where ${TITLE} is on for your teams` }];
  const current = options.find((o) => o.id === section) ?? options[0];
  return (
    <div className="settings-page">
      <div className="settings-head">
        <p className="eyebrow">SETTINGS</p>
        <h1>Settings</h1>
        <p className="settings-lead">{`How ${TITLE} works for your teams. Choose a setting to see what it does and change it.`}</p>
      </div>
      <div className="settings-layout">
        <nav className="settings-nav" aria-label="Settings">
          {options.map((o) => (
            <a key={o.id} href={settingsHref(null, o.id)} aria-current={o === current ? "page" : undefined}>
              <strong>{o.title}</strong><span>{o.summary}</span>
            </a>
          ))}
        </nav>
        <section className="settings-body">
          <Environments memberships={memberships} details={details} onChange={onChange} />
        </section>
      </div>
    </div>
  );
}
