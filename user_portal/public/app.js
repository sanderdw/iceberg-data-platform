let loginUrl = null;
const $ = (s) => document.querySelector(s);
const envNames = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const roleNames = {reader: 'Read', writer: 'Read & write', admin: 'Administrator', 'bucket-admin': 'Database + bucket administration'};
let state, database = null, namespace = [], activeNotebook = null, browseVersion = 0;
let workspacePage = 'catalog', sharesContext = null;
let restoringRoute = false, refreshingWorkspace = false, pendingActions = 0;
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
  catalog: ['Catalog', 'Explore your team catalogs and inspect Iceberg tables and views.'],
  notebooks: ['Notebooks', 'Analyze your team data with shared Python and SQL notebooks in marimo.'],
  shares: ['Data shares', 'Review data shared with external parties across your team’s databases in this environment.'],
};
function showPage(page, updateRoute = true) {
  workspacePage = page;
  for (const name of Object.keys(workspacePages)) $(`#${name}-page`).hidden = name !== page;
  document.querySelectorAll('#workspace-nav button').forEach(button => {
    if (button.dataset.page === page) button.setAttribute('aria-current', 'page');
    else button.removeAttribute('aria-current');
  });
  $('#page-title').textContent = workspacePages[page][0];
  $('#page-description').textContent = workspacePages[page][1];
  if (page === 'shares') renderTeamShares();
  placeNotice();
  if (updateRoute) saveRoute();
}
document.querySelectorAll('#workspace-nav button').forEach(button => button.addEventListener('click', () => showPage(button.dataset.page)));
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
function resetEditor() { $('#frame-host').replaceChildren(); $('#editor').hidden = true; $('#browser').hidden = false; activeNotebook = null; $('#notebook-empty').hidden = false; }
function loginScreen() { if (loginUrl) { $('#login').innerHTML = '<a class="button" href="/auth/login">Sign in with Keycloak</a>'; $('#login a').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`; $('#login-screen .hint').textContent = 'Use your linked Keycloak account.'; } $('#workspace-nav').hidden = true; sharesContext = null; $('#team-shares').replaceChildren(); showPage('catalog', false); state = null; database = null; namespace = []; selectedObject = null; browseVersion++; resetEditor(); $('#workspace-screen').hidden = true; $('#identity').hidden = true; $('#login-screen').hidden = false; clearNotice(); placeNotice(); }
async function api(path, method = 'GET', body) { const response = await fetch(`/api${path}`, {method, headers: method === 'GET' ? {} : {'Content-Type': 'application/json', 'X-Portal-Request': '1'}, body: method === 'GET' ? undefined : JSON.stringify(body ?? {})}); const result = await response.json(); if (!response.ok) { if (response.status === 401) loginScreen(); throw new Error(result.error || 'The action failed.'); } return result; }
async function busy(button, action) { pendingActions++; button.disabled = true; try { await action(); } catch (error) { notice(error.message, true); } finally { pendingActions--; button.disabled = false; } }
function renderState() {
  $('#login-screen').hidden = true; $('#identity').hidden = false; $('#workspace-screen').hidden = false; $('#username').textContent = state.user.name; $('#workspace-nav').hidden = false; placeNotice();
  $('#team').replaceChildren(...state.teams.map(t => { const option = element('option', `${t.name} · ${roleNames[t.role] || t.role}`); option.value = t.id; return option; })); $('#team').value = state.activeTeam;
  $('#environment').replaceChildren(...state.environments.map(env => { const option = element('option', envNames[env]); option.value = env; return option; })); $('#environment').value = state.activeEnvironment;
  $('#databases').replaceChildren(...state.databases.map(d => { const b = element('button', undefined, `db${database === d.id ? ' selected' : ''}`); const label = element('span', d.name); label.append(element('small', envNames[d.environment])); b.append(symbol('database'), label); b.setAttribute('aria-pressed', String(database === d.id)); b.addEventListener('click', () => browse(d.id, [])); return b; }));
  if (!state.databases.length) $('#databases').append(element('p', 'This team has no databases in this environment yet.', 'hint'));
  $('#notebooks').replaceChildren(...state.notebooks.map(n => { const b = element('button', `${state.databases.find(d => d.id === n.database)?.name || n.database} · ${envNames[n.environment]} ↗`, 'quiet'); b.addEventListener('click', () => showNotebook(n)); return b; }));
  if (!state.notebooks.length) $('#notebooks').append(element('p', 'Open a database in marimo to get started.', 'hint'));
  $('#notebook-databases').replaceChildren(...state.databases.map(db => action(`${db.name} ↗`, () => openNotebook(db.id, []))));
  if (!state.databases.length) $('#notebook-databases').append(element('p', 'This team has no databases in this environment yet.', 'hint'));
  const context = JSON.stringify([state.activeTeam, state.activeEnvironment, state.activeRole, state.databases.map(db => db.id)]);
  if (sharesContext !== context) { sharesContext = context; $('#team-shares').replaceChildren(); if (workspacePage === 'shares') renderTeamShares(); }
}
function clearBrowser() { clearNotice(); database = null; namespace = []; selectedObject = null; browseVersion++; $('#database-label').textContent = 'CATALOG'; $('#namespace-title').textContent = 'Select a database'; $('#breadcrumbs').replaceChildren(); $('#open-notebook').hidden = true; $('#examples').hidden = true; $('#objects').replaceChildren(element('p', 'Select a database from the catalog.', 'empty')); }
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
  $('#database-label').textContent = state.databases.find(d => d.id === db)?.name || db; $('#namespace-title').textContent = ns.length ? ns.at(-1) : 'Namespaces'; $('#open-notebook').hidden = false; $('#examples').hidden = false;
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
function showNotebook(notebook, targetUrl = notebook.url) { if (activeNotebook?.id !== notebook.id || activeNotebook?.selectedUrl !== targetUrl) { const frame = element('iframe'); frame.title = `marimo · ${notebook.database}`; frame.src = targetUrl; frame.allow = 'clipboard-read; clipboard-write'; $('#frame-host').replaceChildren(frame); } activeNotebook = {...notebook, selectedUrl: targetUrl}; $('#notebook-file').replaceChildren(...[{title: 'Shared files', url: notebook.filesUrl}, {title: 'Starter notebook', url: notebook.url}, ...(notebook.examples || [])].map(n => { const option = element('option', n.title); option.value = n.url; return option; })); $('#notebook-file').value = targetUrl; $('#editor-title').textContent = `${state.databases.find(d => d.id === notebook.database)?.name || notebook.database} · ${envNames[notebook.environment]}`; $('#editor-external').href = targetUrl; $('#browser').hidden = false; $('#editor').hidden = false; $('#notebook-empty').hidden = true; showPage('notebooks'); }
async function openNotebook(db, ns, table, exampleIndex) { const existing = state.notebooks.find(n => n.database === db); if (existing) { showNotebook(existing, exampleIndex === undefined ? existing.url : existing.examples?.[exampleIndex]?.url); if (exampleIndex === undefined) notice('Your existing notebook for this database has resumed. Select the table you want to work with there.'); return; } notice('Opening your team’s shared workspace…'); const notebook = await api('/notebooks', 'POST', {database: db, namespace: ns, table: table || null}); await loadState(); showNotebook(notebook, exampleIndex === undefined ? notebook.url : notebook.examples?.[exampleIndex]?.url); notice('Marimo is ready. Saved files are shared with your team in this environment.'); }
$('#login').addEventListener('submit', e => { e.preventDefault(); const form = e.currentTarget; busy(form.querySelector('button'), async () => { const data = Object.fromEntries(new FormData(form)); await api('/session', 'POST', data); form.reset(); clearBrowser(); await loadState(); await restoreRoute(); }); });
$('#logout').addEventListener('click', e => busy(e.currentTarget, async () => { const result = await api('/session', 'DELETE'); if (result.logoutUrl) { location.assign(result.logoutUrl); return; } loginScreen(); }));
$('#team').addEventListener('change', e => busy(e.currentTarget, async () => { try { const next = await api('/team', 'PATCH', {team: e.target.value}); resetEditor(); clearBrowser(); state = next; renderState(); saveRoute(); } catch (error) { $('#team').value = state.activeTeam; throw error; } }));
$('#environment').addEventListener('change', e => busy(e.currentTarget, async () => { try { const next = await api('/environment', 'PATCH', {environment: e.target.value}); resetEditor(); clearBrowser(); state = next; renderState(); saveRoute(); } catch (error) { $('#environment').value = state.activeEnvironment; throw error; } }));
$('#refresh').addEventListener('click', e => busy(e.currentTarget, async () => { await loadState(); if (selectedObject) await inspectObject(database, namespace, selectedObject.kind, selectedObject.name); else if (database) await browse(database, namespace); }));
$('#open-notebook').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, selectedObject?.kind === 'table' ? selectedObject.name : null)));
$('#back').addEventListener('click', () => showPage('catalog'));
$('#close-notebook').addEventListener('click', e => busy(e.currentTarget, async () => { await api(`/notebooks/${activeNotebook.id}`, 'DELETE'); resetEditor(); await loadState(); notice('Notebook stopped. Your saved work is preserved.'); }));
(async () => { try { const session = await api('/session'); loginUrl = session.loginUrl; if (session.authenticated) { await loadState(); await restoreRoute(); } else loginScreen(); } catch (error) { loginScreen(); notice(error.message, true); } })();
async function refreshWorkspace() {
  if (!state || document.hidden || refreshingWorkspace || restoringRoute || pendingActions) return;
  refreshingWorkspace = true;
  try {
    await loadState(true);
    if (!state || pendingActions || restoringRoute || document.activeElement?.matches('input, select, textarea')) return;
    if (workspacePage === 'shares') await refreshTeamShares();
    else if (workspacePage === 'catalog' && database && !selectedObject) await browse(database, namespace, true);
  } catch (error) { notice(error.message, true); }
  finally { refreshingWorkspace = false; }
}
setInterval(refreshWorkspace, 30000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshWorkspace(); });
window.addEventListener('focus', refreshWorkspace);

$('#example-write').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, null, 0)));
$('#example-analyse').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, null, 1)));
$('#example-native').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, null, 2)));
$('#example-v3-write').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, null, 3)));
$('#example-v3-read').addEventListener('click', e => busy(e.currentTarget, () => openNotebook(database, namespace, null, 4)));
$('#notebook-file').addEventListener('change', e => showNotebook(activeNotebook, e.target.value));
