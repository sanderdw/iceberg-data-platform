'use strict';
// The Settings page every extension shows, opened from Settings in the header. Its first option, Environments,
// is where the extension is enabled for each of the caller's teams, with Enable and Disable for team
// administrators; pages that are about one team environment show its "not enabled" notice. An extension adds
// its own options after Environments. A reference copy lives in contracts/bridge/v1/settings/ of the platform;
// extensions copy it unchanged. Environments only calls the operations every extension shares
// (docs/develop/api-conventions.md):
//   GET    /api/v1/teams/{team}/environments                  list_environments
//   POST   /api/v1/teams/{team}/environments/{environment}    enable_environment
//   DELETE /api/v1/teams/{team}/environments/{environment}    disable_environment
// Usage, once the page knows who is signed in:
//   IcebergSettings.configure({
//     title: 'Ingestion',                        // the extension's name
//     request: (method, path) => promise,        // the page's own API helper; path is under /api/v1
//     enables: 'Its pipelines load data …',      // what enabling lets the extension do, one or two sentences
//     disables: 'Pipelines can no longer …',     // what disabling stops or removes
//     details: (entry, membership) => node,      // optional: more for one enabled environment, such as a link
//     sections: [{id, title, summary, render: (element, memberships) => {}}],   // optional: options after Environments
//     onChange: entry => {},                     // optional: after an enable or disable, with the new entry
//   });
//   IcebergSettings.link(element)                          the header link to #/settings
//   IcebergSettings.mount(element, memberships, {team, section})   the page; memberships are [{team, teamName, role}]
//   IcebergSettings.notice(membership, environment)       a "not enabled" notice, with Enable for administrators
//   IcebergSettings.change(membership, environment, on)   the confirmation and the call; resolves to the entry or null
//   IcebergSettings.href(team, section)                   #/settings/environments?team=<team>, or another option
// Routes: #/settings opens Environments, #/settings/<section> another option, ?team=<team> scrolls to one team.
// Plain DOM, no dependencies; every text is set as text, never markup.
(() => {
  const ENVIRONMENTS = ['development', 'acceptance', 'production'];
  const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
  const ADMINS = ['admin', 'bucket-admin'];
  const HREF = '#/settings';
  let options = null;

  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  };
  const button = (className, text, onclick) => {
    const node = make('button', className, text);
    node.type = 'button';
    node.addEventListener('click', onclick);
    return node;
  };
  const isAdmin = membership => ADMINS.includes(membership.role);
  const settings = () => {
    if (!options) throw new Error('IcebergSettings.configure() was not called.');
    return options;
  };
  const href = (team, section = 'environments') =>
    `${HREF}/${encodeURIComponent(section)}${team ? `?team=${encodeURIComponent(team)}` : ''}`;
  const path = (team, environment) =>
    `/teams/${encodeURIComponent(team)}/environments${environment ? '/' + encodeURIComponent(environment) : ''}`;

  function configure(value) {
    if (!value || !value.title || typeof value.request !== 'function') {
      throw new Error('IcebergSettings.configure() needs a title and a request function.');
    }
    options = value;
  }

  // A modal confirmation; resolves to true when confirmed.
  function confirmation(title, text, action, danger) {
    return new Promise(resolve => {
      const dialog = make('dialog', 'environments-dialog');
      const form = make('form');
      form.method = 'dialog';
      const cancel = make('button', 'quiet', 'Cancel');
      cancel.value = 'cancel';
      const confirm = make('button', danger ? 'danger' : '', action);
      confirm.value = 'confirm';
      const actions = make('div', 'environments-dialog-actions');
      actions.append(cancel, confirm);
      form.append(make('h2', '', title), make('p', '', text), actions);
      dialog.append(form);
      dialog.addEventListener('close', () => { dialog.remove(); resolve(dialog.returnValue === 'confirm'); });
      document.body.append(dialog);
      dialog.showModal();
      cancel.focus();
    });
  }

  // Asks, then enables or disables; resolves to the environment as list_environments shows it, or null when cancelled.
  async function change(membership, environment, on) {
    const {title, request, enables, disables, onChange} = settings();
    const where = `${membership.teamName} in ${LABELS[environment]}`;
    const ok = on
      ? await confirmation(`Enable ${title} for ${where}?`,
        `${enables || ''} The platform creates the team's service account for ${title} in ${LABELS[environment]} now; ` +
        'a team administrator can disable it again.', 'Enable', false)
      : await confirmation(`Disable ${title} for ${where}?`,
        `${disables || ''} The team's service account for ${title} in ${LABELS[environment]} is revoked.`, 'Disable', true);
    if (!ok) return null;
    const entry = await request(on ? 'POST' : 'DELETE', path(membership.team, environment));
    if (onChange) await onChange(entry);
    return entry;
  }

  function link(anchor) {
    anchor.replaceChildren();
    const item = make('a', 'settings-link', 'Settings');
    item.href = HREF;
    anchor.append(item);
    anchor.hidden = false;
    const current = () => {
      const hash = location.hash.split('?')[0];
      if (hash === HREF || hash.startsWith(HREF + '/')) item.setAttribute('aria-current', 'page');
      else item.removeAttribute('aria-current');
    };
    current();
    window.addEventListener('hashchange', current);
  }

  function notice(membership, environment) {
    const {title} = settings();
    const box = make('div', 'environments-notice');
    box.setAttribute('role', 'status');
    box.append(make('span', '', `${title} is not enabled for ${membership.teamName} in ${LABELS[environment]}.`));
    const status = make('span', 'environments-error');
    if (isAdmin(membership)) {
      const enable = button('quiet', 'Enable', async () => {
        enable.disabled = true;
        status.textContent = '';
        try { await change(membership, environment, true); } catch (error) { status.textContent = error.message; }
        enable.disabled = false;
      });
      enable.setAttribute('aria-label', `Enable ${title} for ${membership.teamName} in ${LABELS[environment]}`);
      box.append(enable);
    } else {
      const more = make('a', '', 'Settings');
      more.href = href(membership.team);
      const ask = make('span', '', 'A team administrator enables it in ');
      ask.append(more, '.');
      box.append(ask);
    }
    box.append(status);
    return box;
  }

  function cell(membership, environment, entry, refresh, status) {
    const {title, details} = settings();
    const on = Boolean(entry && entry.enabled);
    const node = make('div', `environments-cell ${on ? 'on' : 'off'}`);
    const head = make('div', 'environments-cell-head');
    const state = make('span', 'environments-state', entry ? (on ? 'On' : 'Off') : '…');
    head.append(make('strong', '', LABELS[environment]), state);
    node.append(head);
    if (!entry) return node;
    const databases = (entry.databases || []).map(d => d.name);
    node.append(make('p', '', on ? `Enabled by ${entry.enabledBy || 'a team administrator'}` : 'Not enabled'),
      make('p', '', databases.length ? `Databases: ${databases.join(', ')}` : 'No databases yet'));
    const extra = on && details ? details(entry, membership) : null;
    if (extra) node.append(extra);
    if (isAdmin(membership)) {
      const action = button(on ? 'danger' : 'quiet', on ? 'Disable' : 'Enable', async () => {
        action.disabled = true;
        status.textContent = '';
        try {
          if (await change(membership, environment, !on)) await refresh();
        } catch (error) { status.textContent = error.message; }
        action.disabled = false;
      });
      action.setAttribute('aria-label', `${on ? 'Disable' : 'Enable'} ${title} for ${membership.teamName} in ${LABELS[environment]}`);
      node.append(action);
    }
    return node;
  }

  function team(membership) {
    const {request} = settings();
    const section = make('section', 'environments-team');
    section.id = `environments-${membership.team}`;
    const grid = make('div', 'environments-grid');
    const status = make('p', 'environments-error');
    status.setAttribute('role', 'alert');
    section.append(make('h3', '', membership.teamName), grid, status);
    if (!isAdmin(membership)) section.append(make('p', 'environments-hint', 'A team administrator of this team enables and disables it.'));
    const draw = entries => grid.replaceChildren(...ENVIRONMENTS.map(environment =>
      cell(membership, environment, entries && entries.find(e => e.environment === environment), refresh, status)));
    async function refresh() {
      try {
        draw((await request('GET', path(membership.team))).environments);
      } catch (error) { draw(null); status.textContent = error.message; }
    }
    draw(null);
    refresh();
    return section;
  }

  // The first option: where the extension is on, per team and environment, and what that means.
  function environments(anchor, memberships) {
    const {title, enables, disables} = settings();
    const facts = make('dl', 'settings-facts');
    for (const [term, text] of [
      ['When it is on', `${enables || ''} It works through a service account of its own for that team and environment, ` +
        "which reaches only the team's databases there and the data shared with the team there."],
      ['When it is turned off', `${disables || ''} The service account is revoked at once.`],
      ['Who can change it', 'Team administrators of the team. Every member of the team sees where it is on.'],
    ]) {
      const item = make('div');
      item.append(make('dt', '', term), make('dd', '', text));
      facts.append(item);
    }
    const agents = make('p', 'settings-small', 'AI agents and scripts do the same with ');
    agents.append(make('code', '', 'enable_environment'), ' and ', make('code', '', 'disable_environment'), ', or with POST and DELETE on ',
      make('code', '', '/api/v1/teams/{team}/environments/{environment}'), '.');
    anchor.append(make('p', 'eyebrow', 'ENVIRONMENTS'), make('h2', '', `Turn ${title} on per team environment`),
      make('p', 'settings-lead', `${title} only works for a team in an environment (development, acceptance or ` +
        'production) where a team administrator turned it on. Each team decides this per environment, so a team can ' +
        'try it in development long before it lets it touch production.'),
      facts,
      ...(memberships.length ? memberships.map(team) : [make('p', '', 'You are not a member of a team yet.')]),
      agents);
  }

  function mount(anchor, memberships, {team: focus, section} = {}) {
    const {title, sections = []} = settings();
    const all = [{id: 'environments', title: 'Environments', summary: `Where ${title} is on for your teams`,
      render: environments}, ...sections];
    const current = all.find(s => s.id === section) || all[0];
    const page = make('div', 'settings-page');
    const head = make('div', 'settings-head');
    head.append(make('p', 'eyebrow', 'SETTINGS'), make('h1', '', 'Settings'),
      make('p', 'settings-lead', `How ${title} works for your teams. Choose a setting to see what it does and change it.`));
    const nav = make('nav', 'settings-nav');
    nav.setAttribute('aria-label', 'Settings');
    for (const s of all) {
      const item = make('a');
      item.href = href(null, s.id);
      item.append(make('strong', '', s.title));
      if (s.summary) item.append(make('span', '', s.summary));
      if (s === current) item.setAttribute('aria-current', 'page');
      nav.append(item);
    }
    const body = make('section', 'settings-body');
    const layout = make('div', 'settings-layout');
    layout.append(nav, body);
    page.append(head, layout);
    anchor.replaceChildren(page);
    current.render(body, memberships);
    const target = focus && document.getElementById(`environments-${focus}`);
    if (target) target.scrollIntoView({block: 'start'});
  }

  window.IcebergSettings = {configure, link, mount, notice, change, href, ENVIRONMENTS, LABELS};
})();
