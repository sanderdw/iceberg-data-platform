'use strict';
// The pipeline viewer: read-only views over the REST API. Agents change pipelines through MCP;
// people see what exists, how it ran and where each table comes from.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const WRITERS = ['writer', 'admin', 'bucket-admin'];
let me = null, project = null, pipeline = null, graph = null, route = {}, overlay = null, detailToken = 0;

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
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status}).`);
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
  for (const id of ['signin', 'projects', 'project']) $(`#${id}`).hidden = id !== section;
}
function showSignin() {
  show('signin'); $('#identity').hidden = true;
  $('#signin-link').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`;
}

function parseRoute() {
  const [path, query = ''] = location.hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean), params = new URLSearchParams(query);
  return {project: parts[0] === 'projects' ? parts[1] : null, view: parts[2] || 'pipeline',
          environment: ENVIRONMENTS.includes(params.get('environment')) ? params.get('environment') : 'development',
          node: params.get('node'), run: params.get('run')};
}
function go(next, replace = false) {
  const r = {...route, ...next}, params = new URLSearchParams({environment: r.environment});
  if (r.node) params.set('node', r.node);
  if (r.run) params.set('run', r.run);
  const hash = r.project ? `#/projects/${r.project}/${r.view}?${params}` : '#/';
  if (location.hash === hash) render(); else if (replace) { history.replaceState(null, '', hash); render(); } else location.hash = hash;
}

function crumbs() {
  const nav = $('#crumbs'); nav.replaceChildren(h('a', {href: '#/', text: 'Projects'}));
  if (project) nav.append(' / ', h('span', {text: `${teamName(project.team)} · ${project.name}`}));
}
function teamName(id) { return (me?.memberships.find(m => m.team === id) || {}).teamName || id; }

async function render() {
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
    const key = `${project.id}:${route.environment}:${route.run || ''}`;
    if (route.view === 'pipeline' && pipeline && pipeline.key === key) {
      // Only the selection changed: keep the drawn graph and its viewport.
      graph.select(route.node, {silent: true});
      return renderPanel();
    }
    pipeline = null;
    if (route.view === 'runs') return renderRuns();
    if (route.view === 'schedules') return renderSchedules();
    return renderPipeline();
  } catch (error) { if (!$('#signin').hidden) return; show(route.project ? 'project' : 'projects'); notice(error.message, true); }
}

async function renderProjects() {
  show('projects');
  const projects = await api('/projects'), list = $('#project-list');
  list.replaceChildren(...projects.map(p => h('a', {class: 'card project-card', href: `#/projects/${p.id}/pipeline`},
    h('p', {class: 'eyebrow', text: teamName(p.team)}), h('h2', {text: p.name}),
    h('p', {text: p.description || `Models land in ${p.default_database} by default.`}),
    h('div', {class: 'chips'}, h('span', {class: 'chip', text: p.role}), h('span', {class: 'chip', text: `created by ${p.created_by}`})))));
  if (!projects.length) list.append(h('div', {class: 'card'}, h('h2', {text: 'No pipelines yet'}),
    h('p', {text: 'Ask your AI agent to create a dbt project for your team, or call POST /api/v1/projects. A new project starts from a runnable starter pipeline.'})));
  $('#mcp-command').textContent = `claude mcp add --transport http --callback-port 3010 --client-id ext-dbt-mcp dbt ${location.origin}/mcp`;
}

function renderProjectBar() {
  $('#project-team').textContent = `${teamName(project.team)} · ${project.role}`;
  $('#project-name').textContent = project.name;
  const enabled = new Map(project.environments.map(e => [e.environment, e.enabled]));
  $('#environments').replaceChildren(...ENVIRONMENTS.map(env => h('button', {
    type: 'button', role: 'tab', 'aria-selected': String(env === route.environment),
    title: enabled.get(env) ? '' : 'dbt is not enabled for this environment', onclick: () => go({environment: env, node: null, run: null}),
  }, LABELS[env], enabled.get(env) ? '' : h('span', {class: 'off', text: ' ·off'}))));
  for (const button of document.querySelectorAll('#views button')) {
    button.setAttribute('aria-selected', String(button.dataset.view === route.view));
    button.onclick = () => go({view: button.dataset.view, node: null, run: null});
  }
  for (const [id, view] of [['#pipeline-view', 'pipeline'], ['#runs-view', 'runs'], ['#schedules-view', 'schedules']]) $(id).hidden = route.view !== view;
  notice(enabled.get(route.environment) ? '' : `dbt is not enabled for ${LABELS[route.environment]} in this team. A team administrator enables it through the API or an agent (enable_environment).`);
}

async function renderPipeline() {
  pipeline = await api(`/projects/${project.id}/pipeline?environment=${route.environment}`);
  pipeline.key = `${project.id}:${route.environment}:${route.run || ''}`;
  graph ||= new window.PipelineGraph($('#graph'), {onSelect: id => { if (id) graph.center(id); go({node: id}, true); }});
  const empty = !pipeline.nodes.length;
  $('#graph-empty').hidden = !empty;
  if (empty) $('#graph-empty').replaceChildren(h('div', {}, h('h2', {text: 'No pipeline drawn yet'}), h('p', {text: pipeline.hint})));
  overlay = null;
  if (route.run) {
    try {
      const run = await api(`/runs/${route.run}`);
      overlay = new Map(run.results.map(r => [r.node, r.status === 'pass' ? 'success' : r.status]));
    } catch { overlay = null; }
  }
  graph.render(pipeline.nodes, pipeline.edges, overlay);
  if (!graph.fitted || graph.fitted !== `${project.id}:${route.environment}`) { graph.fit(); graph.fitted = `${project.id}:${route.environment}`; }
  graph.select(route.node, {silent: true});
  $('#find').oninput = event => graph.highlight(event.target.value);
  $('#find').onkeydown = event => {
    if (event.key !== 'Enter') return;
    const query = event.target.value.trim().toLowerCase(), hit = pipeline.nodes.find(n => n.name.toLowerCase().includes(query));
    if (hit) { go({node: hit.id}, true); graph.center(hit.id); }
  };
  $('#fit').onclick = () => graph.fit();
  renderTimeline();
  const docs = pipeline.runs.find(r => r.command === 'docs' && r.status === 'success');
  $('#docs-link').hidden = !docs;
  if (docs) $('#docs-link').href = `/docs-site/${docs.id}/index.html`;
  const env = project.environments.find(e => e.environment === route.environment);
  const button = $('#run-now');
  button.hidden = !(env?.enabled && WRITERS.includes(project.role));
  button.onclick = () => buildNow(button);
  await renderPanel();
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
  if (!confirm(`Build ${project.name} (main) in ${LABELS[route.environment]} now? It writes the team's tables.`)) return;
  button.disabled = true;
  try {
    const run = await api(`/projects/${project.id}/runs`, {method: 'POST', body: JSON.stringify({command: 'build', environment: route.environment, ref: 'main'})});
    notice(`Run ${run.id} started. The pipeline updates when it finishes.`);
    await api(`/runs/${run.id}?wait=600`);
    notice('');
    await renderPipeline();
  } catch (error) { notice(error.message, true); } finally { button.disabled = false; }
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
  try { node = await api(`/projects/${project.id}/nodes/${encodeURIComponent(id)}?environment=${route.environment}`); }
  catch (error) { panel.replaceChildren(h('p', {text: error.message})); return; }
  if (token !== detailToken) return;
  const status = overlay?.get(id) || node.status;
  const up = pipeline.edges.filter(e => e.to === id).map(e => e.from), down = pipeline.edges.filter(e => e.from === id).map(e => e.to);
  const link = target => h('button', {type: 'button', onclick: () => { go({node: target}, true); graph.center(target); }, text: target.split('.').slice(-1)[0]});
  const code = h('pre', {class: 'code', text: node.compiledSql || node.sql || '—'});
  const typed = node.columns.some(c => c.type);
  fill(panel,
    h('button', {class: 'ghost close', type: 'button', 'aria-label': 'Close', onclick: () => go({node: null}, true), text: '✕'}),
    h('div', {}, h('p', {class: 'eyebrow', text: `${node.type}${node.materialized ? ' · ' + node.materialized : ''}`}), h('h2', {text: node.name})),
    h('div', {class: 'chips'}, chip(status), node.tests.pass ? h('span', {class: 'chip success', text: `✓ ${node.tests.pass} test${node.tests.pass === 1 ? '' : 's'}`}) : null,
      node.tests.fail ? h('span', {class: 'chip failed', text: `✕ ${node.tests.fail} failing`}) : null,
      node.tests.warn ? h('span', {class: 'chip running', text: `! ${node.tests.warn} warning${node.tests.warn === 1 ? '' : 's'}`}) : null),
    node.description ? h('p', {text: node.description}) : h('p', {class: 'small', text: 'No description. Describe it in the model YAML; it becomes the table comment in the catalog.'}),
    node.message ? h('pre', {class: 'code', text: node.message}) : null,
    h('dl', {class: 'facts'},
      ...[['Table', node.namespace ? `${node.database}.${node.namespace}.${node.table}` : '—'], ['Last run', when(node.lastRunAt)],
          ['Duration', node.executionTime ? `${node.executionTime.toFixed(2)} s` : '—'], ...(node.rowsAffected ? [['Rows', node.rowsAffected]] : []), ['File', node.path || '—']]
        .flatMap(([k, v]) => [h('dt', {text: k}), h('dd', {text: String(v)})])),
    catalogLink(node),
    node.columns.length ? h('div', {}, h('h3', {text: 'Columns'}), h('table', {}, h('thead', {}, h('tr', {}, h('th', {text: 'Name'}), typed ? h('th', {text: 'Type'}) : null, h('th', {text: 'Description'}))),
      h('tbody', {}, ...node.columns.map(c => h('tr', {}, h('td', {text: c.name}), typed ? h('td', {text: c.type || ''}) : null, h('td', {text: c.description || ''})))))) : null,
    node.testResults.length ? h('div', {}, h('h3', {text: 'Tests'}), h('div', {class: 'links'}, ...node.testResults.map(t => h('span', {}, h('i', {class: `status ${t.status}`}), ` ${t.test || t.name}${t.column ? ' · ' + t.column : ''}`)))) : null,
    h('div', {}, h('h3', {text: 'Upstream'}), h('div', {class: 'links'}, ...(up.length ? up.map(link) : [h('span', {class: 'small', text: '—'})]))),
    h('div', {}, h('h3', {text: 'Downstream'}), h('div', {class: 'links'}, ...(down.length ? down.map(link) : [h('span', {class: 'small', text: '—'})]))),
    node.sql ? h('div', {}, h('h3', {text: 'SQL'}), h('div', {class: 'tabs'},
      h('button', {class: 'quiet', type: 'button', onclick: () => { code.textContent = node.compiledSql || '—'; }, text: 'Compiled'}),
      h('button', {class: 'quiet', type: 'button', onclick: () => { code.textContent = node.sql; }, text: 'Source'})), code) : null,
  );
}

async function renderRuns() {
  const runs = await api(`/projects/${project.id}/runs?environment=${route.environment}&limit=100`);
  $('#run-table').replaceChildren(runs.length ? h('table', {}, h('thead', {}, h('tr', {}, ...['Status', 'Command', 'Revision', 'By', 'Started', 'Duration'].map(t => h('th', {text: t})))),
    h('tbody', {}, ...runs.map(run => h('tr', {class: 'run-row', tabindex: '0', onclick: () => go({run: run.id}, true)},
      h('td', {}, chip(run.status)), h('td', {text: `${run.command} ${run.selector || ''}`.trim()}),
      h('td', {text: `${run.ref} @ ${run.revision.slice(0, 7)}`}),
      h('td', {}, run.triggered_by, run.agent ? h('span', {class: 'agent', text: 'agent'}) : null),
      h('td', {text: when(run.created_at)}), h('td', {text: duration(run)}))))) : h('p', {text: 'No runs in this environment yet.'}));
  const detail = $('#run-detail');
  detail.hidden = !route.run;
  if (!route.run) return;
  const [run, logs] = await Promise.all([api(`/runs/${route.run}`), api(`/runs/${route.run}/logs?tail=400`)]);
  fill(detail, h('div', {class: 'chips'}, chip(run.status), h('span', {class: 'chip', text: run.id})),
    run.error ? h('p', {text: run.error}) : null,
    run.results.length ? h('table', {}, h('tbody', {}, ...run.results.map(r => h('tr', {}, h('td', {}, chip(r.status)), h('td', {text: r.node}),
      h('td', {text: r.executionTime ? `${r.executionTime.toFixed(2)} s` : ''}), h('td', {text: r.message || ''}))))) : null,
    h('h2', {text: 'Log'}), h('pre', {class: 'log', text: logs.lines.join('\n') || 'No log.'}),
    h('a', {class: 'button quiet', href: `#/projects/${project.id}/pipeline?environment=${run.environment}&run=${run.id}`, text: 'Show this run on the pipeline'}));
}

async function renderSchedules() {
  const schedules = await api(`/projects/${project.id}/schedules`);
  $('#schedule-table').replaceChildren(schedules.length ? h('table', {}, h('thead', {}, h('tr', {}, ...['Environment', 'Cron (UTC)', 'Command', 'Branch', 'Next run', 'State'].map(t => h('th', {text: t})))),
    h('tbody', {}, ...schedules.map(s => h('tr', {}, h('td', {text: LABELS[s.environment]}), h('td', {text: s.cron}),
      h('td', {text: `${s.command} ${s.selector || ''}`.trim()}), h('td', {text: s.ref}),
      h('td', {text: new Date(s.next_run_at * 1000).toLocaleString()}), h('td', {text: s.enabled ? 'enabled' : 'paused'}))))) :
    h('p', {text: 'No schedules. Agents create them with set_schedule (cron in UTC).'}));
}

function theme() {
  const saved = (() => { try { return localStorage.getItem('dbt-theme'); } catch { return null; } })();
  const apply = mode => { document.documentElement.dataset.theme = mode; $('#theme').textContent = mode === 'light' ? 'Dark / ●' : 'Light / ○'; };
  apply(saved || 'dark');
  $('#theme').onclick = () => { const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; apply(next); try { localStorage.setItem('dbt-theme', next); } catch { /* private mode */ } };
}

$('#logout').onclick = async () => {
  const result = await (await fetch('/auth/logout', {method: 'POST', headers: {'X-Iceberg-Dbt': '1'}, credentials: 'same-origin'})).json();
  location.href = result.logoutUrl || '/';
};
window.addEventListener('hashchange', render);
window.addEventListener('resize', () => graph?.fit());
theme();
render();
