'use strict';
// The pipeline viewer over the REST API. Agents change pipelines through MCP; people see what
// exists, how it ran and where each table comes from, and writers can edit a node's file on a branch.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
let me = null, project = null, pipeline = null, graph = null, route = {}, overlay = null, detailToken = 0;
// Live updates while a run is queued or running; cleared on every navigation.
let poller = null;
const ACTIVE = new Set(['queued', 'running']);
const VIEWS = ['pipeline', 'files', 'runs', 'schedules', 'settings'];
// Graph filters: status groups and node types to show, and a dbt-style focus such as +orders+.
const STATUS_GROUPS = {built: ['success', 'pass'], failed: ['fail', 'error', 'failed'], warning: ['warn', 'skipped'], 'not run': ['never_run']};
const filters = {hiddenStatus: new Set(), hiddenTypes: new Set(), focus: ''};
let includePreviews = false;
const statusGroup = status => Object.keys(STATUS_GROUPS).find(key => STATUS_GROUPS[key].includes(status)) || 'not run';

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}

// replaceChildren would print "null" for skipped optional parts.
function fill(parent, ...children) { parent.replaceChildren(...children.flat().filter(c => c !== null && c !== undefined && c !== false)); }

async function api(path, options = {}) {
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin',
    headers: {...(options.body ? {'Content-Type': 'application/json'} : {}), ...(options.method && options.method !== 'GET' ? {'X-Iceberg-Dbt': '1'} : {})},
  });
  const body = response.headers.get('content-type')?.includes('json') ? await response.json() : {};
  if (response.status === 401) { showSignin(); throw new Error(body.error || 'Sign in to continue.'); }
  if (!response.ok) throw Object.assign(new Error(body.error || `Request failed (${response.status}).`), {code: body.code, detail: body.detail});
  return body;
}

function when(seconds) {
  if (!seconds) return '—';
  const date = new Date(seconds * 1000), diff = (Date.now() - date) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return date.toLocaleString();
}
function duration(run) { return run.started_at && run.finished_at ? `${Math.max(1, Math.round(run.finished_at - run.started_at))} s` : ''; }
function chip(status) { return h('span', {class: `chip ${status}`}, h('i', {class: `status ${status}`}), status.replace('_', ' ')); }
function notice(message, error = false) { const n = $('#notice'); n.hidden = !message; n.textContent = message || ''; n.classList.toggle('error', error); }

function show(section) {
  for (const id of ['loading', 'signin', 'projects', 'project']) $(`#${id}`).hidden = id !== section;
}
function showSignin() {
  show('signin'); $('#identity').hidden = true;
  $('#signin-link').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`;
}

function parseRoute() {
  const [path, query = ''] = location.hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean), params = new URLSearchParams(query);
  return {project: parts[0] === 'projects' ? parts[1] : null, view: VIEWS.includes(parts[2]) ? parts[2] : 'pipeline',
          environment: ENVIRONMENTS.includes(params.get('environment')) ? params.get('environment') : 'development',
          node: params.get('node'), run: params.get('run'), ref: params.get('ref') || 'main', file: params.get('file')};
}
function go(next, replace = false) {
  const r = {...route, ...next}, params = new URLSearchParams({environment: r.environment});
  if (r.ref && r.ref !== 'main') params.set('ref', r.ref);
  if (r.node) params.set('node', r.node);
  if (r.run) params.set('run', r.run);
  if (r.file && r.view === 'files') params.set('file', r.file);
  const hash = r.project ? `#/projects/${r.project}/${r.view}?${params}` : '#/';
  if (location.hash === hash) render(); else if (replace) { history.replaceState(null, '', hash); render(); } else location.hash = hash;
}

function crumbs() {
  const nav = $('#crumbs'); nav.replaceChildren(h('a', {href: '#/', text: 'Projects'}));
  if (project) nav.append(' / ', h('span', {text: `${teamName(project.team)} · ${project.name}`}));
}
// The pipeline shows one branch: its latest structure, node statuses and runs. Acceptance and
// production only build main.
function pipelineKey() { return `${project.id}:${route.environment}:${route.ref}:${route.run || ''}`; }
function teamName(id) { return (me?.memberships.find(m => m.team === id) || {}).teamName || id; }

async function render() {
  clearTimeout(poller);
  route = parseRoute();
  try {
    if (!me) {
      const session = await (await fetch('/api/session', {credentials: 'same-origin'})).json();
      if (!session.authenticated) return showSignin();
      me = await api('/me');
      $('#identity').hidden = false; $('#username').textContent = me.user.name;
    }
    if (!route.project) { project = null; crumbs(); return renderProjects(); }
    if (!project || project.id !== route.project) { project = await api(`/projects/${route.project}`); pipeline = null; }
    crumbs(); show('project'); renderProjectBar();
    const key = pipelineKey();
    if (route.view === 'pipeline' && pipeline && pipeline.key === key) {
      // Only the selection changed: keep the drawn graph and its viewport.
      graph.reserve = route.node ? panelWidth() : 0;
      graph.select(route.node, {silent: true});
      return renderPanel();
    }
    pipeline = null;
    if (route.view === 'runs') return renderRuns();
    if (route.view === 'schedules') return renderSchedules();
    if (route.view === 'files') return renderFiles();
    if (route.view === 'settings') return renderSettings();
    return renderPipeline();
  } catch (error) {
    if (!$('#signin').hidden) return;
    // Before the session loads there is no page to hold the notice: say it on the loading screen.
    if (!me) { show('loading'); $('#loading p').textContent = error.message; return; }
    show(route.project ? 'project' : 'projects'); notice(error.message, true);
  }
}

async function renderProjects() {
  show('projects');
  const projects = await api('/projects'), list = $('#project-list');
  list.replaceChildren(...projects.map(p => h('a', {class: 'card project-card', href: `#/projects/${p.id}/pipeline`},
    h('p', {class: 'eyebrow', text: teamName(p.team)}), h('h2', {text: p.name}),
    h('p', {text: p.description || `Models land in ${p.default_database} by default.`}),
    h('div', {class: 'chips'}, h('span', {class: 'chip', text: p.role}), h('span', {class: 'chip', text: `created by ${p.created_by}`})),
    h('span', {class: 'open', text: 'Open pipeline →'}))));
  if (!projects.length) list.append(h('div', {class: 'card'}, h('h2', {text: 'No pipelines yet'}),
    h('p', {text: 'Ask your AI agent to create a dbt project for your team, or call POST /api/v1/projects. A new project starts empty; the agent adds the models.'})));
  const writer = me.memberships.some(m => WRITERS.includes(m.role));
  $('#new-project').hidden = !writer;
  $('#new-project').onclick = openNewProject;
  $('#cli-commands').textContent = ['uv run iceberg_dbt.py login', 'uv run iceberg_dbt.py clone <project>',
    'cd <project> && uv run iceberg_dbt.py build', 'uv run iceberg_dbt.py push --branch <name> && uv run iceberg_dbt.py build --ref <name>'].join('\n');
  $('#mcp-command').textContent = `claude mcp add --transport http --client-id ext-dbt-mcp --callback-port 3010 iceberg-dbt ${location.origin}/mcp`;
}

// A writer creates an empty project in one of their teams; the agent or the editor adds models.
function openNewProject() {
  const form = $('#new-project-form');
  const teams = me.memberships.filter(m => WRITERS.includes(m.role));
  const team = h('select', {}, ...teams.map(t => h('option', {value: t.team, text: t.teamName})));
  const database = h('select');
  const databases = () => {
    const names = [...new Set(me.databases.filter(d => d.team === team.value).map(d => d.name))].sort();
    database.replaceChildren(...names.map(name => h('option', {value: name, text: name})));
  };
  team.onchange = databases; databases();
  const name = h('input', {type: 'text', required: true, pattern: '[a-z][a-z0-9_]{2,47}', placeholder: 'sales_pipeline',
    title: '3–48 lowercase letters, digits or _, starting with a letter', spellcheck: 'false', autocomplete: 'off'});
  const description = h('input', {type: 'text', maxlength: '280', autocomplete: 'off'});
  const status = h('p', {class: 'small'}), create = h('button', {type: 'submit', text: 'Create project'});
  form.onsubmit = async event => {
    event.preventDefault();
    create.disabled = true; status.className = 'small'; status.textContent = 'Creating…';
    try {
      const created = await api('/projects', {method: 'POST', body: JSON.stringify(
        {team: team.value, name: name.value.trim(), description: description.value.trim(), default_database: database.value})});
      location.hash = `#/projects/${created.id}/pipeline`;
    } catch (error) { status.className = 'small error'; status.textContent = error.message; create.disabled = false; }
  };
  fill(form,
    h('h2', {text: 'New project'}),
    h('p', {class: 'small', text: 'A new project starts empty: dbt_project.yml, a README and the namespace macro. Your agent adds the sources, models and tests.'}),
    teams.length > 1 ? h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Team'}), team) : null,
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Name'}), name),
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Description'}), description),
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Default database'}), database),
    h('div', {class: 'tabs'}, create, h('button', {class: 'quiet', type: 'button', onclick: () => { form.hidden = true; }, text: 'Cancel'})),
    status);
  form.hidden = false;
  name.focus();
}

// Deleting forgets the project as if it never existed; the tables it built stay.
function openDelete() {
  const dialog = $('#delete-dialog');
  const confirmName = h('input', {type: 'text', autocomplete: 'off', spellcheck: 'false'});
  const status = h('div', {class: 'small'}), remove = h('button', {class: 'danger', type: 'button', text: 'Delete project', disabled: true});
  let force = false;
  confirmName.oninput = () => { remove.disabled = confirmName.value !== project.name; };
  remove.onclick = async () => {
    remove.disabled = true; status.className = 'small'; status.textContent = 'Removing the dbt properties from its tables…';
    try {
      await api(`/projects/${project.id}`, {method: 'DELETE', body: JSON.stringify({confirm_name: confirmName.value, force})});
      dialog.close(); project = null; location.hash = '#/';
    } catch (error) {
      status.className = 'small error'; status.textContent = error.message;
      if (error.code === 'cleanup_failed') {
        force = true; remove.textContent = 'Delete anyway';
        status.append(h('ul', {}, ...error.detail.failed.map(f => h('li', {text: `${f.environment} · ${f.table} · ${f.error}`}))));
      }
      remove.disabled = false;
    }
  };
  fill(dialog,
    h('h2', {id: 'delete-title', text: `Delete ${project.name}?`}),
    h('p', {text: 'This forgets the project as if it never existed: its Git history and branches, runs, schedules, and the dbt producer, lineage and quality properties on the tables it built.'}),
    h('p', {text: 'The tables, their data and docs stay, and so do views and semantic models.'}),
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: `Type ${project.name} to confirm`}), confirmName),
    status,
    h('div', {class: 'tabs'}, remove, h('button', {class: 'quiet', type: 'button', onclick: () => dialog.close(), text: 'Cancel'})));
  dialog.showModal();
  confirmName.focus();
}

function renderProjectBar() {
  $('#project-team').textContent = `${teamName(project.team)} · ${project.role}`;
  $('#project-name').textContent = project.name;
  const enabled = new Map(project.environments.map(e => [e.environment, e.enabled]));
  $('#environments').replaceChildren(...ENVIRONMENTS.map(env => h('button', {
    type: 'button', role: 'tab', 'aria-selected': String(env === route.environment),
    title: enabled.get(env) ? '' : 'dbt is not enabled for this environment', onclick: () => go({environment: env, node: null, run: null, ref: null}),
  }, LABELS[env], enabled.get(env) ? '' : h('span', {class: 'off', text: ' ·off'}))));
  for (const button of document.querySelectorAll('#views button')) {
    button.setAttribute('aria-selected', String(button.dataset.view === route.view));
    button.onclick = () => go({view: button.dataset.view, node: null, run: null, file: null});
  }
  for (const view of VIEWS) $(`#${view}-view`).hidden = route.view !== view;
  const where = ADMINS.includes(project.role) ? 'Enable it under Settings.' : 'A team administrator enables it under Settings.';
  notice(enabled.get(route.environment) || ['files', 'settings'].includes(route.view) ? '' : `dbt is not enabled for ${LABELS[route.environment]} in this team. ${where}`);
}

async function renderPipeline() {
  const query = new URLSearchParams({environment: route.environment, ref: route.ref});
  [pipeline] = await Promise.all([api(`/projects/${project.id}/pipeline?${query}`), renderBranches()]);
  pipeline.key = pipelineKey();
  graph ||= new window.PipelineGraph($('#graph'), {onSelect: id => {
    graph.reserve = id ? panelWidth() : 0;
    if (id) graph.center(id);
    go({node: id}, true);
  }});
  const empty = !pipeline.nodes.length;
  $('#graph-empty').hidden = !empty;
  const others = pipeline.refsWithPipeline || [];
  if (empty) $('#graph-empty').replaceChildren(h('div', {}, h('h2', {text: 'No pipeline drawn yet'}), h('p', {text: pipeline.hint}),
    others.length ? h('div', {class: 'tabs'}, ...others.map(ref => h('button', {type: 'button', class: 'quiet', onclick: () => go({ref, node: null, run: null}), text: `Show ${ref}`}))) : null));
  // Development is shared: say when another branch built its tables after this one.
  const over = pipeline.overwrittenBy;
  if (over && $('#notice').hidden) notice(`Development's tables were last built from ${over.ref} (${when(over.at)}), so they may not match ${route.ref}.`);
  await loadOverlay();
  graph.reserve = route.node ? panelWidth() : 0;
  drawGraph();
  const view = `${project.id}:${route.environment}:${route.ref}`;
  if (graph.fitted !== view) { graph.fit(); graph.fitted = view; if (route.node) graph.center(route.node); }
  $('#find').value = filters.focus || $('#find').value;
  $('#find').oninput = event => {
    const value = event.target.value.trim();
    if (!value.includes('+') && filters.focus) { filters.focus = ''; drawGraph(); graph.fit(); }
    graph.highlight(value.includes('+') ? '' : value);
  };
  $('#find').onkeydown = event => {
    if (event.key === 'Escape') { event.target.value = ''; filters.focus = ''; graph.highlight(''); drawGraph(); graph.fit(); return; }
    if (event.key !== 'Enter') return;
    const value = event.target.value.trim();
    if (value.includes('+')) return focusOn(value);
    const query = value.toLowerCase(), hit = pipeline.nodes.find(n => n.name.toLowerCase().includes(query));
    if (hit) { graph.reserve = panelWidth(); go({node: hit.id}, true); graph.center(hit.id); }
  };
  $('#fit').onclick = () => graph.fit();
  renderLegend();
  renderTimeline();
  watchPipeline();
  const docs = pipeline.runs.find(r => r.command === 'docs' && r.status === 'success');
  $('#docs-link').hidden = !docs;
  if (docs) $('#docs-link').href = `/docs-site/${docs.id}/index.html`;
  const env = project.environments.find(e => e.environment === route.environment);
  const button = $('#run-now');
  button.hidden = !(env?.enabled && WRITERS.includes(project.role));
  button.onclick = () => buildNow(button);
  await renderPanel();
}

function panelWidth() { return Math.min(460, $('#graph').getBoundingClientRect().width * 0.92); }

async function loadOverlay() {
  overlay = null;
  if (!route.run) return;
  try {
    const run = await api(`/runs/${route.run}`);
    overlay = new Map(run.results.map(r => [r.node, r.status === 'pass' ? 'success' : r.status]));
  } catch { overlay = null; }
}

// dbt's graph operators on one node: +name (and upstream), name+ (and downstream), +name+ (both).
function focusSet(selector) {
  const match = /^(\+?)([^+\s]+)(\+?)$/.exec(selector.trim());
  if (!match) return null;
  // As in dbt: a bare name means a model, seed or snapshot; sources are source:<source>.<table>.
  const query = match[2].toLowerCase(), source = query.startsWith('source:') ? query.slice(7) : null;
  const node = source
    ? pipeline.nodes.find(n => n.type === 'source' && n.id.toLowerCase().endsWith(`.${source}`))
    : pipeline.nodes.find(n => n.id.toLowerCase() === query)
      || pipeline.nodes.find(n => n.type !== 'source' && n.name.toLowerCase() === query)
      || pipeline.nodes.find(n => n.name.toLowerCase() === query)
      || pipeline.nodes.find(n => n.name.toLowerCase().includes(query));
  if (!node) return null;
  const keep = new Set([node.id]);
  const walk = (from, next) => {
    const stack = [from];
    while (stack.length) {
      const id = stack.pop();
      for (const e of pipeline.edges) {
        const [a, b] = next === 'up' ? [e.to, e.from] : [e.from, e.to];
        if (a === id && !keep.has(b)) { keep.add(b); stack.push(b); }
      }
    }
  };
  if (match[1]) walk(node.id, 'up');
  if (match[3]) walk(node.id, 'down');
  return {node, keep};
}

function focusOn(selector) {
  const found = focusSet(selector);
  if (!found) return notice(`No node matches ${selector}.`, true);
  notice('');
  filters.focus = selector.trim();
  drawGraph();
  graph.fit();
}

function drawGraph() {
  const focus = filters.focus ? focusSet(filters.focus) : null;
  const shown = pipeline.nodes.filter(n => (!focus || focus.keep.has(n.id))
    && (n.status === 'external' || !filters.hiddenStatus.has(statusGroup(overlay?.get(n.id) || n.status)))
    && !filters.hiddenTypes.has(n.type));
  const ids = new Set(shown.map(n => n.id));
  graph.render(shown, pipeline.edges.filter(e => ids.has(e.from) && ids.has(e.to)), overlay);
  graph.select(ids.has(route.node) ? route.node : null, {silent: true});
  $('#focus').hidden = !filters.focus;
  $('#focus').replaceChildren(h('span', {text: `Showing ${filters.focus}`}),
    h('button', {type: 'button', class: 'ghost', 'aria-label': 'Show the whole pipeline', text: '✕',
      onclick: () => { filters.focus = ''; $('#find').value = ''; drawGraph(); graph.fit(); }}));
}

// The legend doubles as filters: status groups, and node types when there are several.
function renderLegend() {
  const types = [...new Set(pipeline.nodes.map(n => n.type))].sort();
  // Hiding the last visible item shows everything again, so a filter never leaves an empty graph.
  const toggle = (hidden, key, all) => () => {
    if (hidden.has(key)) hidden.delete(key);
    else if (all.filter(k => !hidden.has(k)).length === 1) hidden.clear();
    else hidden.add(key);
    drawGraph(); renderLegend();
  };
  const groups = Object.keys(STATUS_GROUPS);
  const status = groups.map(key => h('button', {type: 'button', 'aria-pressed': String(!filters.hiddenStatus.has(key)),
    title: `Show or hide ${key} nodes`, onclick: toggle(filters.hiddenStatus, key, groups)},
    h('i', {class: `status ${STATUS_GROUPS[key][0]}`}), key));
  const kinds = types.length > 1 ? types.map(type => h('button', {type: 'button', 'aria-pressed': String(!filters.hiddenTypes.has(type)),
    title: `Show or hide ${type}s`, onclick: toggle(filters.hiddenTypes, type, types)}, `${type}s`)) : [];
  fill($('#legend'), status, kinds.length ? h('span', {class: 'divider', 'aria-hidden': 'true'}) : null, kinds);
}

// While a run is queued or running, refresh statuses and the timeline every few seconds.
function watchPipeline() {
  clearTimeout(poller);
  if (!pipeline.runs.some(r => ACTIVE.has(r.status))) return;
  const key = pipeline.key;
  poller = setTimeout(async () => {
    if (route.view !== 'pipeline' || pipelineKey() !== key) return;
    try {
      const fresh = await api(`/projects/${project.id}/pipeline?${new URLSearchParams({environment: route.environment, ref: route.ref})}`);
      const before = pipeline.nodes.find(n => n.id === route.node)?.status;
      pipeline = Object.assign(fresh, {key});
      await loadOverlay();
      drawGraph(); renderLegend(); renderTimeline();
      if (route.node && pipeline.nodes.find(n => n.id === route.node)?.status !== before) renderPanel();
    } catch { /* try again on the next tick */ }
    watchPipeline();
  }, 4000);
}

async function renderBranches() {
  const select = $('#branch'), development = route.environment === 'development';
  let names = ['main'];
  if (development) {
    try { names = (await api(`/projects/${project.id}/branches`)).map(b => b.name); } catch { /* keep main */ }
    names = ['main', ...names.filter(n => n !== 'main').sort()];
  }
  // A run of a revision or a deleted branch keeps its own entry, so the route stays visible.
  if (!names.includes(route.ref)) names.push(route.ref);
  select.replaceChildren(...names.map(name => h('option', {value: name, selected: name === route.ref, text: name})));
  select.disabled = !development;
  select.title = development ? 'Show the pipeline of this branch' : `${LABELS[route.environment]} only builds main`;
  select.onchange = () => go({ref: select.value, node: null, run: null});
}

function renderTimeline() {
  const strip = $('#timeline');
  strip.replaceChildren(h('span', {class: 'label', text: 'RUNS'}),
    h('button', {type: 'button', 'aria-pressed': String(!route.run), onclick: () => go({run: null}, true), text: 'Latest status'}),
    ...pipeline.runs.slice(0, 14).map(run => h('button', {
      type: 'button', 'aria-pressed': String(route.run === run.id), title: `${run.command} ${run.selector || ''} · ${run.ref} · ${run.triggered_by}`,
      onclick: () => go({run: run.id}, true),
    }, h('i', {class: `status ${run.status}`}), `${run.command} · ${when(run.created_at)}`)));
}

async function buildNow(button) {
  await startBuild('', button);
}

// Start a build on the shown branch (acceptance and production build main) and follow it live.
async function startBuild(select, button) {
  const ref = route.environment === 'development' ? route.ref : 'main';
  const what = select ? `${select} of ${project.name}` : project.name;
  if (!confirm(`Build ${what} (${ref}) in ${LABELS[route.environment]} now? It writes the team's tables.`)) return;
  if (button) button.disabled = true;
  try {
    const run = await api(`/projects/${project.id}/runs`, {method: 'POST', body: JSON.stringify({command: 'build', environment: route.environment, ref, select})});
    notice(`Run ${run.id} started. Statuses update while it runs.`);
    go({run: run.id}, true);
  } catch (error) { notice(error.message, true); } finally { if (button) button.disabled = false; }
}

// The dbt selector of a node: models and seeds by name, sources as source:<source>.<table>.
function selectorOf(node) {
  return node.type === 'source' ? `source:${node.id.split('.').slice(2).join('.')}` : node.name;
}

function catalogLink(node) {
  if (!me.userPortalUrl || !node.databaseId || !node.namespace) return null;
  const params = new URLSearchParams({team: project.team, environment: route.environment, database: node.databaseId});
  node.namespace.split('.').forEach(part => params.append('namespace', part));
  params.set('kind', 'table'); params.set('name', node.table);
  return h('a', {class: 'button quiet', href: `${me.userPortalUrl}/#catalog?${params}`, target: '_blank', rel: 'noopener', text: 'Open table in catalog'});
}

async function renderPanel() {
  const panel = $('#panel'), id = route.node, token = ++detailToken;
  if (!id || !pipeline.nodes.some(n => n.id === id)) { panel.hidden = true; return; }
  panel.hidden = false;
  panel.replaceChildren(h('p', {class: 'eyebrow', text: 'Loading…'}));
  let node;
  try { node = await api(`/projects/${project.id}/nodes/${encodeURIComponent(id)}?${new URLSearchParams({environment: route.environment, ref: route.ref})}`); }
  catch (error) { panel.replaceChildren(h('p', {text: error.message})); return; }
  if (token !== detailToken) return;
  const status = overlay?.get(id) || node.status;
  const up = pipeline.edges.filter(e => e.to === id).map(e => e.from), down = pipeline.edges.filter(e => e.from === id).map(e => e.to);
  const link = target => h('button', {type: 'button', onclick: () => { go({node: target}, true); graph.center(target); }, text: target.split('.').slice(-1)[0]});
  const code = h('pre', {class: 'code', text: node.compiledSql || node.sql || '—'});
  const editable = node.path && WRITERS.includes(project.role);
  const source = h('div', {}, h('h3', {text: node.sql ? 'SQL' : 'File'}), h('div', {class: 'tabs'},
    node.sql ? h('button', {class: 'quiet', type: 'button', onclick: () => { code.textContent = node.compiledSql || '—'; }, text: 'Compiled'}) : null,
    node.sql ? h('button', {class: 'quiet', type: 'button', onclick: () => { code.textContent = node.sql; }, text: 'Source'}) : null,
    editable ? h('button', {type: 'button', onclick: () => openEditor(node, source), text: 'Edit'}) : null), node.sql ? code : null);
  const typed = node.columns.some(c => c.type);
  const env = project.environments.find(e => e.environment === route.environment);
  const canBuild = env?.enabled && WRITERS.includes(project.role) && node.type !== 'exposure';
  const select = selectorOf(node);
  const actions = h('div', {class: 'tabs actions'},
    canBuild && node.type !== 'source' ? h('button', {type: 'button', onclick: e => startBuild(select, e.currentTarget), text: 'Build'}) : null,
    canBuild && node.type !== 'source' ? h('button', {type: 'button', class: 'quiet', title: `dbt build --select +${select}`, onclick: e => startBuild(`+${select}`, e.currentTarget), text: '+ Upstream'}) : null,
    canBuild ? h('button', {type: 'button', class: 'quiet', title: `dbt build --select ${select}+`, onclick: e => startBuild(`${select}+`, e.currentTarget), text: node.type === 'source' ? 'Build downstream' : '+ Downstream'}) : null,
    h('button', {type: 'button', class: 'quiet', title: `Show only +${select}+`, onclick: () => { $('#find').value = `+${select}+`; focusOn(`+${select}+`); }, text: 'Focus lineage'}),
    node.path ? h('button', {type: 'button', class: 'quiet', onclick: () => go({view: 'files', file: node.path, node: null}), text: 'Open file'}) : null);
  const previewBox = h('div', {class: 'preview'});
  const previewable = ['model', 'seed', 'snapshot'].includes(node.type) && env?.enabled;
  const preview = previewable ? h('div', {}, h('h3', {text: 'Rows'}),
    h('button', {type: 'button', class: 'quiet', onclick: event => previewRows(node, previewBox, event.currentTarget), text: 'Preview 20 rows'}), previewBox) : null;
  fill(panel,
    h('button', {class: 'ghost close', type: 'button', 'aria-label': 'Close', onclick: () => go({node: null}, true), text: '✕'}),
    h('div', {}, h('p', {class: 'eyebrow', text: `${node.type}${node.materialized ? ' · ' + node.materialized : ''}`}), h('h2', {text: node.name})),
    h('div', {class: 'chips'}, status === 'external' ? null : chip(status), node.tests.pass ? h('span', {class: 'chip success', text: `✓ ${node.tests.pass} test${node.tests.pass === 1 ? '' : 's'}`}) : null,
      node.tests.fail ? h('span', {class: 'chip failed', text: `✕ ${node.tests.fail} failing`}) : null,
      node.tests.warn ? h('span', {class: 'chip running', text: `! ${node.tests.warn} warning${node.tests.warn === 1 ? '' : 's'}`}) : null),
    actions,
    node.description ? h('p', {text: node.description}) : h('p', {class: 'small', text: 'No description. Describe it in the model YAML; it becomes the table comment in the catalog.'}),
    node.message ? h('pre', {class: 'code', text: node.message}) : null,
    h('dl', {class: 'facts'},
      ...[['Table', node.namespace ? `${node.database}.${node.namespace}.${node.table}` : '—'], ['Last run', when(node.lastRunAt)],
          ['Duration', node.executionTime ? `${node.executionTime.toFixed(2)} s` : '—'], ...(node.rowsAffected ? [['Rows', node.rowsAffected]] : []), ['File', node.path || '—']]
        .flatMap(([k, v]) => [h('dt', {text: k}), h('dd', {text: String(v)})])),
    catalogLink(node),
    preview,
    node.columns.length ? h('div', {}, h('h3', {text: 'Columns'}), h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Name'}), typed ? h('th', {text: 'Type'}) : null, h('th', {text: 'Description'}))),
      h('tbody', {}, ...node.columns.map(c => h('tr', {}, h('td', {text: c.name}), typed ? h('td', {text: c.type || ''}) : null, h('td', {text: c.description || ''})))))) : null,
    node.testResults.length ? h('div', {}, h('h3', {text: 'Tests'}), h('div', {class: 'links'}, ...node.testResults.map(t => h('span', {}, h('i', {class: `status ${t.status}`}), ` ${t.test || t.name}${t.column ? ' · ' + t.column : ''}`)))) : null,
    h('div', {}, h('h3', {text: 'Upstream'}), h('div', {class: 'links'}, ...(up.length ? up.map(link) : [h('span', {class: 'small', text: '—'})]))),
    h('div', {}, h('h3', {text: 'Downstream'}), h('div', {class: 'links'}, ...(down.length ? down.map(link) : [h('span', {class: 'small', text: '—'})]))),
    node.sql || editable ? source : null,
  );
}

// A preview runs `dbt show` on the platform with a read-only token, at the shown branch.
async function previewRows(node, box, button) {
  button.disabled = true;
  fill(box, h('p', {class: 'small', text: 'Running a preview on the platform…'}));
  try {
    const ref = route.environment === 'development' ? route.ref : 'main';
    const result = await api(`/projects/${project.id}/preview`, {method: 'POST', body: JSON.stringify({model: node.name, environment: route.environment, ref, limit: 20})});
    if (!result.rows) return fill(box, h('pre', {class: 'code', text: result.output || 'The preview failed.'}));
    if (!result.rows.length) return fill(box, h('p', {class: 'small', text: 'No rows.'}));
    const columns = Object.keys(result.rows[0]);
    fill(box, h('div', {class: 'scroll'}, h('table', {class: 'rows'},
      h('thead', {}, h('tr', {}, ...columns.map(c => h('th', {text: c})))),
      h('tbody', {}, ...result.rows.map(row => h('tr', {}, ...columns.map(c => h('td', {title: String(row[c] ?? ''), text: row[c] === null ? '∅' : String(row[c])}))))))));
  } catch (error) { fill(box, h('p', {class: 'small error', text: error.message})); } finally { button.disabled = false; }
}

// Editing a node's file: commits go to a branch (main changes only through a merge), a writer
// builds that branch in development, and a team administrator merges it.
function savedBranch() {
  try { return JSON.parse(localStorage.getItem('dbt-edit-branch') || '{}')[project.id] || null; } catch { return null; }
}
function rememberBranch(branch) {
  try {
    const saved = JSON.parse(localStorage.getItem('dbt-edit-branch') || '{}');
    localStorage.setItem('dbt-edit-branch', JSON.stringify({...saved, [project.id]: branch}));
  } catch { /* private mode */ }
}
const filePath = path => path.split('/').map(encodeURIComponent).join('/');

async function openEditor(node, box) {
  const id = project.id, token = detailToken;
  let branches;
  try { branches = new Set((await api(`/projects/${id}/branches`)).map(b => b.name)); }
  catch (error) { return notice(error.message, true); }
  if (token !== detailToken) return;
  const branch = h('input', {type: 'text', list: 'edit-branches', value: savedBranch() || `edit/${node.name}`, spellcheck: 'false', autocomplete: 'off'});
  const text = h('textarea', {class: 'code editor', spellcheck: 'false', rows: '18', 'aria-label': `Contents of ${node.path}`});
  const message = h('input', {type: 'text', value: `Edit ${node.name}`, autocomplete: 'off'});
  const status = h('p', {class: 'small', role: 'status'});
  const save = h('button', {type: 'button', text: 'Save to branch'});
  const after = h('div', {class: 'tabs'});
  let loaded = null;
  const say = (words, error = false) => { status.textContent = words; status.classList.toggle('error', error); };

  async function load() {
    const name = branch.value.trim(), ref = branches.has(name) ? name : 'main';
    loaded = null; save.disabled = true; after.replaceChildren(); say(`Loading ${node.path} from ${ref}…`);
    try {
      const file = await api(`/projects/${id}/files/${filePath(node.path)}?ref=${encodeURIComponent(ref)}`);
      text.value = file.content; loaded = {revision: file.revision, content: file.content}; save.disabled = false;
      say(ref === name ? `Editing on ${name}.` : `Editing main's version. Saving creates the branch ${name || '…'}.`);
    } catch (error) { say(error.message, true); }
  }

  async function commit() {
    const name = branch.value.trim();
    if (!name || name === 'main') return say('Choose a branch other than main. main changes only through a merge.', true);
    if (!loaded) return;
    save.disabled = true;
    try {
      if (!branches.has(name)) {
        // The branch starts at the revision this file was read from, so the commit below cannot be stale.
        await api(`/projects/${id}/branches`, {method: 'POST', body: JSON.stringify({name, source: loaded.revision})});
        branches.add(name);
      }
      const result = await api(`/projects/${id}/commits`, {method: 'POST', body: JSON.stringify({
        branch: name, files: {[node.path]: text.value}, message: message.value.trim() || `Edit ${node.name}`, expected_revision: loaded.revision})});
      loaded = {revision: result.revision, content: text.value}; rememberBranch(name);
      say(`Saved to ${name} @ ${result.revision.slice(0, 7)}.`);
      afterSave(name);
    } catch (error) {
      say(error.code === 'stale_branch' ? `${name} changed since you opened it. Copy your edits, then reload the branch.` : error.message, true);
    } finally { save.disabled = !loaded; }
  }

  function afterSave(name) {
    const development = project.environments.find(e => e.environment === 'development');
    after.replaceChildren(
      development?.enabled ? h('button', {class: 'quiet', type: 'button', onclick: event => buildBranch(event.currentTarget, name), text: 'Build in Development'}) : null,
      ADMINS.includes(project.role) ? h('button', {class: 'quiet', type: 'button', onclick: event => merge(event.currentTarget, name), text: 'Merge to main'}) : null);
  }

  async function buildBranch(button, name) {
    button.disabled = true;
    try {
      const run = await api(`/projects/${id}/runs`, {method: 'POST', body: JSON.stringify({command: 'build', environment: 'development', ref: name, select: `${node.name}+`})});
      say(`Building ${name} in Development (${run.id})…`);
      const done = await api(`/runs/${run.id}?wait=600`);
      if (done.status === 'running' || done.status === 'queued') return say(`Run ${run.id} is still going. Follow it under Runs.`);
      go({environment: 'development', view: 'pipeline', ref: name, run: run.id, node: node.id});
    } catch (error) { say(error.message, true); button.disabled = false; }
  }

  async function buildMain(button) {
    button.disabled = true;
    try {
      const run = await api(`/projects/${id}/runs`, {method: 'POST', body: JSON.stringify({command: 'build', environment: 'development', ref: 'main'})});
      say(`Building main in Development (${run.id})…`);
      const done = await api(`/runs/${run.id}?wait=600`);
      if (done.status === 'running' || done.status === 'queued') return say(`Run ${run.id} is still going. Follow it under Runs.`);
      go({environment: 'development', view: 'pipeline', ref: 'main', run: run.id, node: node.id});
    } catch (error) { say(error.message, true); button.disabled = false; }
  }

  async function merge(button, name) {
    if (!confirm(`Merge ${name} into main? Acceptance and production build main.`)) return;
    button.disabled = true;
    try {
      const result = await api(`/projects/${id}/merge`, {method: 'POST', body: JSON.stringify({branch: name})});
      say((result.merged ? `Merged ${name} into main @ ${result.revision.slice(0, 7)}.` : `main already contains ${name}.`)
        + (result.hint ? ' The pipeline shows main once main is built in Development.' : ''));
      const development = project.environments.find(e => e.environment === 'development');
      if (result.hint && development?.enabled) after.replaceChildren(h('button', {class: 'quiet', type: 'button', onclick: event => buildMain(event.currentTarget), text: 'Build main in Development'}));
    } catch (error) {
      say(error.code === 'merge_conflict' ? `${error.message} Conflicting: ${(error.detail?.files || []).join(', ')}.` : error.message, true);
      button.disabled = false;
    }
  }

  branch.addEventListener('change', () => {
    if (branch.value.trim() === 'main') return say('Choose a branch other than main. main changes only through a merge.', true);
    if (loaded && text.value !== loaded.content && !confirm('Discard your edits and load the file from this branch?')) return;
    load();
  });
  text.addEventListener('keydown', event => {
    if (event.key !== 'Tab' || event.shiftKey) return;
    event.preventDefault();
    text.setRangeText('  ', text.selectionStart, text.selectionEnd, 'end');
  });
  save.onclick = commit;
  box.className = 'edit';
  fill(box,
    h('h3', {text: `Edit · ${node.path}`}),
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Branch'}), branch),
    h('datalist', {id: 'edit-branches'}, ...[...branches].filter(b => b !== 'main').map(b => h('option', {value: b}))),
    text,
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Commit message'}), message),
    h('div', {class: 'tabs'}, save, h('button', {class: 'quiet', type: 'button', onclick: () => renderPanel(), text: 'Cancel'})),
    status, after);
  await load();
}

async function renderRuns() {
  clearTimeout(poller);
  const all = await api(`/projects/${project.id}/runs?environment=${route.environment}&limit=100`);
  const runs = includePreviews ? all : all.filter(r => !['show', 'compile', 'parse'].includes(r.command) || r.id === route.run);
  const toggle = h('label', {class: 'check'}, h('input', {type: 'checkbox', checked: includePreviews,
    onchange: event => { includePreviews = event.target.checked; renderRuns(); }}), 'Include previews (show, compile, parse)');
  $('#run-table').replaceChildren(toggle, runs.length ? h('table', {}, h('thead', {}, h('tr', {}, ...['Status', 'Command', 'Revision', 'By', 'Started', 'Duration'].map(t => h('th', {text: t})))),
    h('tbody', {}, ...runs.map(run => h('tr', {class: 'run-row', tabindex: '0', onclick: () => go({run: run.id}, true)},
      h('td', {}, chip(run.status)), h('td', {text: `${run.command} ${run.selector || ''}`.trim()}),
      h('td', {text: `${run.ref} @ ${run.revision.slice(0, 7)}`}),
      h('td', {}, run.triggered_by, run.agent ? h('span', {class: 'agent', text: 'agent'}) : null),
      h('td', {class: 'nowrap', text: when(run.created_at)}), h('td', {class: 'nowrap', text: duration(run)}))))) : h('p', {text: 'No runs in this environment yet.'}));
  const detail = $('#run-detail');
  detail.hidden = !route.run;
  const key = `${project.id}:${route.environment}:${route.run || ''}`;
  const again = () => { poller = setTimeout(() => { if (route.view === 'runs' && `${project.id}:${route.environment}:${route.run || ''}` === key) renderRuns(); }, 3000); };
  if (!route.run) { if (all.some(r => ACTIVE.has(r.status))) again(); return; }
  const [run, logs] = await Promise.all([api(`/runs/${route.run}`), api(`/runs/${route.run}/logs?tail=400`)]);
  const cancel = ACTIVE.has(run.status) && WRITERS.includes(project.role) ? h('button', {type: 'button', class: 'danger', text: 'Stop run', onclick: async event => {
    if (!confirm(`Stop ${run.id}? Tables it already replaced stay replaced.`)) return;
    event.currentTarget.disabled = true;
    try { await api(`/runs/${run.id}/cancel`, {method: 'POST'}); renderRuns(); } catch (error) { notice(error.message, true); }
  }}) : null;
  const log = h('pre', {class: 'log', text: logs.lines.join('\n') || 'No log.'});
  fill(detail, h('div', {class: 'chips'}, chip(run.status), h('span', {class: 'chip', text: run.id}), cancel),
    run.error ? h('p', {text: run.error}) : null,
    run.results.length ? h('table', {}, h('tbody', {}, ...run.results.map(r => h('tr', {}, h('td', {}, chip(r.status)), h('td', {text: r.node}),
      h('td', {class: 'nowrap', text: r.executionTime ? `${r.executionTime.toFixed(2)} s` : ''}), h('td', {class: 'message', text: r.message || ''}))))) : null,
    h('h2', {text: 'Log'}), log,
    h('a', {class: 'button quiet', href: `#/projects/${project.id}/pipeline?${new URLSearchParams({environment: run.environment, ref: run.ref, run: run.id})}`, text: 'Show this run on the pipeline'}));
  if (ACTIVE.has(run.status)) { log.scrollTop = log.scrollHeight; again(); } else if (all.some(r => ACTIVE.has(r.status))) again();
}

function theme() {
  const saved = (() => { try { return localStorage.getItem('iceberg-theme'); } catch { return null; } })();
  const apply = mode => { document.documentElement.dataset.theme = mode;
    document.querySelector('meta[name="theme-color"]').setAttribute('content', mode === 'light' ? '#f5f5f5' : '#000000');
    $('#theme').textContent = mode === 'light' ? 'Dark / ●' : 'Light / ○';
    $('#theme').setAttribute('aria-label', `Switch to ${mode === 'light' ? 'dark' : 'light'} mode`); };
  apply(saved || 'dark');
  $('#theme').onclick = () => { const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; apply(next); try { localStorage.setItem('iceberg-theme', next); } catch { /* private mode */ } };
}

$('#logout').onclick = async () => {
  const result = await (await fetch('/auth/logout', {method: 'POST', headers: {'X-Iceberg-Dbt': '1'}, credentials: 'same-origin'})).json();
  location.href = result.logoutUrl || '/';
};
window.addEventListener('hashchange', render);
window.addEventListener('resize', () => graph?.fit());
theme();
render();
