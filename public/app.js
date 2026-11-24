const $ = selector => document.querySelector(selector);
let loginUrl = null;
// Who is signed in: platform administrators change the platform, team administrators only look (read-only).
let viewer = { access: 'admin', user: '', userPortalUrl: '', userPortalPages: [] };
// Setting up starts with a team, so the portal opens on Teams.
const state = { page: 'teams', query: '', team: '', environment: '', data: null, loading: false, status: '' };
const adminPages = ['teams', 'users', 'databases', 'shares', 'explorer', 'infrastructure', 'guide'];
let refreshingOverview = false, activeMutations = 0;
function readAdminRoute() {
  const [page, query = ''] = location.hash.slice(1).split('?');
  const params = new URLSearchParams(query);
  state.page = adminPages.includes(page) ? page : 'teams';
  state.query = params.get('search') || ''; state.team = params.get('team') || ''; state.environment = params.get('environment') || '';
}
function saveAdminRoute(replace = false) {
  const params = new URLSearchParams();
  if (state.query) params.set('search', state.query);
  if (state.team) params.set('team', state.team);
  if (state.environment) params.set('environment', state.environment);
  const hash = `#${state.page}${params.size ? '?' + params : ''}`;
  if (location.hash !== hash) history[replace ? 'replaceState' : 'pushState'](null, '', hash);
}
readAdminRoute();
window.addEventListener('hashchange', () => { readAdminRoute(); if (state.data) { closeModal(); render(); } });
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const paths = {
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.87M15 3a4 4 0 0 1 0 8"/><circle cx="9" cy="7" r="4"/>',
  layers: '<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  arrow: '<path d="M7 17 17 7M7 7h10v10"/>', check: '<path d="m5 12 4 4L19 6"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 2M5 16a8 8 0 0 0 13 2"/>',
  key: '<circle cx="8" cy="8" r="5"/><path d="m12 12 9 9m-5-5 3-3m-1 5 3-3"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>', logout: '<path d="M9 3H4v18h5m5-14 5 5-5 5M8 12h13"/>',
  settings: '<path d="M4 5h16M4 12h16M4 19h16"/><circle cx="8" cy="5" r="2"/><circle cx="16" cy="12" r="2"/><circle cx="10" cy="19" r="2"/>',
  book: '<path d="M12 5v16M3 3c4-1 7 0 9 2 2-2 5-3 9-2v16c-4-1-7 0-9 2-2-2-5-3-9-2Z"/>',
  model: '<circle cx="6" cy="6" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="12" cy="18" r="2.5"/><path d="M8.5 6h7M7 8.2l4 7.6M17 8.2l-4 7.6"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.database}</svg>`;
const roleNames = { reader: 'Read', writer: 'Read & write', admin: 'Administrator', 'bucket-admin': 'Database + bucket administration' };
const envNames = { development: 'Development', acceptance: 'Acceptance', production: 'Production' };
const date = timestamp => timestamp ? new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(timestamp)) : 'Just now';
// A stopped server answers through the proxy with plain text, or not at all.
const serverDown = 'The admin server is not responding. Try again in a moment.';
async function api(path, method = 'GET', body) {
  if (method !== 'GET') activeMutations++;
  try {
  let response, result;
  try { response = await fetch(`/api${path}`, { method, headers: { 'Content-Type': 'application/json', 'X-Portal-Request': '1' }, ...(body ? { body: JSON.stringify(body) } : {}) }); }
  catch { throw new Error(serverDown); }
  try { result = await response.json(); }
  catch { throw new Error(response.status >= 500 ? serverDown : `The request failed with HTTP ${response.status}.`); }
  if (!response.ok) {
    if (response.status === 401 && path !== '/session') { closeModal(); login(); }
    throw new Error(result.error || 'The action failed.');
  }
  return result;
  } finally { if (method !== 'GET') activeMutations--; }
}
function notifyStatus(message) { state.status = message; const target = modal.open ? modal.querySelector('.modal-status') : $('#status'); if (target) target.textContent = `[ ${message} ]`; }
function themeControls() { for (const button of document.querySelectorAll('[data-theme-toggle]')) { const dark = document.documentElement.dataset.theme === 'dark'; button.textContent = dark ? 'Light / ○' : 'Dark / ●'; button.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} mode`); button.onclick = () => { const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme; try { localStorage.setItem('iceberg-theme', theme); } catch {} themeControls(); }; } }
try { document.documentElement.dataset.theme = localStorage.getItem('iceberg-theme') || 'dark'; } catch { document.documentElement.dataset.theme = 'dark'; }
function login() {
  stopInfrastructure();
  state.data = null;
  $('#app').innerHTML = `<main class="login"><div class="login-story"><a class="brand" href="/"> <img src="/favicon.svg" alt="">iceberg<span> / administration</span></a><div><span class="eyebrow">PLATFORM ADMINISTRATION</span><h1>Administration<span>.</span></h1><p>Manage teams, accounts, Iceberg databases, active data shares and live stack health from one control plane.</p><div class="system-label">[ 01 ] TEAMS &amp; ACCESS<br>[ 02 ] DATABASES &amp; SHARES<br>[ 03 ] STACK HEALTH</div></div><small>OPEN STANDARDS / APACHE ICEBERG / CONTROL PLANE</small></div><section class="login-form"><button class="theme-button" data-theme-toggle></button><span class="pill"><span class="dot"></span> Control plane</span><h2>Sign in to Administration</h2><p>Manage teams, databases, users, data shares and stack health.</p><form id="login-form"><label>Admin password<input type="password" name="password" autocomplete="current-password" required maxlength="1024" autofocus placeholder="Enter your password"></label><p class="form-error" role="alert"></p><button class="button primary" type="submit">Open administration ${icon('arrow')}</button></form><p class="subtle">The administrator password is configured via <code>PORTAL_PASSWORD</code>.</p></section></main>`;
  if (loginUrl) {
    $('#login-form').innerHTML = '<a class="button primary" href="/auth/login">Sign in with Keycloak</a>';
    $('#login-form a').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`;
    $('.login-form .subtle').textContent = 'Platform administrators manage the stack. Team administrators can sign in with read-only access.';
  }
  themeControls();
  $('#login-form').onsubmit = async event => {
    event.preventDefault(); const button = event.target.querySelector('button'); button.disabled = true;
    try { await api('/session', 'POST', Object.fromEntries(new FormData(event.target))); await start(); }
    catch (e) { const error = $('.form-error'); if (error) error.textContent = e.message; }
    finally { button.disabled = false; }
  };
}
async function load() {
  try { state.data = await api('/overview'); render(); }
  catch (error) { if (state.data) notifyStatus(error.message); else if (!$('#login-form')) $('#app').innerHTML = `<main class="initial"><p>${esc(error.message)}</p><button class="button" id="retry">Try again</button></main>`; if ($('#retry')) $('#retry').onclick = load; }
}
// The dialog closes only once the page shows the change, so a search or click right after it never meets the rows from before.
async function saved(message) {
  let data;
  try { data = await api('/overview'); }
  catch (error) { closeModal(); notifyStatus(error.message); return; }
  state.data = data; closeModal(); notifyStatus(message); render();
}
// The Apps menu (apps-menu.js) is mounted once and moved into each new header; it is mounted again only when its links change.
const appsAnchor = document.createElement('div');
let mountedApps = '';
function placeApps() {
  const apps = { workspace: viewer.userPortalPages, extensions: state.data.extensions || [] };
  if (JSON.stringify(apps) !== mountedApps) { IcebergApps.mount(appsAnchor, apps); mountedApps = JSON.stringify(apps); }
  $('#apps').replaceWith(appsAnchor);
}
// The account menu closes on Escape or a click elsewhere, as the Apps menu does for itself.
function accountMenu(open) {
  const toggle = $('#account-toggle');
  if (toggle) { toggle.setAttribute('aria-expanded', String(open)); $('#account-menu').hidden = !open; }
}
document.addEventListener('click', event => { if (!event.target.closest('#account')) accountMenu(false); });
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('#account-toggle')?.getAttribute('aria-expanded') === 'true') { accountMenu(false); $('#account-toggle').focus(); }
});
let renderedPage = null;
function render() {
  saveAdminRoute();
  stopInfrastructure();
  // A status belongs to the page where it happened; navigating away clears it.
  if (state.page !== renderedPage) { state.status = ''; renderedPage = state.page; }
  const { databases, teams, health } = state.data;
  const online = health.status === 'online';
  const titles = { databases: 'Databases', users: 'Users', teams: 'Teams', shares: 'Data shares', explorer: 'Catalog', infrastructure: 'Infrastructure', guide: 'Getting started' };
  const isViewer = viewer.access === 'viewer';
  const pages = [['teams', 'Teams'], ['users', 'Users'], ['databases', 'Databases'], ['shares', 'Data shares'], ['explorer', 'Catalog'], ['infrastructure', 'Infrastructure']];
  const tabs = pages.map(([id, label]) => `<button data-page="${id}" class="nav-item ${state.page === id ? 'active' : ''}"${state.page === id ? ' aria-current="page"' : ''}>${label}${id === 'databases' ? `<span class="nav-count">${databases.length}</span>` : ''}</button>`).join('');
  // The account menu: who is signed in, the provider's status, the guide, the theme and signing out.
  const account = `<div id="account" class="menu-anchor"><button id="account-toggle" class="menu-toggle" aria-expanded="false" aria-controls="account-menu"><span>${esc(viewer.user || 'Account')}</span></button><div id="account-menu" class="header-menu" hidden><div class="account-identity">${viewer.user ? `<strong>${esc(viewer.user)}</strong>` : ''}<span>${isViewer ? 'Team administrator · read-only' : 'Platform administrator'}</span><span class="account-status"><span class="dot ${online ? '' : 'offline'}"></span>${online ? esc(health.provider) + ' online' : 'Stack offline'}</span></div><button class="menu-item" data-page="guide"${state.page === 'guide' ? ' aria-current="page"' : ''}>Getting started</button><button class="menu-item" data-theme-toggle></button><button class="menu-item" id="logout">Sign out</button></div></div>`;
  // A team administrator changes nothing here; on the pages where they might try, a bar says where they can.
  const readOnlyBar = isViewer && ['teams', 'users', 'databases', 'shares'].includes(state.page) ? `<div class="read-only-bar" role="note"><span>You have read-only access. Manage your team’s databases and data shares in Workspaces.</span>${viewer.userPortalUrl ? `<a href="${esc(viewer.userPortalUrl)}" target="_blank" rel="noopener">Open Workspaces ↗</a>` : ''}</div>` : '';
  $('#app').innerHTML = `<div class="shell"><header class="header"><a class="brand" href="/"> <img src="/favicon.svg" alt="">iceberg<span> / administration</span></a><nav aria-label="Administration">${tabs}</nav><div class="header-actions">${isViewer ? '<span class="read-only-badge" title="Team administrators can inspect everything here; only platform administrators change it.">Read-only</span>' : ''}<div id="apps"></div>${account}</div></header><main class="content"><div class="page-heading"><div><div class="eyebrow">PLATFORM ADMINISTRATION</div><h1>${titles[state.page]}<span>.</span></h1><p>${({ databases: 'Iceberg catalogs and dedicated storage buckets, organized by team and environment.', users: 'Accounts and per-team database roles across your workspaces.', teams: 'Teams own databases, memberships and extension service accounts.', shares: 'Tables, views and semantic models that team administrators share with other teams and external parties.', explorer: 'Browse all databases, namespaces, tables, views and semantic models across the platform.', infrastructure: 'Live health, compute and storage across your stack.', guide: 'Portal, AI agent or API. Three ways to configure the platform.' })[state.page]}</p></div>${['databases', 'users', 'teams'].includes(state.page) ? `<button class="button primary" id="create" ${!online || (state.page !== 'teams' && !teams.length) ? 'disabled' : ''}>${icon('plus')}${({ databases: 'Create database', users: 'Create user', teams: 'Create team' })[state.page]}</button>` : ''}</div>${readOnlyBar}${!online ? '<div class="alert" role="alert">The data provider is unavailable. Start the stack and refresh the page.</div>' : ''}<div id="status" class="inline-status" role="status" aria-live="polite">${state.status ? `[ ${esc(state.status)} ]` : ''}</div><div id="page-content"></div><footer><span><span class="dot"></span> ICEBERG / CONTROL PLANE</span><span>Iceberg Data Platform <span class="footer-divider">/</span> Administration</span></footer></main></div>`;
  document.body.classList.toggle('read-only', viewer.access === 'viewer');
  for (const nav of document.querySelectorAll('[data-page]')) nav.onclick = () => { state.page = nav.dataset.page; state.query = ''; render(); };
  placeApps();
  $('#account-toggle').onclick = () => accountMenu($('#account-toggle').getAttribute('aria-expanded') !== 'true');
  $('#logout').onclick = async () => { try { const result = await api('/session', 'DELETE'); if (result.logoutUrl) { location.assign(result.logoutUrl); return; } login(); } catch (e) { notifyStatus(e.message); } };
  if ($('#create')) $('#create').onclick = () => state.page === 'databases' ? databaseModal() : state.page === 'teams' ? teamModal() : userModal();
  themeControls();
  renderPage();
}
function renderPage() {
  const { databases, users, health } = state.data;
  if (state.page === 'explorer') { renderExplorer(); return; }
  if (state.page === 'teams') { renderTeams(); return; }
  if (state.page === 'shares') { renderShares(); return; }
  if (state.page === 'infrastructure') { renderInfrastructure(); return; }
  if (state.page === 'guide') { renderGuide(); return; }
  const isDb = state.page === 'databases';
  const teams = state.data.teams;
  $('#page-content').innerHTML = `<section class="panel"><div class="panel-heading"><div><h2>${isDb ? 'All databases' : 'All users'} <span class="number-badge">${isDb ? databases.length : users.length}</span></h2><p>${isDb ? 'Catalog and storage, organized by team.' : 'Service accounts with access to team databases.'}</p></div><button class="icon-button" id="refresh" aria-label="Refresh">${icon('refresh')}</button></div><div class="filters"><label class="search">${icon('search')}<input id="search" placeholder="${isDb ? 'Search databases…' : 'Search users…'}" value="${esc(state.query)}" aria-label="Search"></label>${isDb ? `<select id="team-filter" aria-label="Filter by team"><option value="">All teams</option>${teams.map(t => `<option value="${esc(t.id)}" ${t.id === state.team ? 'selected' : ''}>${esc(t.name)}</option>`).join('')}</select><select id="env-filter" aria-label="Filter by environment"><option value="">All environments</option>${Object.entries(envNames).map(([v, t]) => `<option value="${v}" ${v === state.environment ? 'selected' : ''}>${t}</option>`).join('')}</select>` : '<span class="filter-note">Credentials are shown only once</span>'}</div><div id="table"></div></section><div class="bottom-callout"><div class="callout-icon">${icon('layers')}</div><div><h3>Explore and share your data</h3><p>Connect through Iceberg REST or S3, or follow the guide for API and AI agent setup.</p></div><button id="guide-link">Open the guide ${icon('arrow')}</button></div>`;
  $('#search').oninput = event => { state.query = event.target.value; saveAdminRoute(true); renderTable(); };
  if (keycloakUsers) {
    if (!isDb) $('.panel-heading p').textContent = 'Keycloak accounts with access to team databases.';
    if (!isDb) $('.filter-note').textContent = 'Sign-in with Keycloak · permissions managed here';
    $('.bottom-callout p').textContent = 'Sign in to Workspaces to browse catalogs, run marimo notebooks and manage team data shares.';
  }
  if (isDb) { $('#team-filter').onchange = e => { state.team = e.target.value; saveAdminRoute(true); renderTable(); }; $('#env-filter').onchange = e => { state.environment = e.target.value; saveAdminRoute(true); renderTable(); }; }
  $('#refresh').onclick = load;
  $('#guide-link').onclick = () => { state.page = 'guide'; render(); };
  renderTable();
}
const teamName = id => state.data.teams.find(t => t.id === id)?.name || id;
const teamOptions = selected => state.data.teams.map(t => `<option value="${esc(t.id)}" ${t.id === selected ? 'selected' : ''}>${esc(t.name)}</option>`).join('');
const roleOptions = selected => Object.entries(roleNames).filter(([value]) => !keycloakUsers || value !== 'bucket-admin').map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>${esc(label)}</option>`).join('');
const membershipRows = (current = {}) => `<fieldset class="team-choices"><legend>Teams and access · select at least one</legend>${state.data.teams.map(t => `<div class="team-role"><label><input type="checkbox" name="teams" value="${esc(t.id)}" ${current[t.id] ? 'checked' : ''}>${esc(t.name)}</label><select name="role:${esc(t.id)}" aria-label="Access for ${esc(t.name)}" ${current[t.id] ? '' : 'disabled'}>${roleOptions(current[t.id] || 'reader')}</select></div>`).join('')}</fieldset>`;
function bindMembershipRows() { for (const box of document.querySelectorAll('#modal-form [name=teams]')) box.onchange = () => { box.closest('.team-role').querySelector('select').disabled = !box.checked; }; }
const userRoles = user => Object.fromEntries(user.memberships.map(m => [m.team, m.role]));
function renderTeams() {
  const { teams, databases, users } = state.data;
  const ro = viewer.access === 'viewer';
  $('#page-content').innerHTML = `<section class="panel"><div class="panel-heading"><div><h2>All teams <span class="number-badge">${teams.length}</span></h2><p>A central place for ownership and membership.</p></div><button class="icon-button" id="refresh" aria-label="Refresh">${icon('refresh')}</button></div><div class="filters"><label class="search">${icon('search')}<input id="search" aria-label="Search" placeholder="Search teams…" value="${esc(state.query)}"></label></div><div id="team-table"></div></section>`;
  function table() {
    const items = teams.filter(t => `${t.name} ${t.description}`.toLowerCase().includes(state.query.toLowerCase()));
    $('#team-table').innerHTML = items.length ? `<div class="table-scroll"><table><thead><tr><th>Team</th><th>Databases</th><th>Members</th>${ro ? '' : '<th>Actions</th>'}</tr></thead><tbody>${items.map(t => `<tr><td><strong>${esc(t.name)}</strong><p class="subtle">${esc(t.description)}</p></td><td>${databases.filter(d => d.team === t.id).length}</td><td>${users.filter(u => u.teams.includes(t.id)).length}</td>${ro ? '' : `<td><div class="row-actions"><button class="text-button" data-edit-team="${t.id}">Edit</button><button class="text-button danger" data-delete-team="${t.id}">Delete</button></div></td>`}</tr>`).join('')}</tbody></table></div>` : `<div class="empty"><span class="empty-icon">${icon('layers')}</span><h3>${teams.length ? 'No results found' : '[ NO TEAMS ]'}</h3><p>${teams.length ? 'Adjust your search.' : 'Create a team first. Then add databases and users.'}</p></div>`;
    for (const b of document.querySelectorAll('[data-edit-team]')) b.onclick = () => teamModal(b.dataset.editTeam);
    for (const b of document.querySelectorAll('[data-delete-team]')) b.onclick = () => confirmAction('Delete team', `Team ${esc(teamName(b.dataset.deleteTeam))} will be deleted. Move or delete its databases first. Users must remain members of at least one other team.`, `/teams/${b.dataset.deleteTeam}`, 'Team deleted.');
  }
  $('#search').oninput = e => { state.query = e.target.value; saveAdminRoute(true); table(); };
  $('#refresh').onclick = load;
  table();
  renderAutomation();
}
// Service accounts of extensions (such as Conversational BI). Team administrators enable them in the extension.
function renderAutomation() {
  const { automationPrincipals: items = [], extensions = [] } = state.data;
  if (!items.length && !extensions.length) return;
  const ro = viewer.access === 'viewer';
  const origin = id => extensions.find(e => e.id === id)?.origin;
  $('#page-content').insertAdjacentHTML('beforeend', `<section class="panel"><div class="panel-heading"><div><h2>Extension service accounts <span class="number-badge">${items.length}</span></h2><p>Extensions such as Conversational BI act for a team in one environment, with the team's write access there. Team administrators enable them in the extension; platform administrators can revoke them here.</p></div></div>${items.length ? `<div class="table-scroll"><table><thead><tr><th>Extension</th><th>Team</th><th>Environment</th><th>Enabled</th>${ro ? '' : '<th>Actions</th>'}</tr></thead><tbody>${items.map(i => `<tr><td><strong>${esc(i.extension)}</strong><p class="subtle">${origin(i.extension) ? esc(origin(i.extension)) : 'Not registered'}</p></td><td>${esc(teamName(i.team) || i.team)}</td><td><span class="tag">${esc(i.environment)}</span></td><td>${date(i.createdAt)}<p class="subtle">${esc(i.createdBy || '—')}</p></td>${ro ? '' : `<td><div class="row-actions"><button class="text-button danger" data-revoke-automation="${esc(i.id)}">Revoke</button></div></td>`}</tr>`).join('')}</tbody></table></div>` : `<div class="empty"><span class="empty-icon">${icon('key')}</span><h3>[ NO SERVICE ACCOUNTS ]</h3><p>Registered extensions: ${extensions.map(e => esc(e.id)).join(', ')}. Team administrators enable them per environment.</p></div>`}</section>`);
  for (const b of document.querySelectorAll('[data-revoke-automation]')) { const item = items.find(i => i.id === b.dataset.revokeAutomation); b.onclick = () => confirmAction('Revoke service account', `${esc(item.extension)} can no longer act for ${esc(teamName(item.team) || item.team)} in ${esc(item.environment)}. It gets no new tokens; tokens already issued expire on their own (after one hour, unless POLARIS_TOKEN_LIFETIME is set).`, `/automation-principals/${item.id}`, 'Service account revoked.', 'Revoke'); }
}
async function renderExplorer() {
  $('#page-content').innerHTML = `<section class="panel explorer"><div class="panel-heading"><div><h2>All databases <span id="explorer-count" class="number-badge">…</span></h2><p>Expand a database or namespace to view its contents.</p></div><button class="icon-button" id="explorer-refresh" aria-label="Refresh catalog">${icon('refresh')}</button></div><div class="filters"><label class="search">${icon('search')}<input id="explorer-search" aria-label="Search databases" placeholder="Search databases…"></label><span class="tag">Portal administrator · read only</span></div><div id="explorer-tree" aria-live="polite" aria-busy="true"><p class="explorer-note">Loading databases…</p></div></section>`;
  const tree = $('#explorer-tree'), search = $('#explorer-search'), count = $('#explorer-count');
  $('#explorer-refresh').onclick = renderExplorer;
  function branch(database, namespace, label, subtitle) {
    const node = document.createElement('details');
    node.className = 'explorer-branch';
    const summary = document.createElement('summary');
    summary.innerHTML = `<span class="explorer-symbol">${icon(namespace.length ? 'layers' : 'database')}</span><span class="explorer-name"><strong>${esc(label)}</strong><small>${esc(subtitle)}</small></span><span class="tag">${namespace.length ? 'Namespace' : 'Database'}</span>`;
    const content = document.createElement('div');
    content.className = 'explorer-children';
    node.append(summary, content);
    let loaded = false, loading = false;
    async function fetchChildren() {
      if (loaded || loading) return;
      loading = true;
      content.setAttribute('aria-busy', 'true');
      content.innerHTML = '<p class="explorer-note" role="status">Loading contents…</p>';
      try {
        const query = new URLSearchParams({ database });
        for (const part of namespace) query.append('namespace', part);
        const result = await api(`/admin/explorer/contents?${query}`);
        if (!content.isConnected) return;
        content.replaceChildren();
        for (const child of result.namespaces) content.append(branch(database, child, child.at(-1), child.join(' / ')));
        const models = result.semanticModels || [];
        for (const [kind, items, symbol] of [['Table', result.tables, 'database'], ['View', result.views, 'book'], ['Semantic model', models, 'model']]) {
          for (const item of items) {
            const row = document.createElement('div');
            row.className = 'explorer-object';
            row.innerHTML = `<span class="explorer-symbol">${icon(symbol)}</span><span class="explorer-name"><strong>${esc(item.name)}</strong><small>${esc(item.namespace.join(' / '))}</small></span><span class="tag">${kind}</span>`;
            content.append(row);
          }
        }
        const total = result.namespaces.length + result.tables.length + result.views.length + models.length;
        const note = document.createElement('p');
        note.className = 'explorer-note';
        note.textContent = total ? `${result.namespaces.length} namespaces · ${result.tables.length} tables · ${result.views.length} views${namespace.length ? ` · ${models.length} semantic models` : ''}` : namespace.length ? 'This namespace is empty.' : 'This database has no namespaces yet.';
        content.append(note);
        loaded = true;
      } catch (error) {
        if (!content.isConnected) return;
        content.innerHTML = `<p class="form-error" role="alert">${esc(error.message)}</p><button class="button explorer-retry">Try again</button>`;
        content.querySelector('button').onclick = fetchChildren;
      } finally {
        loading = false;
        content.setAttribute('aria-busy', 'false');
      }
    }
    node.addEventListener('toggle', () => { if (node.open) fetchChildren(); });
    return node;
  }
  try {
    const result = await api('/admin/explorer/databases');
    if (!tree.isConnected) return;
    count.textContent = result.databases.length;
    tree.replaceChildren();
    const nodes = result.databases.map(database => {
      const team = database.team ? teamName(database.team) : 'No portal team';
      const node = branch(database.id, [], database.name, `${team}${database.environment ? ' · ' + envNames[database.environment] : ''} · ${database.managed ? 'Managed in portal' : 'Created outside portal'}`);
      if (database.status === 'deleting') {
        const summary = node.querySelector('summary');
        summary.setAttribute('aria-disabled', 'true');
        summary.onclick = event => event.preventDefault();
        summary.querySelector('.tag').textContent = 'Deleting';
      }
      tree.append(node);
      return { node, name: database.name };
    });
    const empty = document.createElement('p');
    empty.className = 'explorer-note';
    empty.textContent = result.databases.length ? 'No databases match this search.' : 'There are no databases in the catalog yet.';
    tree.append(empty);
    function filter() {
      let visible = 0;
      for (const item of nodes) {
        item.node.hidden = !item.name.toLowerCase().includes(search.value.trim().toLowerCase());
        if (!item.node.hidden) visible++;
      }
      empty.hidden = visible > 0;
    }
    search.oninput = filter;
    filter();
  } catch (error) {
    if (tree.isConnected) tree.innerHTML = `<p class="form-error" role="alert">${esc(error.message)}</p><button class="button" id="explorer-retry">Try again</button>`;
    if ($('#explorer-retry')) $('#explorer-retry').onclick = renderExplorer;
  } finally {
    tree.setAttribute('aria-busy', 'false');
  }
}
function renderTable() {
  const isDb = state.page === 'databases';
  const { databases, users, teams } = state.data;
  const items = (isDb ? databases : users).filter(item => `${item.name} ${isDb ? teamName(item.team) : item.memberships.map(m => `${teamName(m.team)} ${roleNames[m.role] || m.role}`).join(' ')}`.toLowerCase().includes(state.query.toLowerCase()) && (!isDb || ((!state.team || item.team === state.team) && (!state.environment || item.environment === state.environment))));
  if (!items.length) {
    const empty = !(isDb ? databases : users).length;
    $('#table').innerHTML = `<div class="empty"><span class="empty-icon">${icon(isDb ? 'database' : 'users')}</span><h3>${empty ? isDb ? '[ NO DATABASES ]' : '[ NO USERS ]' : 'No results found'}</h3><p>${!teams.length ? 'Create a team on the Teams page first.' : empty ? isDb ? 'Create a database; its dedicated bucket is provisioned immediately.' : 'Create a user and select one or more teams.' : 'Adjust your search or filters.'}</p>${empty && teams.length && state.data.health.status === 'online' ? `<button class="button" id="empty-create">${icon('plus')}${isDb ? 'Create your first database' : 'Create user'}</button>` : ''}</div>`;
    if ($('#empty-create')) $('#empty-create').onclick = () => isDb ? databaseModal() : userModal(); return;
  }
  const ro = viewer.access === 'viewer';
  $('#table').innerHTML = `<div class="table-scroll"><table><thead><tr>${(isDb ? ['Database', 'Team', 'Environment', 'Users', 'Status', 'Actions'] : ['User', 'Team access', 'Created', ...(ro ? [] : ['Actions'])]).map(t => `<th>${t}</th>`).join('')}</tr></thead><tbody>${items.map(item => isDb ? `<tr><td><button class="database-name" data-connect="${esc(item.id)}" ${item.status === 'deleting' ? 'disabled' : ''}><span class="db-icon">${icon('database')}</span><span><strong>${esc(item.name)}</strong><small>${esc(item.bucket)}</small></span></button></td><td><span class="team-avatar">${esc(teamName(item.team).slice(0, 2).toUpperCase())}</span>${esc(teamName(item.team))}</td><td><span class="tag env-${esc(item.environment)}">${esc(envNames[item.environment] || item.environment)}</span></td><td>${item.status === 'deleting' ? '—' : users.filter(u => u.teams.includes(item.team)).length}</td><td><span class="ready"><span class="dot"></span>${item.status === 'deleting' ? 'Resume deletion' : 'Available'}</span></td><td><div class="row-actions"><button class="icon-button" data-connect="${esc(item.id)}" aria-label="Connect to ${esc(item.name)}" ${item.status === 'deleting' ? 'disabled' : ''}>${icon('arrow')}</button><button class="text-button" data-rename-db="${esc(item.id)}" ${item.status === 'deleting' ? 'disabled' : ''}>Rename</button><button class="text-button" data-move="${esc(item.id)}" ${item.status === 'deleting' ? 'disabled' : ''}>Move</button><button class="text-button danger" data-delete-db="${esc(item.id)}">Delete</button></div></td></tr>` : `<tr><td><div class="database-name"><span class="db-icon">${icon('key')}</span><span><strong>${esc(item.name)}</strong><small>${esc(identityLabel(item))}</small></span></div></td><td><div class="team-tags">${item.memberships.map(m => `<span class="tag">${esc(teamName(m.team))} · ${esc(roleNames[m.role] || m.role)}</span>`).join('')}</div></td><td>${date(item.createdAt)}</td>${ro ? '' : `<td><div class="row-actions">${identityActions(item)}<button class="text-button" data-memberships="${esc(item.id)}" ${['pending', 'revoking'].includes(item.identity?.status) ? 'disabled' : ''}>Edit access</button><button class="text-button danger" data-delete="${esc(item.id)}">Revoke</button></div></td>`}</tr>`).join('')}</tbody></table></div><div class="table-footer">${items.length} ${isDb ? 'databases' : 'users'}<span>${esc(state.data.health.provider)} catalog</span></div>`;
  bindIdentityActions();
  for (const b of document.querySelectorAll('[data-connect]')) b.onclick = () => connectionModal(b.dataset.connect);
  for (const b of document.querySelectorAll('[data-delete]')) b.onclick = () => deleteModal(b.dataset.delete);
  for (const b of document.querySelectorAll('[data-move]')) b.onclick = () => moveModal(b.dataset.move);
  for (const b of document.querySelectorAll('[data-rename-db]')) b.onclick = () => renameDatabaseModal(b.dataset.renameDb);
  for (const b of document.querySelectorAll('[data-memberships]')) b.onclick = () => accessModal(b.dataset.memberships);
  for (const b of document.querySelectorAll('[data-delete-db]')) b.onclick = () => confirmAction('Delete database', `Database ${esc(b.dataset.deleteDb)}, all tables, views, namespaces, files and its dedicated bucket will be permanently deleted. Users and their team memberships will remain.`, `/databases/${encodeURIComponent(b.dataset.deleteDb)}`, 'Database and bucket deleted.');
}
const modal = $('#modal');
function closeModal() { modal.close(); modal.innerHTML = ''; }
modal.addEventListener('cancel', event => { event.preventDefault(); if (!$('#close-modal')?.disabled) closeModal(); });
function openModal(title, subtitle, content) {
  modal.innerHTML = `<div class="modal-header"><div><span class="eyebrow">ICEBERG WORKSPACE</span><h2 id="modal-title">${title}</h2></div><button class="icon-button" id="close-modal" aria-label="Close">${icon('close')}</button></div><p class="modal-subtitle">${subtitle}</p><p class="modal-status inline-status" role="status" aria-live="polite"></p>${content}`;
  $('#close-modal').onclick = closeModal;
  if (!modal.open) modal.showModal();
}
function formSubmit(handler) {
  $('#modal-form').onsubmit = async event => {
    event.preventDefault(); const form = event.target, button = form.querySelector('[type=submit]');
    button.disabled = true; const original = button.textContent; button.textContent = 'Working…';
    $('#close-modal').disabled = true;
    try { const data = new FormData(form), input = Object.fromEntries(data); if (form.querySelector('.team-choices')) { input.memberships = data.getAll('teams').map(team => ({ team, role: data.get(`role:${team}`) })); for (const key of Object.keys(input)) if (key === 'teams' || key.startsWith('role:')) delete input[key]; if (input.memberships.length > 100) throw new Error('Select no more than 100 teams.'); } await handler(input); }
    catch (error) { const el = form.querySelector('.form-error'); if (el) el.textContent = error.message; }
    finally { button.disabled = false; button.textContent = original; if ($('#close-modal')) $('#close-modal').disabled = false; }
  };
}
const nameInput = (label, placeholder) => `<label>${label}<input name="name" required minlength="3" maxlength="48" pattern="[a-z][a-z0-9_\\-]{2,47}" aria-describedby="name-help" title="Use 3–48 lowercase letters, digits, hyphens or underscores, starting with a letter." placeholder="${placeholder}" autocomplete="off"><small id="name-help">Use 3–48 lowercase letters, digits, hyphens or underscores, starting with a letter.</small></label>`;
function teamModal(id) {
  const team = state.data.teams.find(t => t.id === id);
  openModal(team ? 'Edit team' : 'A new team', 'Manage teams centrally in your workspace.', `<form id="modal-form">${nameInput('Team name', 'e.g. data-platform')}<label>Description<textarea name="description" maxlength="280">${esc(team?.description)}</textarea></label><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">${team ? 'Save' : 'Create team'}</button></div></form>`);
  if (team) $('#modal-form [name=name]').value = team.name;
  formSubmit(async input => { await api(id ? `/teams/${id}` : '/teams', id ? 'PATCH' : 'POST', input); await saved('Team saved.'); });
}
function databaseModal() {
  openModal('A new database', 'A dedicated place for your team data.', `<form id="modal-form">${nameInput('Database name', 'e.g. analytics')}<div class="form-row"><label>Team<select name="team" required>${teamOptions()}</select></label><label>Environment<select name="environment">${Object.entries(envNames).map(([v, l]) => `<option value="${v}">${l}</option>`).join('')}</select></label></div><label>Description <span class="optional">optional</span><textarea name="description" maxlength="280" placeholder="What will your team use this database for?"></textarea></label><div class="info-box">${icon('layers')}<span>Your database gets its own bucket. Existing team members automatically receive access with their role in this team.</span></div><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">${icon('plus')}Create database</button></div></form>`);
  formSubmit(async input => { await api('/databases', 'POST', input); await saved('Your database has been created.'); });
}
function renameDatabaseModal(id) {
  const db = state.data.databases.find(d => d.id === id);
  openModal('Rename database', `${esc(db.name)} · ${envNames[db.environment]}. The catalog ID, bucket and connection settings stay the same.`, `<form id="modal-form">${nameInput('Database name', 'e.g. analytics')}<p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">Save name</button></div></form>`);
  $('#modal-form [name=name]').value = db.name;
  formSubmit(async input => { await api(`/databases/${encodeURIComponent(id)}/name`, 'PATCH', {name: input.name}); await saved('Database renamed.'); });
}
function moveModal(id) {
  const db = state.data.databases.find(d => d.id === id);
  openModal('Move database', `${esc(db.name)} · ${envNames[db.environment]} will be owned by another team.`, `<form id="modal-form"><label>Team<select name="team" required>${teamOptions(db.team)}</select></label><div class="info-box">${icon('users')}<span>Members of the new team receive access with the role they hold in that team. Members of the previous team lose access unless they also belong to the new team. The bucket and connection details stay the same.</span></div><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">Move</button></div></form>`);
  formSubmit(async input => { await api(`/databases/${encodeURIComponent(id)}`, 'PATCH', input); state.team = ''; await saved('Database moved and permissions updated.'); });
}
function accessModal(id) {
  const user = state.data.users.find(u => u.id === id);
  openModal('Edit access', `Choose the teams of ${esc(user.name)} and the role in each team.`, `<form id="modal-form">${membershipRows(userRoles(user))}<p class="subtle">${keycloakUsers ? 'Each role applies to the databases of that team only. Database administration does not grant access to this administration portal.' : 'Each role applies to the databases of that team only. Existing database credentials stay the same. Direct S3 access is limited to teams with Database + bucket administration.'}</p><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">Save</button></div></form>`);
  bindMembershipRows();
  formSubmit(async input => {
    if (!input.memberships.length) throw new Error('Select at least one team.');
    const result = await api(`/users/${encodeURIComponent(id)}`, 'PATCH', input);
    await saved('Team access and permissions updated.');
    if (result.bucketCredentials) {
      const credentials = result.bucketCredentials;
      openModal('S3 access is ready', `Save these new S3 credentials for ${esc(user.name)} now. The secret is shown only once.`, `<div class="credential"><span>S3 access key</span><code>${esc(credentials.accessKeyId)}</code></div><div class="credential"><span>S3 secret key</span><code>${esc(credentials.secretAccessKey)}</code></div><p class="subtle">Use these credentials at ${esc(credentials.endpoint)} or in the RustFS console. Existing database credentials stay the same.</p><div class="modal-actions"><button class="button" id="copy-credentials">Copy credentials</button><button class="button primary" id="saved">Saved securely</button></div>`);
      $('#copy-credentials').onclick = () => copy(JSON.stringify(credentials, null, 2));
      $('#saved').onclick = closeModal;
    }
  });
}
function userModal(database) {
  if (keycloakUsers) { identityModal(null, database); return; }
  const selected = state.data.databases.find(d => d.id === database)?.team;
  openModal('A new user', 'Create a service account and assign it to one or more teams.', `<form id="modal-form">${nameInput('Username', 'e.g. pipeline_reader')}${membershipRows(selected ? { [selected]: 'reader' } : {})}<div class="info-box">${icon('key')}<span>Each role applies to all current and future databases of that team. Secrets are shown only once.</span></div><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">Create user ${icon('arrow')}</button></div></form>`);
  bindMembershipRows();
  formSubmit(async input => {
    if (!input.memberships.length) throw new Error('Select at least one team.');
    const result = await api('/users', 'POST', input);
    openModal('Your user is ready', 'Save these credentials now. You cannot view the secret again later.', `<div class="credential"><span>Client-ID</span><code>${esc(result.credentials.clientId)}</code></div><div class="credential"><span>Client-secret</span><code>${esc(result.credentials.clientSecret)}</code></div>${result.bucketCredentials ? `<div class="code-label">S3 · buckets of teams with bucket administration</div><div class="credential"><span>S3 access key</span><code>${esc(result.bucketCredentials.accessKeyId)}</code></div><div class="credential"><span>S3 secret key</span><code>${esc(result.bucketCredentials.secretAccessKey)}</code></div><p class="subtle">Use these S3 credentials at ${esc(result.bucketCredentials.endpoint)} or in the RustFS console.</p>` : ''}<div class="info-box">${icon('check')}<span>${esc(input.name)} belongs to ${input.memberships.map(m => `${esc(teamName(m.team))} (${esc(roleNames[m.role])})`).join(', ')}.</span></div><div class="modal-actions"><button class="button" id="copy-credentials">Copy credentials</button><button class="button primary" id="saved">Saved securely</button></div>`);
    $('#copy-credentials').onclick = () => copy(JSON.stringify(result.bucketCredentials ? { database: result.credentials, s3: result.bucketCredentials } : result.credentials, null, 2));
    $('#saved').onclick = closeModal;
    await load();
  });
}
// A share expires at the end of a UTC day; a local date would show the next day east of Greenwich.
const utcDate = timestamp => new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }).format(new Date(timestamp));
function renderShares() {
  const { shares = [], databases } = state.data;
  const ro = viewer.access === 'viewer';
  const database = id => databases.find(d => d.id === id), owner = s => teamName(database(s.database)?.team) || '—';
  $('#page-content').innerHTML = `<section class="panel"><div class="panel-heading"><div><h2>All data shares <span class="number-badge">${shares.length}</span></h2><p>${ro ? 'Team administrators create and edit shares in Workspaces. Platform administrators can revoke them here.' : 'Team administrators create and edit shares in Workspaces. You can revoke them here.'}</p></div><button class="icon-button" id="refresh" aria-label="Refresh">${icon('refresh')}</button></div><div class="filters"><label class="search">${icon('search')}<input id="search" aria-label="Search" placeholder="Search shares…" value="${esc(state.query)}"></label>${ro ? '' : '<span class="tag">Platform administrator · revoke only</span>'}</div><div id="share-table"></div></section>`;
  function table() {
    const items = shares.filter(s => `${s.name} ${s.recipient} ${s.recipientTeamName || ''} ${s.recipientTeam || ''} ${database(s.database)?.name || ''} ${owner(s)}`.toLowerCase().includes(state.query.toLowerCase()));
    $('#share-table').innerHTML = items.length ? `<div class="table-scroll"><table><thead><tr><th>Share</th><th>Database</th><th>Team</th><th>Shared objects</th><th>Expires</th><th>Created</th>${ro ? '' : '<th>Actions</th>'}</tr></thead><tbody>${items.map(s => `<tr><td><strong>${esc(s.name)}</strong><p class="subtle">${esc([s.recipientTeam ? `Team: ${s.recipientTeamName || s.recipientTeam}` : '', s.external !== false ? `External: ${s.recipient || '—'}` : '', s.listed ? 'Listed' : ''].filter(Boolean).join(' · '))}</p></td><td>${esc(database(s.database)?.name || s.database)}</td><td>${esc(owner(s))}</td><td><div class="team-tags">${s.objects.map(o => `<span class="tag">${esc([...o.namespace, o.name].join('.'))}</span>`).join('')}</div></td><td>${s.expiresAt ? `${utcDate(s.expiresAt)}<p class="subtle">end of day UTC</p>` : 'Never'}</td><td>${date(s.createdAt)}<p class="subtle">${esc(s.createdBy || '—')}</p></td>${ro ? '' : `<td><div class="row-actions"><button class="text-button danger" data-revoke-share="${esc(s.id)}">${s.status === 'revoking' ? 'Resume revocation' : 'Revoke'}</button></div></td>`}</tr>`).join('')}</tbody></table></div>` : `<div class="empty"><span class="empty-icon">${icon('key')}</span><h3>${shares.length ? 'No results found' : '[ NO DATA SHARES ]'}</h3><p>${shares.length ? 'Adjust your search.' : 'Nothing is shared with other teams or external parties.'}</p></div>`;
    for (const b of document.querySelectorAll('[data-revoke-share]')) { const share = shares.find(s => s.id === b.dataset.revokeShare); b.onclick = () => confirmAction('Revoke data share', `Access through ${esc(share.name)}${share.recipient ? ` (${esc(share.recipient)})` : ''} stops working immediately. Storage credentials that were already issued expire on their own shortly after.`, `/shares/${share.id}`, 'Data share revoked.', 'Revoke'); }
  }
  $('#search').oninput = e => { state.query = e.target.value; saveAdminRoute(true); table(); };
  $('#refresh').onclick = load;
  table();
}
async function confirmAction(title, description, path, status, label = 'Delete') {
  openModal(title, description, `<form id="modal-form"><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button" type="button" id="cancel-delete">Cancel</button><button class="button destructive" type="submit">${label}</button></div></form>`);
  $('#cancel-delete').onclick = closeModal;
  formSubmit(async () => { $('#cancel-delete').disabled = true; try { await api(path, 'DELETE'); await saved(status); } finally { if ($('#cancel-delete')) $('#cancel-delete').disabled = false; } });
}
async function copy(text) { try { await navigator.clipboard.writeText(text); notifyStatus('Copied to your clipboard.'); } catch { notifyStatus('Clipboard access is unavailable. Select and copy the text.'); } }
async function connectionModal(id) {
  try {
    const connection = await api(`/databases/${encodeURIComponent(id)}/connection`);
    const config = JSON.stringify({ uri: connection.uri, warehouse: connection.warehouse, 'oauth2-server-uri': connection.oauth2ServerUri, scope: connection.scope, credential: connection.credential, 'header.X-Iceberg-Access-Delegation': 'vended-credentials' }, null, 2);
    const db = state.data.databases.find(d => d.id === id);
    openModal(`${esc(db.name)} · ${envNames[db.environment]}`, `Team ${esc(teamName(db.team))} · Connect your application using an Iceberg REST client.`, `<div class="credential"><span>${connection.bucket ? 'Dedicated bucket' : 'Existing storage location'}</span><code>${esc(connection.bucket || connection.storageLocation)}</code></div><div class="code-label">ICEBERG REST CONFIGURATION</div><pre>${esc(config)}</pre><p class="subtle">Replace the placeholders with database user credentials. These addresses are accessible from this computer.</p><div class="modal-actions"><button class="button" id="add-user">${icon('plus')}Add user</button><button class="button primary" id="copy-config">Copy configuration</button></div>`);
    $('#copy-config').onclick = () => copy(config); $('#add-user').onclick = () => userModal(id);
  } catch (e) { notifyStatus(e.message); }
}
function deleteModal(id) {
  const user = state.data.users.find(u => u.id === id);
  confirmAction('Revoke access', `User ${esc(user.name)} and their database permissions will be removed.${keycloakUsers ? ' Their Keycloak account is preserved. Notebook shutdown is checked every 30 seconds. Previously issued storage credentials expire separately.' : ' Associated S3 access will also be removed.'}`, `/users/${encodeURIComponent(id)}`, 'Access revoked.');
}
async function start() {
  const session = await api('/session');
  loginUrl = session.loginUrl; keycloakUsers = session.userManagement === 'keycloak';
  viewer = { access: session.access || 'admin', user: session.user || '', userPortalUrl: session.userPortalUrl || '', userPortalPages: session.userPortalPages || [] };
  return session.authenticated ? load() : login();
}
start().catch(() => login());

async function refreshOverview() {
  if (!state.data || document.hidden || refreshingOverview || activeMutations) return;
  refreshingOverview = true;
  const previous = state.data, page = state.page;
  try {
    const next = await api('/overview');
    if (state.status === serverDown) { state.status = ''; if ($('#status')) $('#status').textContent = ''; }
    if (!state.data || state.data !== previous || page !== state.page || activeMutations || modal.open || $('.header [aria-expanded="true"]') || document.activeElement?.matches('input, select, textarea')) return;
    if (JSON.stringify(previous) === JSON.stringify(next)) return;
    state.data = next;
    // These pages own their own tree/polling state; update their overview on the next navigation.
    if (['explorer', 'infrastructure', 'guide'].includes(state.page)) return;
    const position = {left: scrollX, top: scrollY};
    render(); scrollTo(position);
  } catch (error) { if (state.data) notifyStatus(error.message); }
  finally { refreshingOverview = false; }
}
setInterval(refreshOverview, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshOverview(); });
window.addEventListener('focus', refreshOverview);
