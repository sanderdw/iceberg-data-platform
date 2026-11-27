import { useEffect, useId, useRef, useState } from "react";
import { useTheme } from "../theme";
import type { Membership } from "./Settings";

// The user portal's words for the team roles, as IcebergAccount.teams in the reference script.
const ROLES: Record<string, string> = {
  reader: "Read", writer: "Read & write", admin: "Administrator", "bucket-admin": "Database + bucket administration",
};
const SHOWN = 3;
export function teams(memberships: Membership[] = []): string[] {
  const lines = memberships.slice(0, SHOWN).map((m) => `${m.teamName || m.team} · ${ROLES[m.role] ?? m.role}`);
  if (memberships.length > SHOWN) lines.push(`+${memberships.length - SHOWN} more teams`);
  return lines;
}

// The account menu at the right end of every header (contracts/bridge/v1/account-menu in the platform), as in the
// portals: before sign-in only the theme switch; after it, the person's name, which opens who is signed in (with
// `details`, such as their teams), Getting started (`guide`, a URL that opens in a new tab), Light or Dark mode and
// Sign out. Same markup, words and stylesheet (account-menu.css) as the reference script, in React. The document
// listeners exist only while the menu is open.
export function AccountMenu({ name, details = [], guide, signOut }: {
  name?: string; details?: string[]; guide?: string; signOut?: () => Promise<void> | void;
}) {
  const [theme, toggleTheme] = useTheme();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const anchor = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const menu = useId();
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
  const themeLabel = theme === "dark" ? "Light / ○" : "Dark / ●";
  const themeAria = `Switch to ${theme === "dark" ? "light" : "dark"} mode`;
  if (!name) {
    return (
      <div className="account-anchor" ref={anchor}>
        <button type="button" className="account-theme" onClick={toggleTheme} aria-label={themeAria}>{themeLabel}</button>
      </div>
    );
  }
  const out = async () => {
    setBusy(true);
    try { await signOut?.(); } finally { setBusy(false); }
  };
  return (
    <div className="account-anchor" ref={anchor}>
      <button type="button" className="account-toggle" ref={toggle} title={name} aria-expanded={open} aria-controls={menu}
        onClick={() => setOpen(!open)}><span>{name}</span></button>
      <div className="account-menu" id={menu} hidden={!open}>
        <div className="account-identity">
          <strong>{name}</strong>
          {details.filter(Boolean).map((line) => <span key={line}>{line}</span>)}
        </div>
        {guide && (
          <a className="account-item" href={guide} target="_blank" rel="noopener" onClick={() => setOpen(false)}>
            Getting started ↗
          </a>
        )}
        <button type="button" className="account-item" onClick={toggleTheme} aria-label={themeAria}>{themeLabel}</button>
        <button type="button" className="account-item" disabled={busy} onClick={out}>Sign out</button>
      </div>
    </div>
  );
}
