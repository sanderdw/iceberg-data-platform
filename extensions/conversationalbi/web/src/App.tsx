import { CopilotChat, CopilotKitProvider, useAgent } from "@copilotkit/react-core/v2";
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { api, CSRF, sameRef, type ModelEntry, type ModelRef, type NotEnabled } from "./api";
import { Icon } from "./components/Icon";
import { ModelPicker } from "./components/ModelPicker";
import { useGenerativeUI } from "./generative";
import { useTheme } from "./theme";

type Session = { authenticated: boolean; user?: string };
type Models = { models: ModelEntry[]; notEnabled: NotEnabled[] };
const RETURN_KEY = "cbi-return-hash";

// A catalog link such as #/ask?database=db-…&namespace=["ai_flights"]&model=flights selects that model.
function deepLink(): ModelRef | null {
  const [path, query] = location.hash.slice(1).split("?");
  if (path !== "/ask" || !query) return null;
  const params = new URLSearchParams(query);
  try {
    const raw = params.get("namespace") ?? "[]";
    const namespace = raw.startsWith("[") ? (JSON.parse(raw) as string[]) : raw.split(".");
    const database = params.get("database"), name = params.get("model");
    return database && name && Array.isArray(namespace) ? { database, namespace, name } : null;
  } catch {
    return null;
  }
}

// The platform portals' header: brand, navigation, theme, identity.
function Header({ nav, children }: { nav?: ReactNode; children?: ReactNode }) {
  const [theme, toggle] = useTheme();
  return (
    <header className="site">
      <a className="brand" href="/"><img src="/favicon.svg" alt="" />iceberg<span>/ conversational bi</span></a>
      {nav}
      <div className="header-actions">
        <button type="button" className="ghost" onClick={toggle} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}>
          {theme === "dark" ? "Light / ○" : "Dark / ●"}
        </button>
        {children}
      </div>
    </header>
  );
}

export function App() {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState<string>();
  useEffect(() => {
    api<Session>("/api/session").then(setSession, (e: Error) => setError(e.message));
    try {
      const saved = sessionStorage.getItem(RETURN_KEY);
      if (saved && !location.hash) location.hash = saved;
      sessionStorage.removeItem(RETURN_KEY);
    } catch {
      /* Storage can be blocked; the deep link is a convenience. */
    }
  }, []);
  if (error || !session) {
    return (
      <>
        <Header />
        <main className="initial"><p className="eyebrow">{error ?? "[ LOADING ]"}</p></main>
      </>
    );
  }
  if (!session.authenticated) return <SignIn />;
  return (
    <CopilotKitProvider runtimeUrl="/api/copilotkit" headers={CSRF} agentId="analyst" useSingleEndpoint={false}
      enableInspector={false}>
      <Workspace user={session.user ?? ""} />
    </CopilotKitProvider>
  );
}

function SignIn() {
  const signIn = () => {
    try {
      if (location.hash) sessionStorage.setItem(RETURN_KEY, location.hash);
    } catch {
      /* Continue without the deep link. */
    }
    location.href = "/auth/login";
  };
  return (
    <>
      <Header />
      <main id="login-screen">
        <section className="login">
          <div className="eyebrow">YOUR QUESTIONS. GOVERNED ANSWERS.</div>
          <h1>Conversational BI<span className="dot-accent">.</span></h1>
          <p>Ask questions of your teams' semantic models and the data products shared with them.</p>
          <button type="button" className="wide" onClick={signIn}>Sign in with Keycloak <span aria-hidden="true">↗</span></button>
          <p className="hint">Use your linked Keycloak account.</p>
        </section>
        <aside className="intro">
          <div className="eyebrow">FROM QUESTION TO ANSWER</div>
          <h2>Every answer is a query you can inspect.</h2>
          <p>Metrics, joins and definitions come from the model's owner, not from the AI.</p>
          <ol>
            <li><span>[ 01 ]</span>Pick a semantic model</li>
            <li><span>[ 02 ]</span>Ask in plain language</li>
            <li><span>[ 03 ]</span>Check the query behind every chart</li>
          </ol>
        </aside>
      </main>
    </>
  );
}

function Fact({ label, value }: { label: string; value?: string }) {
  return (
    <div className="fact">
      <span className="eyebrow">{label}</span>
      <strong>{value || "—"}</strong>
    </div>
  );
}

function Workspace({ user }: { user: string }) {
  useGenerativeUI();
  const { agent } = useAgent({ agentId: "analyst" });
  const [models, setModels] = useState<Models>();
  const [portal, setPortal] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const selected = (agent.state as { model?: ModelRef } | undefined)?.model ?? null;
  const current = models?.models.find((m) => sameRef(m.model, selected));
  // The full heading introduces a model; once the conversation starts it shrinks to one line.
  const started = (agent.messages?.length ?? 0) > 0;

  const select = useCallback(
    (entry: ModelEntry) => agent.setState({ ...(agent.state ?? {}), model: entry.model, lastResultId: null }),
    [agent],
  );
  const load = useCallback(() => {
    api<Models>("/api/v1/models").then(
      (found) => {
        setModels(found);
        setProblem(undefined);
        const linked = deepLink();
        const match = linked && found.models.find((m) => sameRef(m.model, linked));
        if (match) select(match);
        else if (linked) setProblem("The linked model is not available to your teams, or Conversational BI is not enabled for its environment.");
      },
      (e: Error) => setProblem(e.message),
    );
  }, [select]);
  useEffect(load, [load]);
  useEffect(() => {
    api<{ userPortalUrl?: string }>("/api/v1/me").then((me) => setPortal(me.userPortalUrl), () => undefined);
  }, []);

  const signOut = async () => {
    const { logoutUrl } = await api<{ logoutUrl: string }>("/auth/logout", { method: "POST" });
    location.href = logoutUrl;
  };

  const nav = (
    <nav aria-label="Conversational BI">
      <button type="button" className="nav-item" aria-current="page">Ask</button>
      <button type="button" className="nav-item" onClick={() => setThreadId(crypto.randomUUID())}>New chat</button>
      {portal && <a className="nav-item extension-link" href={portal + "/#catalog"} target="_blank" rel="noopener">Workspaces</a>}
    </nav>
  );

  return (
    <>
      <Header nav={nav}>
        <div id="identity">
          <span id="username">{user}</span>
          <button type="button" className="quiet" onClick={signOut}>Sign out</button>
        </div>
      </Header>
      <main id="workspace-screen" className={started ? "started" : undefined}>
        <section className={`workspace-heading${started ? " compact" : ""}`}>
          <div>
            <div className="eyebrow">{current ? (current.shared ? "SHARED SEMANTIC MODEL" : "TEAM SEMANTIC MODEL") : "CONVERSATIONAL BI"}</div>
            <h1>{current ? current.name : "Ask"}<span className="dot-accent">.</span></h1>
            <p>{current
              ? "Ask about its metrics in plain language. Every answer shows the query, the join path and the metric definitions."
              : "Pick a semantic model of your team, or one shared with your team, and ask about its metrics."}</p>
          </div>
          {current && (
            <div className="workspace-context">
              <Fact label="Database" value={`${current.databaseName} · ${current.namespace.join(".")}`} />
              <Fact label="Environment" value={current.environment} />
              <Fact label="Owner" value={current.teamName} />
            </div>
          )}
        </section>
        <div className="workbench">
          <aside className="catalog">
            <div className="panel-heading">
              <h2>Models</h2>
              <button type="button" id="refresh" className="quiet" aria-label="Refresh models" onClick={load}><Icon kind="refresh" /></button>
            </div>
            {models ? (
              <ModelPicker models={models.models} notEnabled={models.notEnabled} selected={selected} onSelect={select} onEnabled={load} />
            ) : (
              <p className="hint">[ LOADING ]</p>
            )}
            {problem && <p className="notice-error" role="alert">{problem}</p>}
          </aside>
          <section className="chat" aria-label="Chat">
            <CopilotChat key={threadId} threadId={threadId} agentId="analyst"
              labels={{
                chatInputPlaceholder: current ? `Ask about ${current.name}…` : "Select a model, or ask which data you can explore…",
                chatDisclaimerText: "Answers come from governed queries on the selected model. Check the query card for definitions and SQL.",
              }} />
          </section>
        </div>
      </main>
    </>
  );
}
