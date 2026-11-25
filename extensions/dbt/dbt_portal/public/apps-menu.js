'use strict';
// The Apps menu every extension shows: the user portal's pages and the platform's extensions, from the
// Bridge's discovery (capability `app-directory`), which the extension's gateway passes to its page.
// A reference copy lives in contracts/bridge/v1/apps-menu/ of the platform; extensions copy it unchanged.
// Usage: IcebergApps.mount(element, {workspace: [{id, title, url}], extensions: [{id, title, purpose, origin}],
// current: '<this extension id>'}). Mount again on the same element to redraw it: the previous menu's listeners
// are removed. Plain DOM, no dependencies; every text is set as text, never markup.
(() => {
  // The listeners of each mounted menu, so that mounting again removes those of the previous one.
  const mounted = new WeakMap();

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };

  function link(url, title, purpose, current) {
    const item = make(current ? 'span' : 'a', 'apps-link');
    if (current) item.setAttribute('aria-current', 'page');
    else { item.href = url; item.target = '_blank'; item.rel = 'noopener'; }
    item.append(make('strong', '', title));
    if (purpose || current) item.append(make('span', '', current ? 'You are here' : purpose));
    return item;
  }

  function mount(anchor, apps) {
    const workspace = (apps && apps.workspace) || [], extensions = (apps && apps.extensions) || [];
    if (mounted.has(anchor)) mounted.get(anchor).abort();
    const listeners = new AbortController();
    mounted.set(anchor, listeners);
    const { signal } = listeners;
    anchor.replaceChildren();
    anchor.classList.add('apps-anchor');
    if (!workspace.length && !extensions.length) { anchor.hidden = true; return; }
    const id = 'apps-menu-' + Math.random().toString(36).slice(2, 8);
    const toggle = make('button', 'apps-toggle', 'Apps');
    toggle.type = 'button';
    toggle.setAttribute('aria-expanded', 'false');
    toggle.setAttribute('aria-controls', id);
    const menu = make('div', 'apps-menu');
    menu.id = id;
    menu.hidden = true;
    if (workspace.length) {
      const grid = make('div', 'apps-grid compact');
      for (const page of workspace) grid.append(link(page.url, page.title));
      menu.append(make('p', 'apps-group', 'Workspace'), grid);
    }
    if (extensions.length) {
      const grid = make('div', 'apps-grid');
      for (const app of extensions) {
        grid.append(link(app.origin + '/', app.title || app.id, app.purpose, app.id === apps.current));
      }
      menu.append(make('p', 'apps-group', 'Extensions · each opens in a new tab'), grid);
    }
    const close = () => { toggle.setAttribute('aria-expanded', 'false'); menu.hidden = true; };
    toggle.addEventListener('click', () => {
      const open = toggle.getAttribute('aria-expanded') !== 'true';
      toggle.setAttribute('aria-expanded', String(open));
      menu.hidden = !open;
    }, { signal });
    menu.addEventListener('click', event => { if (event.target.closest('a')) close(); }, { signal });
    document.addEventListener('click', event => { if (!anchor.contains(event.target)) close(); }, { signal });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !menu.hidden) { close(); toggle.focus(); }
    }, { signal });
    anchor.append(toggle, menu);
    anchor.hidden = false;
  }

  window.IcebergApps = { mount };
})();
