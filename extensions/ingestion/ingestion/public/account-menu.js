'use strict';
// The account menu at the right end of every header: the administration portal, the user portal and every extension.
// Before sign-in only the theme switch; after it, the person's name, which opens who is signed in, Getting started,
// Light or Dark mode and Sign out. The header's order is the same everywhere: the page's own navigation, then the
// Read-only pill (when the person can change nothing here), Settings (when the page has one), Apps and this menu.
// A reference copy lives in contracts/bridge/v1/account-menu/ of the platform; extensions copy it unchanged.
// The theme is the platform's: data-theme on <html>, kept in localStorage under `iceberg-theme`, so every portal and
// extension on the same browser shows the same one. A change is announced as the window event `iceberg-theme`.
// Usage: IcebergAccount.mount(element, {name, details, guide, guideCurrent, signOut}) after sign-in,
// IcebergAccount.mount(element) before it. `details` are lines under the name, such as the person's role; an extension
// passes IcebergAccount.teams(me.memberships): each team with the person's role in it, as the user portal names it.
// `guide` is the Getting started page: a URL (opens in a new tab) or a function (called; the menu closes);
// `guideCurrent` marks it as the page shown. `signOut` is the page's own sign-out, such as POST /auth/logout and then
// its logoutUrl. Mount again on the same element to redraw it: the previous menu's listeners are removed.
// Plain DOM; every text is set as text.
(() => {
  const KEY = 'iceberg-theme';
  const mounted = new WeakMap();

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  const current = () => (document.documentElement.dataset.theme === 'light' ? 'light' : 'dark');
  function apply(theme, remember = true) {
    document.documentElement.dataset.theme = theme;
    const meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'light' ? '#f5f5f5' : '#000000');
    if (remember) { try { localStorage.setItem(KEY, theme); } catch { /* private mode */ } }
    window.dispatchEvent(new CustomEvent('iceberg-theme', {detail: theme}));
  }
  // The theme this browser chose, applied as the page loads.
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch { /* private mode */ }
  apply(saved === 'light' ? 'light' : 'dark', false);

  // The user portal's words for the team roles.
  const ROLES = {reader: 'Read', writer: 'Read & write', admin: 'Administrator', 'bucket-admin': 'Database + bucket administration'};
  const SHOWN = 3;
  function teams(memberships = []) {
    const lines = memberships.slice(0, SHOWN).map(m => `${m.teamName || m.team} · ${ROLES[m.role] || m.role}`);
    if (memberships.length > SHOWN) lines.push(`+${memberships.length - SHOWN} more teams`);
    return lines;
  }

  const label = theme => (theme === 'dark' ? 'Light / ○' : 'Dark / ●');

  function themeButton(className) {
    const button = make('button', className, label(current()));
    button.type = 'button';
    const sync = () => {
      button.textContent = label(current());
      button.setAttribute('aria-label', `Switch to ${current() === 'dark' ? 'light' : 'dark'} mode`);
    };
    sync();
    button.addEventListener('click', () => { apply(current() === 'dark' ? 'light' : 'dark'); sync(); });
    return {button, sync};
  }

  function mount(anchor, {name, details = [], guide, guideCurrent = false, signOut} = {}) {
    if (mounted.has(anchor)) mounted.get(anchor).abort();
    const listeners = new AbortController();
    mounted.set(anchor, listeners);
    const {signal} = listeners;
    anchor.replaceChildren();
    anchor.classList.add('account-anchor');
    anchor.hidden = false;
    const {button: theme, sync} = themeButton(name ? 'account-item' : 'account-theme');
    window.addEventListener('iceberg-theme', sync, {signal});
    if (!name) { anchor.append(theme); return; }

    const id = 'account-menu-' + Math.random().toString(36).slice(2, 8);
    // The name in its own span, so a long one ends in an ellipsis and the chevron stays whole.
    const toggle = make('button', 'account-toggle');
    toggle.append(make('span', '', name));
    toggle.type = 'button';
    toggle.title = name;
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', id);
    const menu = make('div', 'account-menu');
    menu.id = id;
    menu.hidden = true;
    const close = () => { toggle.setAttribute('aria-expanded', 'false'); menu.hidden = true; };

    const identity = make('div', 'account-identity');
    identity.append(make('strong', '', name), ...details.filter(Boolean).map(line => make('span', '', line)));
    menu.append(identity);
    if (typeof guide === 'string' && guide) {
      const link = make('a', 'account-item', 'Getting started ↗');
      link.href = guide; link.target = '_blank'; link.rel = 'noopener';
      link.addEventListener('click', close, {signal});
      menu.append(link);
    } else if (typeof guide === 'function') {
      const button = make('button', 'account-item', 'Getting started');
      button.type = 'button';
      if (guideCurrent) button.setAttribute('aria-current', 'page');
      button.addEventListener('click', () => { close(); guide(); }, {signal});
      menu.append(button);
    }
    const out = make('button', 'account-item', 'Sign out');
    out.type = 'button';
    out.addEventListener('click', async () => {
      out.disabled = true;
      try { await signOut(); } finally { out.disabled = false; }
    }, {signal});
    menu.append(theme, out);
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      menu.hidden = !open;
    }, {signal});
    document.addEventListener('click', event => { if (!anchor.contains(event.target)) close(); }, {signal});
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !menu.hidden) { close(); toggle.focus(); }
    }, {signal});
    anchor.append(toggle, menu);
  }

  window.IcebergAccount = {mount, apply, current, teams};
})();
