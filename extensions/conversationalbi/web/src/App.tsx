import { CopilotChat, CopilotKitProvider, useAgent, useConfigureSuggestions, useCopilotKit } from "@copilotkit/react-core/v2";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, CSRF, describeModel, sameRef, type ModelDescription, type ModelEntry, type ModelRef, type NotEnabled } from "./api";
import { Icon } from "./components/Icon";
import { ModelGuide } from "./components/ModelGuide";
import { ModelPicker } from "./components/ModelPicker";
import { useGenerativeUI } from "./generative";
import { starterQuestions } from "./guide";
import { useTheme } from "./theme";

type Session = { authenticated: boolean; user?: string };
type Models = { models: ModelEntry[]; notEnabled: NotEnabled[] };
const RETURN_KEY = "cbi-return-hash";
const GUIDE_KEY = "cbi-guide";
// Below this width the guide covers the chat instead of standing next to it.
const narrow = () => matchMedia("(max-width: 1100px)").matches;

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

// The guide stays open on wide screens unless the person closed it; on narrow ones it opens on request.
function useGuideOpen() {
  const [open, setOpen] = useState(() => {
    try {
      return !narrow() && localStorage.getItem(GUIDE_KEY) !== "closed";
    } catch {
      return !narrow();
    }
  });
  const set = useCallback((next: boolean) => {
    setOpen(next);
    try {
      if (!narrow()) localStorage.setItem(GUIDE_KEY, next ? "open" : "closed");
    } catch {
      /* Storage can be blocked; the choice then lasts until the page reloads. */
    }
  }, []);
  return [open, set] as const;
}

type Described = { key: string; model?: ModelDescription; error?: string };

function Workspace({ user }: { user: string }) {
  useGenerativeUI();
  const { agent } = useAgent({ agentId: "analyst" });
  const { copilotkit } = useCopilotKit();
  const [models, setModels] = useState<Models>();
  const [portal, setPortal] = useState<string>();
  const [problem, setProblem] = useState<string>();
  const [threadId, setThreadId] = useState(() => crypto.randomUUID());
  const selected = (agent.state as { model?: ModelRef } | undefined)?.model ?? null;
  const current = models?.models.find((m) => sameRef(m.model, selected));
  // The full heading introduces a model; once the conversation starts it shrinks to one line.
  const started = (agent.messages?.length ?? 0) > 0;
  const [guideOpen, setGuideOpen] = useGuideOpen();
  const [described, setDescribed] = useState<Described>();
  const description = described && current && described.key === current.key ? described : undefined;
  const currentKey = current?.key;
  const currentRef = current?.model;
  // Loaded again with every model list load, so Refresh also shows what the owner changed since.
  useEffect(() => {
    if (!currentKey || !currentRef) return;
    let live = true;
    describeModel(currentRef).then(
      (model) => live && setDescribed({ key: currentKey, model }),
      (e: Error) => live && setDescribed({ key: currentKey, error: e.message }),
    );
    return () => {
      live = false;
    };
  }, [currentKey, models]);
  // Before the first question the chat offers the owner's examples, or questions built from the model.
  const starters = description?.model ? starterQuestions(description.model) : [];
  useConfigureSuggestions({
    consumerAgentId: "analyst",
    available: "before-first-message",
    suggestions: current
      ? starters.map((q) => ({ title: q, message: q }))
      : [{ title: "Which data can I explore?", message: "Which semantic models can I ask about?" }],
  }, [current ? starters.join("\n") : null]);
  // A question from the guide goes to the chat as if typed; on a narrow screen the guide makes way for it.
  const ask = useCallback((question: string) => {
    if (agent.isRunning) return;
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: question });
    copilotkit.runAgent({ agent }).catch(() => undefined);
    if (narrow()) setGuideOpen(false);
  }, [agent, copilotkit, setGuideOpen]);

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
  // The agent is stateless: every run sends the conversation it is given. Clearing stops a running
  // answer, empties the messages and starts a new thread, so nothing of it reaches the LLM again.
  // Switching threads resets the agent's state more than once, the last time when the connection to
  // the new thread replays it; the selected model is put back after each reset until the next question.
  const kept = useRef<ModelRef | null>(null);
  const [confirming, setConfirming] = useState(false);
  const clear = useCallback(() => {
    setConfirming(false);
    kept.current = selected;
    agent.abortRun();
    agent.setMessages([]);
    setThreadId(crypto.randomUUID());
  }, [agent, selected]);
  useEffect(() => {
    const subscription = agent.subscribe({
      onStateChanged: ({ state }) => {
        if (!kept.current || (state as { model?: ModelRef } | undefined)?.model) return;
        const model = kept.current;
        queueMicrotask(() => agent.setState({ ...(agent.state ?? {}), model, lastResultId: null }));
      },
      onMessagesChanged: ({ messages }) => {
        if (messages.length) kept.current = null;
      },
    });
    return () => subscription.unsubscribe();
  }, [agent]);
  // On a phone the page scrolls, the models come first and the chat is one screen high. Picking a
  // model scrolls to the chat. While the question field has focus, also after the keyboard resized the
  // page, the chat fills the screen with the field at the bottom.
  const chat = useRef<HTMLElement>(null);
  const phone = () => matchMedia("(max-width: 700px)").matches;
  const picked = useRef(false);
  const pick = useCallback((entry: ModelEntry) => {
    picked.current = phone();
    select(entry);
  }, [select]);
  // After the render: the heading above the chat grows with the selected model's details.
  useEffect(() => {
    if (!picked.current || !selected) return;
    picked.current = false;
    const smooth = !matchMedia("(prefers-reduced-motion: reduce)").matches;
    chat.current?.scrollIntoView({ block: "start", behavior: smooth ? "smooth" : "auto" });
  }, [selected]);
  useEffect(() => {
    const fit = () => {
      const section = chat.current;
      // Only for the field: scrolling when a button takes focus would move it away from the tap.
      const field = document.activeElement;
      if (section && phone() && field instanceof HTMLTextAreaElement && section.contains(field)) {
        section.scrollIntoView({ block: "end" });
      }
    };
    const section = chat.current;
    section?.addEventListener("focusin", fit);
    addEventListener("resize", fit);
    return () => {
      section?.removeEventListener("focusin", fit);
      removeEventListener("resize", fit);
    };
  }, []);
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
        <div className={`workbench${current && guideOpen ? " with-guide" : ""}`}>
          <aside className="catalog">
            <div className="panel-heading">
              <h2>Models</h2>
              <button type="button" id="refresh" className="quiet" aria-label="Refresh models" onClick={load}><Icon kind="refresh" /></button>
            </div>
            {models ? (
              <ModelPicker models={models.models} notEnabled={models.notEnabled} selected={selected} onSelect={pick} onEnabled={load} />
            ) : (
              <p className="hint">[ LOADING ]</p>
            )}
            {problem && <p className="notice-error" role="alert">{problem}</p>}
          </aside>
          <section className="chat" aria-label="Chat" ref={chat}>
            <div className="panel-heading">
              <h2>Conversation</h2>
              <div className="chat-actions">
                {current && (
                  <button type="button" id="guide" className="quiet" aria-expanded={guideOpen} onClick={() => setGuideOpen(!guideOpen)}>
                    Guide
                  </button>
                )}
                {confirming && started ? (
                  <div className="confirm" role="group" aria-label="Clear the conversation?">
                    <span className="eyebrow">Clear the conversation?</span>
                    <button type="button" className="quiet" onClick={() => setConfirming(false)} autoFocus>Cancel</button>
                    <button type="button" id="clear-confirm" onClick={clear}>Clear</button>
                  </div>
                ) : (
                  <button type="button" id="clear" className="quiet" onClick={() => setConfirming(true)} disabled={!started}>Clear</button>
                )}
              </div>
            </div>
            <CopilotChat key={threadId} threadId={threadId} agentId="analyst"
              labels={{
                chatInputPlaceholder: current ? `Ask about ${current.name}…` : "Select a model, or ask which data you can explore…",
                chatDisclaimerText: "Answers come from governed queries on the selected model. Check the query card for definitions and SQL.",
              }} />
          </section>
          {current && guideOpen && (
            <ModelGuide key={current.key} model={description?.model} error={description?.error} busy={agent.isRunning}
              onAsk={ask} onClose={() => setGuideOpen(false)} />
          )}
        </div>
      </main>
    </>
  );
}
