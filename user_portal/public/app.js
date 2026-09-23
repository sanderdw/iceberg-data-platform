let loginUrl = null;
const $ = (s) => document.querySelector(s);
const envNames = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const roleNames = {reader: 'Read', writer: 'Read & write', admin: 'Administrator', 'bucket-admin': 'Database + bucket administration'};
let state, database = null, namespace = [], activeNotebook = null, browseVersion = 0;
let workspacePage = 'catalog', sharesContext = null;
let restoringRoute = false, refreshingWorkspace = false, pendingActions = 0;
let teamMembersVersion = 0;
let databaseFormContext = null;
function saveRoute(replace = false) {
  if (!state || restoringRoute) return;
  const params = new URLSearchParams({team: state.activeTeam, environment: state.activeEnvironment});
  if (database) { params.set('database', database); namespace.forEach(part => params.append('namespace', part)); }
  if (selectedObject) { params.set('kind', selectedObject.kind); params.set('name', selectedObject.name); }
  if (activeNotebook) { params.set('notebook', activeNotebook.id); params.set('file', activeNotebook.selectedUrl); }
  const hash = `#${workspacePage}?${params}`;
  if (location.hash !== hash) history[replace ? 'replaceState' : 'pushState'](null, '', hash);
}
async function restoreRoute() {
  if (!state) return;
  const [page, query = ''] = location.hash.slice(1).split('?'), params = new URLSearchParams(query);
  restoringRoute = true;
  try {
    if (params.has('team') && state.teams.some(t => t.id === params.get('team')) && state.activeTeam !== params.get('team')) {
      state = await api('/team', 'PATCH', {team: params.get('team')}); resetEditor(); clearBrowser(); renderState();
    }
    if (state.environments.includes(params.get('environment')) && state.activeEnvironment !== params.get('environment')) {
      state = await api('/environment', 'PATCH', {environment: params.get('environment')}); resetEditor(); clearBrowser(); renderState();
    }
    const db = params.get('database');
    if (db && state.databases.some(d => d.id === db)) {
      await browse(db, params.getAll('namespace'));
      if (['table', 'view'].includes(params.get('kind')) && params.get('name')) await inspectObject(db, namespace, params.get('kind'), params.get('name'));
    } else clearBrowser();
    const notebook = state.notebooks.find(n => n.id === params.get('notebook'));
    if (notebook) {
      const urls = [notebook.url, notebook.filesUrl, ...(notebook.examples || []).map(n => n.url)];
      showNotebook(notebook, urls.includes(params.get('file')) ? params.get('file') : notebook.url);
    }
    showPage(Object.hasOwn(workspacePages, page) ? page : 'catalog');
  } finally { restoringRoute = false; saveRoute(true); }
}
window.addEventListener('hashchange', () => restoreRoute().catch(error => notice(error.message, true)));
const workspacePages = {
  team: ['Team overview', 'Your team, its members, and the workspace available in this environment.'],
  databases: ['Databases', 'Manage your team databases and explore data shared by other teams.'],
  catalog: ['Catalog', 'Explore your team databases and read-only data shared by other teams.'],
  notebooks: ['Notebooks', 'Analyze team data and read-only shared data with Python and SQL in marimo.'],
  shares: ['Data shares', 'Share data with other teams and external parties, and review data shared with your team.'],
  guide: ['Getting started', 'Notebook, your own tools or an AI agent. Three ways to work with your team data.'],
};
function showPage(page, updateRoute = true) {
  if (page !== workspacePage) clearNotice();
  workspacePage = page;
  for (const name of Object.keys(workspacePages)) $(`#${name}-page`).hidden = name !== page;
  document.querySelectorAll('header [data-page]').forEach(button => {
    if (button.dataset.page === page) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#page-title').textContent = workspacePages[page][0];
  $('#page-description').textContent = workspacePages[page][1];
  if (page === 'team' && state) { renderTeamOverview(); loadTeamMembers(); }
  if (page === 'databases' && state) renderDatabases();
  if (page === 'shares') renderTeamShares();
  if (page === 'guide' && state) renderGuide();
  placeNotice();
  if (updateRoute) saveRoute();
}
document.querySelectorAll('header [data-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
document.querySelectorAll('[data-workspace-page]').forEach(button => button.addEventListener('click', () => showPage(button.dataset.workspacePage)));
function element(tag, text, className) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (className) e.className = className; return e; }
function placeNotice() { const target = !$('#login-screen').hidden ? $('#login-status') : workspacePage === 'notebooks' && !$('#editor').hidden ? $('#editor-status') : $('#workspace-status'); target.append($('#notice')); }
function clearNotice() { $('#notice').hidden = true; $('#notice').textContent = ''; }
function notice(message, error = false) { placeNotice(); $('#notice').textContent = `[ ${error ? 'ERROR: ' : ''}${message} ]`; $('#notice').className = error ? 'error' : ''; $('#notice').hidden = false; }
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  const light = theme === 'light';
  $('#theme-toggle').textContent = light ? 'Dark / ●' : 'Light / ○';
  $('#theme-toggle').setAttribute('aria-label', `Switch to ${light ? 'dark' : 'light'} mode`);
  document.querySelector('meta[name="theme-color"]').content = light ? '#f5f5f5' : '#000000';
}
try { applyTheme(localStorage.getItem('iceberg-theme') === 'light' ? 'light' : 'dark'); } catch { applyTheme('dark'); }
$('#theme-toggle').addEventListener('click', () => { const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; applyTheme(theme); try { localStorage.setItem('iceberg-theme', theme); } catch {} });
function symbol(kind) {
  const paths = {database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>', namespace: '<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>', table: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 9v12"/>', view: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>'};
  const e = element('span', undefined, 'object-icon');
  e.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true">${paths[kind]}</svg>`;
  return e;
}
function databaseOwner(db) { return db.ownerTeamName || state.teams.find(t => t.id === db.team)?.name || db.team; }
function databaseLabel(db) {
  const label = element('span', undefined, 'database-label');
  label.append(element('strong', db.name));
  if (db.shared) label.append(element('span', 'Shared · Read-only', 'shared-badge'), element('small', `Owner: ${databaseOwner(db)}`));
  return label;
}
function databaseGroups(items, getDatabase, renderItems) {
  return [false, true].flatMap(shared => {
    const groupItems = items.filter(item => Boolean(getDatabase(item)?.shared) === shared);
    if (!groupItems.length) return [];
    const group = element('section', undefined, `database-group${shared ? ' shared-databases' : ''}`);
    const title = shared ? 'Shared with your team' : 'Team databases';
    group.setAttribute('aria-label', title);
    group.append(element('h3', title, 'database-group-title'), ...renderItems(groupItems));
    return [group];
  });
}
function renderDatabaseContext(host, db) {
  host.hidden = !db?.shared;
  host.replaceChildren();
  if (db?.shared) host.append(element('span', 'Shared · Read-only', 'shared-badge'), element('span', `Owned by ${databaseOwner(db)} · Only shared tables and views are available.`));
}
function renderNotebookContext(notebook) {
  const db = state.databases.find(d => d.id === notebook.database);
  $('#editor-title').textContent = `${db?.name || notebook.database} · ${envNames[notebook.environment]}`;
  renderDatabaseContext($('#editor-database-context'), db);
}
function resetEditor() { $('#frame-host').replaceChildren(); $('#editor').hidden = true; $('#browser').hidden = false; activeNotebook = null; $('#notebook-empty').hidden = false; }
function loginScreen() { if (loginUrl) { $('#login').innerHTML = '<a class="button" href="/auth/login">Sign in with Keycloak</a>'; $('#login a').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`; $('#login-screen .hint').textContent = 'Use your linked Keycloak account.'; } $('#workspace-nav').hidden = true; $('#guide-nav').hidden = true; sharesContext = null; databaseFormContext = null; $('#database-form').replaceChildren(); $('#team-shares').replaceChildren(); showPage('catalog', false); state = null; database = null; namespace = []; selectedObject = null; browseVersion++; resetEditor(); $('#workspace-screen').hidden = true; $('#identity').hidden = true; $('#login-screen').hidden = false; clearNotice(); placeNotice(); }
async function api(path, method = 'GET', body) { const response = await fetch(`/api${path}`, {method, headers: method === 'GET' ? {} : {'Content-Type': 'application/json', 'X-Portal-Request': '1'}, body: method === 'GET' ? undefined : JSON.stringify(body ?? {})}); const result = await response.json(); if (!response.ok) { if (response.status === 401) loginScreen(); throw new Error(result.error || 'The action failed.'); } return result; }
async function busy(button, action) { pendingActions++; button.disabled = true; try { await action(); } catch (error) { notice(error.message, true); } finally { pendingActions--; button.disabled = false; } }
function renderState() {
  $('#login-screen').hidden = true; $('#identity').hidden = false; $('#workspace-screen').hidden = false; $('#username').textContent = state.user.name; $('#workspace-nav').hidden = false; $('#guide-nav').hidden = false; placeNotice();
  $('#team').replaceChildren(...state.teams.map(t => { const option = element('option', `${t.name} · ${roleNames[t.role] || t.role}`); option.value = t.id; return option; })); $('#team').value = state.activeTeam;
  $('#environment').replaceChildren(...state.environments.map(env => { const option = element('option', envNames[env]); option.value = env; return option; })); $('#environment').value = state.activeEnvironment;
  $('#databases').replaceChildren(...databaseGroups(state.databases, db => db, items => items.map(d => { const b = element('button', undefined, `db${database === d.id ? ' selected' : ''}`); const label = databaseLabel(d); label.append(element('small', envNames[d.environment])); b.append(symbol('database'), label); b.setAttribute('aria-pressed', String(database === d.id)); b.addEventListener('click', () => browse(d.id, [])); return b; })));
  renderDatabaseContext($('#catalog-database-context'), state.databases.find(d => d.id === database));
  if (database) {
    const selected = state.databases.find(d => d.id === database);
    if (selected) {
      $('#database-label').textContent = selected.name;
      const firstCrumb = $('#breadcrumbs button'); if (firstCrumb) firstCrumb.textContent = selected.name;
    }
  }
  if (!state.databases.length) $('#databases').append(element('p', 'This team has no databases in this environment yet.', 'hint'));
  $('#notebooks').replaceChildren(...databaseGroups(state.notebooks, n => state.databases.find(d => d.id === n.database), items => items.map(n => { const db = state.databases.find(d => d.id === n.database); const b = action('', () => showNotebook(n)), label = databaseLabel(db || {name: n.database}); label.append(element('small', envNames[n.environment])); b.append(label, element('span', '↗')); return b; })));
  if (activeNotebook) renderNotebookContext(activeNotebook);
  if (!state.notebooks.length) $('#notebooks').append(element('p', 'Open a database in marimo to get started.', 'hint'));
  $('#notebook-databases').replaceChildren(...databaseGroups(state.databases, db => db, items => items.map(db => { const b = action('', () => openNotebook(db.id)); b.append(databaseLabel(db), element('span', '↗')); return b; })));
  if (!state.databases.length) $('#notebook-databases').append(element('p', 'This team has no databases in this environment yet.', 'hint'));
  const context = JSON.stringify([state.activeTeam, state.activeEnvironment, state.activeRole, state.databases.map(db => db.id)]);
  if (sharesContext !== context) { sharesContext = context; $('#team-shares').replaceChildren(); if (workspacePage === 'shares') renderTeamShares(); }
  renderDatabases();
  // The guide's examples follow the active team, environment and role.
  if (workspacePage === 'guide') renderGuide();
  if (workspacePage === 'team') {
    renderTeamOverview();
    if ($('#team-members').dataset.context !== teamMembersContext()) loadTeamMembers();
  }
}
function teamMembersContext() { return JSON.stringify([state.user.id, state.activeTeam, state.activeEnvironment]); }
function renderTeamOverview() {
  const team = state.teams.find(t => t.id === state.activeTeam);
  $('#team-name').textContent = team.name;
  $('#team-description').textContent = team.description || '';
  $('#team-description').hidden = !team.description;
  $('#team-summary').replaceChildren(facts([
    ['Your role', roleNames[team.role] || team.role],
    ['Environment', envNames[state.activeEnvironment]],
    ['Team databases', state.databases.filter(db => !db.shared).length],
    ['Shared databases', state.databases.filter(db => db.shared).length],
    ['Your active notebooks', state.notebooks.length],
  ]));
  $('#team-databases-description').textContent = `Available in ${envNames[state.activeEnvironment]}.`;
  const databaseRows = databaseGroups(state.databases, db => db, items => items.map(db => {
    const row = element('div', undefined, 'object-row'), label = element('div', undefined, 'object-label');
    label.append(databaseLabel(db));
    const actions = element('div', undefined, 'database-actions');
    actions.append(action('Browse →', () => browse(db.id, [])));
    row.append(symbol('database'), label, actions);
    return row;
  }));
  $('#team-databases').replaceChildren(...databaseRows);
  if (!databaseRows.length) $('#team-databases').append(element('p', 'This team has no databases in this environment yet.', 'catalog-empty'));
}
function renderDatabases() {
  const team = state.teams.find(t => t.id === state.activeTeam);
  const canManage = ['admin', 'bucket-admin'].includes(team.role);
  const context = JSON.stringify([team.id, state.activeEnvironment, team.role]);
  if (databaseFormContext !== context || !canManage) { databaseFormContext = context; $('#database-form').replaceChildren(); }
  $('#new-database').hidden = !canManage;
  $('#database-permissions').textContent = canManage
    ? `Manage databases for ${team.name} in ${envNames[state.activeEnvironment]}.`
    : 'Only team administrators can create, rename, or delete databases.';
  const databaseRow = db => {
    const label = element('div');
    label.append(databaseLabel(db));
    if (db.description) label.append(element('small', db.description));
    const actions = element('div', undefined, 'database-actions');
    actions.append(action('Browse →', () => browse(db.id, [])));
    if (canManage && !db.shared) actions.append(action('Rename', () => databaseForm('rename', db)), action('Delete', () => databaseForm('delete', db)));
    return [label, databaseOwner(db), envNames[db.environment], actions];
  };
  const rows = state.databases.filter(db => !db.shared).map(databaseRow);
  if (canManage) (state.deletingDatabases || []).forEach(db => {
    const label = element('div');
    label.append(element('strong', db.name), element('small', 'Deletion incomplete'));
    rows.push([label, team.name, envNames[db.environment], action('Resume deletion', () => databaseForm('delete', db))]);
  });
  $('#managed-databases').replaceChildren(rows.length
    ? dataGrid(['Database', 'Owner team', 'Environment', 'Actions'], rows, 'Team databases')
    : element('p', 'This team has no databases in this environment yet.', 'catalog-empty'));
  const shared = state.databases.filter(db => db.shared);
  $('#received-databases-section').hidden = !shared.length;
  $('#received-databases').replaceChildren(...(shared.length ? [dataGrid(['Database', 'Owner team', 'Environment', 'Actions'], shared.map(databaseRow), 'Shared with your team')] : []));
}
$('#refresh-databases').addEventListener('click', event => busy(event.currentTarget, () => loadState()));
function databaseForm(kind, db = null) {
  const host = $('#database-form'), form = element('form', undefined, 'database-form');
  const title = kind === 'create' ? 'Create database' : kind === 'rename' ? `Rename ${db.name}` : `Delete ${db.name}`;
  const name = element('input'); name.maxLength = 48; name.required = true;
  const buttons = element('div', undefined, 'database-actions');
  const submit = element('button', kind === 'create' ? 'Create database' : kind === 'rename' ? 'Save name' : 'Delete database permanently'); submit.type = 'submit';
  const cancel = element('button', 'Cancel', 'quiet'); cancel.type = 'button'; cancel.addEventListener('click', () => host.replaceChildren());
  buttons.append(submit, cancel);
  form.append(element('h3', title));
  if (kind === 'delete') {
    form.append(element('p', 'This permanently deletes every namespace, table, view, stored file, the dedicated bucket, and all data shares. This also applies in Production. Saved team notebook files remain.', 'database-warning'));
    form.append(element('p', `Type ${db.name} to confirm.`, 'hint'));
    form.append(field('Database name', name));
  } else {
    name.value = db?.name || ''; name.pattern = '[a-z][a-z0-9_\\-]{2,47}';
    form.append(field('Database name', name));
    form.append(element('p', 'Use 3–48 lowercase letters, digits, hyphens or underscores, starting with a letter.', 'hint'));
    if (kind === 'create') {
      const description = element('input'); description.maxLength = 280;
      form.append(field('Description (optional)', description));
      form.dataset.description = 'true';
    }
  }
  form.append(buttons);
  form.addEventListener('submit', event => {
    event.preventDefault();
    busy(submit, async () => {
      if (kind === 'delete' && name.value !== db.name) throw new Error('Type the database name exactly to confirm deletion.');
      if (kind === 'create') {
        const description = form.querySelectorAll('input')[1].value.trim();
        await api('/databases', 'POST', {name: name.value, description});
      } else if (kind === 'rename') await api(`/databases/${db.id}`, 'PATCH', {name: name.value});
      else {
        try { await api(`/databases/${db.id}`, 'DELETE', {confirm_name: name.value}); }
        catch (error) { await loadState(); throw error; }
      }
      host.replaceChildren();
      await loadState();
      notice(kind === 'create' ? 'Database created.' : kind === 'rename' ? 'Database renamed.' : 'Database and stored data deleted.');
    });
  });
  host.replaceChildren(form);
  name.focus();
}
$('#new-database').addEventListener('click', () => databaseForm('create'));
async function loadTeamMembers() {
  const host = $('#team-members'), context = teamMembersContext(), version = ++teamMembersVersion;
  if (host.dataset.context !== context) {
    host.replaceChildren(element('p', 'Loading team members…', 'catalog-empty'));
    host.dataset.context = context;
    delete host.dataset.members;
  }
  const current = () => state && context === teamMembersContext() && version === teamMembersVersion && workspacePage === 'team';
  try {
    const result = await api('/team');
    if (!current() || result.team !== state.activeTeam) return;
    const serialized = JSON.stringify(result.members);
    if (host.dataset.members === serialized) return;
    host.replaceChildren(result.members.length ? dataGrid(['Member', 'Team role'], result.members.map(member => [
      `${member.name}${member.id === state.user.id ? ' (you)' : ''}`, roleNames[member.role] || member.role,
    ]), 'Team members') : element('p', 'No team members to display.', 'catalog-empty'));
    host.dataset.members = serialized;
  } catch (error) {
    if (current()) { delete host.dataset.members; host.replaceChildren(element('p', error.message, 'catalog-empty')); }
  }
}
$('#refresh-team').addEventListener('click', event => busy(event.currentTarget, async () => { await loadState(); await loadTeamMembers(); }));
function clearBrowser() { clearNotice(); database = null; namespace = []; selectedObject = null; browseVersion++; $('#database-label').textContent = 'CATALOG'; renderDatabaseContext($('#catalog-database-context'), null); $('#namespace-title').textContent = 'Select a database'; $('#breadcrumbs').replaceChildren(); $('#objects').replaceChildren(element('p', 'Select a database from the catalog.', 'empty')); }
async function loadState(background = false) {
  const previous = state, next = await api('/workspace');
  if (background && (pendingActions || state !== previous)) return;
  if (state && (state.activeTeam !== next.activeTeam || state.activeEnvironment !== next.activeEnvironment || (database && !next.databases.some(d => d.id === database)))) { resetEditor(); clearBrowser(); }
  if (activeNotebook && !next.notebooks.some(n => n.id === activeNotebook.id)) resetEditor();
  if (JSON.stringify(state) !== JSON.stringify(next)) { state = next; renderState(); }
}
async function browse(db, ns, background = false) {
  if (background) {
    const version = browseVersion, host = $('#objects'), current = host.firstChild;
    const query = new URLSearchParams({database: db}); ns.forEach(p => query.append('namespace', p));
    const [result, detail] = await Promise.all([api(`/contents?${query}`), api(`/details?${query}&kind=${ns.length ? 'namespace' : 'database'}`)]);
    if (version !== browseVersion || workspacePage !== 'catalog' || selectedObject || pendingActions || host.firstChild !== current || document.activeElement?.matches('input, select, textarea')) return;
    if (host.dataset.listing === JSON.stringify([detail, result])) return;
    const filter = host.querySelector('input')?.value || '';
    const open = [...host.querySelectorAll('details[open] summary')].map(n => n.textContent);
    renderListing(db, ns, detail, result);
    const input = host.querySelector('input'); input.value = filter; input.dispatchEvent(new Event('input'));
    host.querySelectorAll('details').forEach(n => { n.open = open.includes(n.querySelector('summary').textContent); });
    return;
  }
  showPage('catalog', false); clearNotice(); selectedObject = null; const version = ++browseVersion; database = db; namespace = [...ns]; saveRoute(); $('#browser').hidden = false; renderState();
  $('#database-label').textContent = state.databases.find(d => d.id === db)?.name || db; $('#namespace-title').textContent = ns.length ? ns.at(-1) : 'Namespaces';
  const crumbs = [[], ...ns.map((_, i) => ns.slice(0, i + 1))];
  $('#breadcrumbs').replaceChildren(...crumbs.flatMap((parts, i) => { const b = element('button', parts.at(-1) || state.databases.find(d => d.id === db)?.name || db); b.addEventListener('click', () => browse(db, parts)); return i ? [element('span', '/'), b] : [b]; }));
  $('#objects').replaceChildren(element('p', '[ LOADING... ]', 'empty'));
  try {
    const query = new URLSearchParams({database: db}); ns.forEach(p => query.append('namespace', p));
    const [result, detail] = await Promise.all([api(`/contents?${query}`), api(`/details?${query}&kind=${ns.length ? 'namespace' : 'database'}`)]); if (version !== browseVersion) return;
    renderListing(db, ns, detail, result);
  } catch (error) { if (version === browseVersion) { $('#objects').replaceChildren(element('p', error.message, 'empty')); notice(error.message, true); } }
}
function renderListing(db, ns, detail, result) {
    const rows = [];
    function row(name, kind, iconName, actionLabel, action) { const r = element('div', undefined, 'object-row'); const label = element('div', undefined, 'object-label'); label.append(element('strong', name), element('small', kind)); const button = element('button', actionLabel, 'quiet'); button.addEventListener('click', () => busy(button, action)); r.append(symbol(iconName), label, button); rows.push(r); }
    result.namespaces.forEach(parts => row(parts.at(-1), 'Namespace', 'namespace', 'Open →', () => browse(db, parts)));
    result.tables.forEach(t => row(t.name, 'Iceberg table', 'table', 'Details →', () => inspectObject(db, ns, 'table', t.name)));
    result.views.forEach(v => row(v.name, 'Iceberg view', 'view', 'Details →', () => inspectObject(db, ns, 'view', v.name)));
    renderCatalogListing(detail, result, rows);
    $('#objects').dataset.listing = JSON.stringify([detail, result]);
}
function showNotebook(notebook, targetUrl = notebook.url) { if (activeNotebook?.id !== notebook.id || activeNotebook?.selectedUrl !== targetUrl) { const frame = element('iframe'); frame.title = `marimo · ${notebook.database}`; frame.src = targetUrl; frame.allow = 'clipboard-read; clipboard-write'; $('#frame-host').replaceChildren(frame); } activeNotebook = {...notebook, selectedUrl: targetUrl}; $('#notebook-file').replaceChildren(...[{title: 'Shared files', url: notebook.filesUrl}, {title: 'Starter notebook', url: notebook.url}, ...(notebook.examples || [])].map(n => { const option = element('option', n.title); option.value = n.url; return option; })); $('#notebook-file').value = targetUrl; renderNotebookContext(notebook); $('#editor-external').href = targetUrl; $('#browser').hidden = false; $('#editor').hidden = false; $('#notebook-empty').hidden = true; showPage('notebooks'); }
async function openNotebook(db) { const existing = state.notebooks.find(n => n.database === db); if (existing) { showNotebook(existing); notice('Your existing notebook for this database has resumed. Select the table you want to work with there.'); return; } notice('Opening your team’s shared workspace…'); const notebook = await api('/notebooks', 'POST', {database: db, namespace: [], table: null}); await loadState(); showNotebook(notebook); notice('Marimo is ready. Saved files are shared with your team in this environment.'); }
$('#login').addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy(form.querySelector('button'), async () => { const data = Object.fromEntries(new FormData(form)); await api('/session', 'POST', data); form.reset(); clearBrowser(); await loadState(); await restoreRoute(); }); });
$('#logout').addEventListener('click', e => busy(e.currentTarget, async () => { const result = await api('/session', 'DELETE'); if (result.logoutUrl) { location.assign(result.logoutUrl); return; } loginScreen(); }));
$('#team').addEventListener('change', e => busy(e.currentTarget, async () => { try { const next = await api('/team', 'PATCH', {team: e.target.value}); resetEditor(); clearBrowser(); state = next; renderState(); saveRoute(); } catch (error) { $('#team').value = state.activeTeam; throw error; } }));
$('#environment').addEventListener('change', e => busy(e.currentTarget, async () => { try { const next = await api('/environment', 'PATCH', {environment: e.target.value}); resetEditor(); clearBrowser(); state = next; renderState(); saveRoute(); } catch (error) { $('#environment').value = state.activeEnvironment; throw error; } }));
$('#refresh').addEventListener('click', e => busy(e.currentTarget, async () => { await loadState(); if (selectedObject) await inspectObject(database, namespace, selectedObject.kind, selectedObject.name); else if (database) await browse(database, namespace); }));
$('#back').addEventListener('click', () => showPage('catalog'));
$('#close-notebook').addEventListener('click', e => busy(e.currentTarget, async () => { await api(`/notebooks/${activeNotebook.id}`, 'DELETE'); resetEditor(); await loadState(); notice('Notebook stopped. Your saved work is preserved.'); }));
(async () => { try { const session = await api('/session'); loginUrl = session.loginUrl; if (session.authenticated) { await loadState(); await restoreRoute(); } else loginScreen(); } catch (error) { loginScreen(); notice(error.message, true); } })();
async function refreshWorkspace() {
  if (!state || document.hidden || refreshingWorkspace || restoringRoute || pendingActions) return;
  refreshingWorkspace = true;
  try {
    await loadState(true);
    if (!state || pendingActions || restoringRoute || document.activeElement?.matches('input, select, textarea')) return;
    if (workspacePage === 'team') await loadTeamMembers();
    else if (workspacePage === 'shares') await refreshTeamShares();
    else if (workspacePage === 'catalog' && database && !selectedObject) await browse(database, namespace, true);
  } catch (error) { notice(error.message, true); }
  finally { refreshingWorkspace = false; }
}
setInterval(refreshWorkspace, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshWorkspace(); });
window.addEventListener('focus', refreshWorkspace);

$('#notebook-file').addEventListener('change', e => showNotebook(activeNotebook, e.target.value));
