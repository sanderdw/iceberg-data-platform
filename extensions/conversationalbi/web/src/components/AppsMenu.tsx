import { useEffect, useRef, useState } from "react";

// The Apps menu every extension shows (contracts/bridge/v1/apps-menu in the platform): the user portal's
// pages and the platform's extensions, from the Bridge's discovery (capability `app-directory`). Same
// markup and stylesheet (apps-menu.css) as the reference script, in React.
export type Apps = {
  workspace: { id: string; title: string; url: string }[];
  extensions: { id: string; title: string; purpose: string; origin: string }[];
};

export function AppsMenu({ apps, current }: { apps?: Apps; current: string }) {
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const outside = (event: MouseEvent) => {
      if (!anchor.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setOpen(false); toggle.current?.focus(); }
    };
    document.addEventListener("click", outside);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("click", outside); document.removeEventListener("keydown", escape); };
  }, [open]);
  if (!apps || (!apps.workspace.length && !apps.extensions.length)) return null;
  return (
    <div className="apps-anchor" ref={anchor}>
      <button ref={toggle} type="button" className="apps-toggle" aria-expanded={open} aria-controls="apps-menu"
        onClick={() => setOpen(!open)}>Apps</button>
      <div id="apps-menu" className="apps-menu" hidden={!open} onClick={(e) => { if ((e.target as Element).closest("a")) setOpen(false); }}>
        {apps.workspace.length > 0 && <>
          <p className="apps-group">Workspace</p>
          <div className="apps-grid compact">
            {apps.workspace.map((page) => (
              <a key={page.id} className="apps-link" href={page.url} target="_blank" rel="noopener"><strong>{page.title}</strong></a>
            ))}
          </div>
        </>}
        {apps.extensions.length > 0 && <>
          <p className="apps-group">Extensions · each opens in a new tab</p>
          <div className="apps-grid">
            {apps.extensions.map((app) => app.id === current
              ? <span key={app.id} className="apps-link" aria-current="page"><strong>{app.title || app.id}</strong><span>You are here</span></span>
              : <a key={app.id} className="apps-link" href={app.origin + "/"} target="_blank" rel="noopener">
                  <strong>{app.title || app.id}</strong>{app.purpose && <span>{app.purpose}</span>}
                </a>)}
          </div>
        </>}
      </div>
    </div>
  );
}
