'use strict';
// The Ingestion page over the REST API: pipelines of your teams, their secrets, schedules and runs.
// Agents do the same through the MCP server; the server checks every permission again.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const SOURCE_LABELS = {rest_api: 'REST API', sql_database: 'SQL database', filesystem: 'Files'};
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
const ACTIVE = new Set(['queued', 'running']);
const DISPOSITIONS = {append: 'Append rows', replace: 'Replace rows', merge: 'Merge on key'};
const CRON_PRESETS = [['Hourly', '0 * * * *'], ['Daily 05:00', '0 5 * * *'], ['Weekdays 05:00', '0 5 * * 1-5'], ['Mondays 05:00', '0 5 * * 1']];
const BLANKS = {
  rest_api: {client: {base_url: 'https://api.example.com/v1/', paginator: {type: 'auto'}},
             resources: [{name: 'items', endpoint: {path: 'items'}}]},
  sql_database: {dialect: 'postgresql', host: 'db.example.com', port: 5432, database: 'app', username: 'reader',
                 password: '$secret:password', tls: 'verify-full', tables: [{name: 'customers'}]},
  filesystem: {url: 'https://data.example.com/export.csv', format: 'csv', table: 'export'},
};
const number = new Intl.NumberFormat();
let me = null, templates = null, route = {}, poller = null, flash = null, renderToken = 0;

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'value') node.value = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}
// replaceChildren would print "null" for skipped optional parts.
function fill(parent, ...children) { parent.replaceChildren(...children.flat().filter(c => c !== null && c !== undefined && c !== false)); }

async function api(path, options = {}) {
  const write = options.method && options.method !== 'GET';
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin',
    headers: {...(options.body ? {'Content-Type': 'application/json'} : {}), ...(write ? {'X-Iceberg-Ingestion': '1'} : {})},
  });
  const body = response.headers.get('content-type')?.includes('json') ? await response.json() : {};
  if (response.status === 401) { showSignin(); throw Object.assign(new Error(body.error || 'Sign in to continue.'), {signin: true}); }
  if (!response.ok) throw Object.assign(new Error(body.error || `Request failed (${response.status}).`), {code: body.code, detail: body.detail});
  return body;
}
const send = (path, method, body) => api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});

// Formatting
function when(seconds) {
  if (!seconds) return '—';
  const date = new Date(seconds * 1000), diff = (Date.now() - date) / 1000, ahead = diff < 0, d = Math.abs(diff);
  const text = d < 60 ? (ahead ? 'in a moment' : 'just now') : d < 3600 ? `${Math.round(d / 60)} min` : d < 86400 ? `${Math.round(d / 3600)} h` : null;
  if (!text) return date.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
  return d < 60 ? text : ahead ? `in ${text}` : `${text} ago`;
}
function time(seconds) {
  return h('time', {datetime: seconds ? new Date(seconds * 1000).toISOString() : null,
                    title: seconds ? new Date(seconds * 1000).toLocaleString(undefined, {dateStyle: 'full', timeStyle: 'long'}) : null,
                    text: when(seconds)});
}
function duration(run) {
  if (!run.startedAt) return '—';
  const s = Math.max(0, Math.round((run.finishedAt || Date.now() / 1000) - run.startedAt));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}
function chip(status, label) { return h('span', {class: `chip ${status}`}, h('i', {class: `status ${status}`, 'aria-hidden': 'true'}), label || status); }
function typeChip(type) { return h('span', {class: 'chip type', text: SOURCE_LABELS[type] || type}); }
function target(p) {
  return h('span', {class: 'target'}, p.databaseName || p.database, h('span', {class: 'sep', 'aria-hidden': 'true', text: '›'}),
           h('span', {class: 'visually-hidden', text: ' namespace '}), p.namespace);
}
function teamName(id) { return (me?.memberships.find(m => m.team === id) || {}).teamName || id; }
const isAdmin = role => ADMINS.includes(role);
const isWriter = role => WRITERS.includes(role);
function enabledIn(team) { return (me?.enabledEnvironments.find(e => e.team === team) || {}).environments || []; }
function rows(run) { return run?.summary?.rows !== undefined ? number.format(run.summary.rows) : '—'; }

function notice(message, kind = 'ok') {
  const n = $('#notice'); n.hidden = !message; n.textContent = message || '';
  n.classList.toggle('error', kind === 'error'); n.classList.toggle('ok', kind === 'ok');
}
function failure(error) { if (!error.signin) notice(error.message + (error.detail?.field ? ` (${error.detail.field})` : ''), 'error'); }

// Screens and routes
function show(section) { for (const id of ['loading', 'signin', 'page']) $(`#${id}`).hidden = id !== section; }
function showSignin() {
  show('signin'); $('#identity').hidden = true;
  $('#signin-link').href = `/auth/login?return_to=${encodeURIComponent('/' + location.hash)}`;
}
function parseRoute() {
  const [path, query = ''] = location.hash.replace(/^#/, '').split('?');
  const parts = path.split('/').filter(Boolean), params = new URLSearchParams(query);
  if (parts[0] === 'new') return {page: 'new', template: params.get('template'), type: params.get('type')};
  if (parts[0] === 'pipelines' && parts[1]) return {page: parts[2] === 'edit' ? 'edit' : 'pipeline', id: parts[1]};
  if (parts[0] === 'runs' && parts[1]) return {page: 'run', id: parts[1]};
  return {page: 'list', team: params.get('team') || '', environment: ENVIRONMENTS.includes(params.get('environment')) ? params.get('environment') : ''};
}
function crumbs(...items) {
  fill($('#crumbs'), h('a', {href: '#/', text: 'Pipelines'}),
       items.flatMap(([text, href]) => [' / ', href ? h('a', {href, text}) : h('span', {text, 'aria-current': 'page'})]));
}
function heading(eyebrow, title, text, ...actions) {
  return h('section', {class: 'page-heading'},
    h('div', {}, h('p', {class: 'eyebrow', text: eyebrow}), title, text && h('p', {text})),
    actions.length ? h('div', {class: 'actions'}, actions) : null);
}
function schedulePoll(fn, ms) { clearTimeout(poller); poller = setTimeout(fn, ms); }

async function render(keep = false) {
  clearTimeout(poller);
  route = parseRoute();
  const token = ++renderToken;
  try {
    if (!me) {
      const session = await (await fetch('/api/session', {credentials: 'same-origin'})).json();
      if (!session.authenticated) return showSignin();
      me = await api('/me');
      $('#identity').hidden = false; $('#username').textContent = me.user.name;
      if (me.userPortalUrl) { $('#portal-link').href = me.userPortalUrl; $('#portal-link').hidden = false; }
    }
    show('page');
    if (flash) { notice(...flash); flash = null; } else notice('');
    const pages = {list: renderList, new: renderEditor, edit: renderEditor, pipeline: renderPipeline, run: renderRun};
    await pages[route.page](token);
    if (token === renderToken && !keep) window.scrollTo(0, 0);
  } catch (error) {
    if (error.signin) return;
    if (!me) { show('loading'); $('#loading p').textContent = error.message; return; }
    show('page'); failure(error);
  }
}
function go(hash, message) { if (message) flash = message; if (location.hash === hash) render(true); else location.hash = hash; }

// Pipelines
async function renderList() {
  crumbs();
  const query = new URLSearchParams();
  if (route.team) query.set('team', route.team);
  if (route.environment) query.set('environment', route.environment);
  const pipelines = await api(`/pipelines?${query}`);
  const adminTeams = me.memberships.filter(m => isAdmin(m.role));
  const setFilter = changes => {
    const next = new URLSearchParams({team: route.team, environment: route.environment, ...changes});
    for (const [k, v] of [...next]) if (!v) next.delete(k);
    location.hash = `#/?${next}`;
  };
  const filters = h('div', {class: 'filters'},
    me.memberships.length > 1 && h('label', {class: 'field-inline'}, h('span', {class: 'visually-hidden', text: 'Team'}),
      h('select', {class: 'pill', onchange: e => setFilter({team: e.target.value})},
        h('option', {value: '', text: 'All teams'}),
        me.memberships.map(m => h('option', {value: m.team, text: m.teamName, selected: m.team === route.team})))),
    h('div', {class: 'segmented', role: 'group', 'aria-label': 'Environment'},
      [['', 'All'], ...ENVIRONMENTS.map(e => [e, LABELS[e]])].map(([value, label]) =>
        h('button', {type: 'button', 'aria-pressed': String(route.environment === value), text: label, onclick: () => setFilter({environment: value})}))));
  const body = pipelines.length ? pipelineTable(pipelines) : emptyState(adminTeams.length > 0);
  fill($('#content'),
    heading('INGESTION', h('h1', {}, 'Your pipelines', h('span', {class: 'dot-accent', text: '.'})),
      'Declarative dlt pipelines that load REST APIs, databases and files into your teams\' Iceberg databases, as each team\'s service account.',
      adminTeams.length ? h('a', {class: 'button', href: '#/new', text: 'New pipeline'}) : null),
    filters, body, environmentsCard(), agentCard());
  if (pipelines.some(p => ACTIVE.has(p.lastRun?.status))) schedulePoll(() => render(true), 4000);
}

function pipelineTable(pipelines) {
  return h('div', {class: 'card table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, ['Pipeline', 'Source', 'Loads into', 'Schedule', 'Last run'].map(t => h('th', {scope: 'col', text: t})))),
    h('tbody', {}, pipelines.map(p => h('tr', {},
      h('td', {class: 'name-cell wide'}, h('a', {href: `#/pipelines/${p.id}`, text: p.name}),
        h('p', {text: `${teamName(p.team)} · ${LABELS[p.environment]}`}),
        p.secretsMissing.length ? h('p', {class: 'error-text', text: `Needs secret ${p.secretsMissing.join(', ')}`}) : null),
      h('td', {}, typeChip(p.sourceType)),
      h('td', {}, target(p)),
      h('td', {class: 'mono'}, p.schedule ? h('span', {title: `Next run ${new Date(p.schedule.nextRunAt * 1000).toLocaleString()}`},
        p.schedule.enabled ? p.schedule.cron : `${p.schedule.cron} (paused)`) : h('span', {class: 'muted', text: '—'})),
      h('td', {class: 'wide'}, p.lastRun ? h('div', {class: 'run-cell'},
        h('a', {href: `#/runs/${p.lastRun.id}`, class: 'plain-link'}, chip(p.lastRun.status)),
        h('span', {class: 'small-print'}, `${rows(p.lastRun)} rows · `, time(p.lastRun.createdAt)))
        : h('span', {class: 'muted small', text: 'Never run'})))))));
}

function emptyState(admin) {
  return h('section', {class: 'card empty-state'},
    h('p', {class: 'eyebrow', text: 'NO PIPELINES YET'}),
    h('h2', {text: 'Bring data in, without code.'}),
    h('p', {text: 'A pipeline names a source — a REST API, a PostgreSQL or MySQL table, or a CSV, JSONL or Parquet file — and the database and namespace it loads into. Runs happen in an isolated container as the team\'s service account, now or on a schedule. Loaded tables show "Produced by dlt" in the catalog.'}),
    h('ol', {class: 'how'},
      h('li', {}, h('span', {text: '[ 01 ]'}), 'Start from a template'),
      h('li', {}, h('span', {text: '[ 02 ]'}), 'Set the source\'s secrets'),
      h('li', {}, h('span', {text: '[ 03 ]'}), 'Run it now or on a schedule')),
    admin ? h('div', {class: 'actions'}, h('a', {class: 'button', href: '#/new', text: 'Start from a template'}))
      : h('p', {class: 'small-print', text: 'A team administrator creates pipelines. You can follow their runs here.'}));
}

function environmentsCard() {
  const items = me.memberships.map(m => {
    const on = enabledIn(m.team);
    return h('div', {class: 'env-row'}, h('span', {class: 'team', text: m.teamName}),
      ENVIRONMENTS.map(env => h('span', {class: `env ${on.includes(env) ? 'on' : 'off'}`},
        h('i', {class: `status ${on.includes(env) ? 'success' : ''}`, 'aria-hidden': 'true'}),
        `${LABELS[env]}${on.includes(env) ? '' : ' · off'}`,
        !on.includes(env) && isAdmin(m.role) ? h('button', {type: 'button', class: 'quiet', text: 'Enable',
          'aria-label': `Enable Ingestion for ${m.teamName} in ${LABELS[env]}`, onclick: () => enableEnvironment(m, env)}) : null)));
  });
  return h('section', {class: 'card agent-hint'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Where pipelines can run'})),
    h('p', {class: 'small', text: 'Runs only happen in environments a team administrator enabled. Each runs as that team\'s service account for the environment.'}),
    h('div', {class: 'env-list'}, items));
}

async function enableEnvironment(membership, env) {
  const ok = await confirmDialog(`Enable Ingestion in ${LABELS[env]}?`,
    `Pipelines of ${membership.teamName} in ${LABELS[env]} will load data as the team's service account. The platform creates that account now; a team administrator can disable it again.`,
    'Enable');
  if (!ok) return;
  try {
    await send(`/teams/${membership.team}/environments/${env}`, 'POST');
    me = await api('/me');
    go(location.hash, [`Ingestion is enabled for ${membership.teamName} in ${LABELS[env]}.`]);
  } catch (error) { failure(error); }
}

function agentCard() {
  return h('section', {class: 'card agent-hint'},
    h('h2', {text: 'With an AI agent'}),
    h('p', {class: 'small', text: 'Every action here is also an MCP tool and a REST operation, with your permissions. Connect your agent and sign in through Keycloak.'}),
    h('pre', {text: `claude mcp add --transport http --client-id ext-ingestion-mcp --callback-port 3010 iceberg-ingestion ${location.origin}/mcp`}),
    h('p', {class: 'small-print'}, 'API reference: ', h('a', {href: '/api/v1/openapi.json', text: '/api/v1/openapi.json'}), ' · MCP: ', h('code', {text: '/mcp'})));
}

// New and edit
function resourceNames(type, source) {
  try {
    if (type === 'rest_api') return (source.resources || []).map(r => r?.name).filter(n => typeof n === 'string' && n);
    if (type === 'sql_database') return (source.tables || []).map(t => typeof t === 'string' ? t : t?.name).filter(Boolean);
    if (type === 'filesystem') return source.table ? [source.table] : [];
  } catch { /* shown as a parse problem */ }
  return [];
}
const list = text => text.split(',').map(s => s.trim()).filter(Boolean);

async function renderEditor(token) {
  const editing = route.page === 'edit';
  const existing = editing ? await api(`/pipelines/${route.id}`) : null;
  templates = templates || await api('/templates');
  if (token !== renderToken) return;
  const adminTeams = me.memberships.filter(m => isAdmin(m.role));
  if (!editing && !adminTeams.length) {
    crumbs(['New pipeline']);
    return fill($('#content'), heading('NEW PIPELINE', h('h1', {class: 'compact', text: 'Only team administrators create pipelines.'}),
      'Ask an administrator of your team, or follow existing pipelines.', h('a', {class: 'button quiet', href: '#/', text: 'Back to pipelines'})));
  }
  const template = !editing && route.template ? templates.find(t => t.id === route.template) : null;
  const blankType = !editing && !template && BLANKS[route.type] ? route.type : null;
  const chosen = editing || template || blankType;
  crumbs(...(editing ? [[`${teamName(existing.team)} · ${existing.name}`, `#/pipelines/${existing.id}`], ['Edit']] : [['New pipeline']]));

  const picker = !editing && h('section', {},
    h('div', {class: 'section-title'}, h('h2', {text: chosen ? 'Start from' : 'Choose a starting point'})),
    h('div', {class: 'templates', role: 'group', 'aria-label': 'Templates'},
      templates.map(t => h('button', {type: 'button', class: 'template', 'aria-pressed': String(template?.id === t.id),
        onclick: () => { location.hash = `#/new?template=${t.id}`; }},
        typeChip(t.sourceType), h('h3', {text: t.title}), h('p', {text: t.description}))),
      Object.keys(BLANKS).map(type => h('button', {type: 'button', class: 'template blank', 'aria-pressed': String(blankType === type),
        onclick: () => { location.hash = `#/new?type=${type}`; }},
        typeChip(type), h('h3', {text: `Blank ${SOURCE_LABELS[type]}`}), h('p', {text: 'Write the definition yourself.'})))));
  const head = heading(editing ? 'EDIT PIPELINE' : 'NEW PIPELINE',
    h('h1', {class: 'compact', text: editing ? existing.name : 'Bring data in'}),
    editing ? 'Changes apply from the next run. A new host removes the secrets; a new database or namespace starts loading from scratch.'
      : 'Pick a template, adapt the definition and check it. Secrets are set after the pipeline exists.');
  if (!chosen) return fill($('#content'), head, picker);

  const type = editing ? existing.sourceType : template ? template.sourceType : blankType;
  const start = editing ? existing : {
    team: route.team || adminTeams[0].team, environment: 'development', name: '', description: '',
    namespace: template?.namespace || 'raw_data', source: template?.source || BLANKS[type], resources: template?.resources || {}};
  const hints = structuredClone(start.resources || {});
  const state = {type, team: editing ? existing.team : (adminTeams.find(m => m.team === start.team) || adminTeams[0]).team,
                 environment: start.environment, source: start.source};

  const teamSelect = h('select', {id: 'f-team', disabled: editing, onchange: e => { state.team = e.target.value; refreshDatabases(); }},
    (editing ? me.memberships.filter(m => m.team === existing.team) : adminTeams).map(m => h('option', {value: m.team, text: m.teamName, selected: m.team === state.team})));
  const envSelect = h('select', {id: 'f-env', disabled: editing, onchange: e => { state.environment = e.target.value; refreshDatabases(); }},
    ENVIRONMENTS.map(env => h('option', {value: env, text: LABELS[env], selected: env === state.environment})));
  const dbSelect = h('select', {id: 'f-db'});
  const envHelp = h('span', {class: 'help'});
  function refreshDatabases() {
    const dbs = me.databases.filter(d => d.team === state.team && d.environment === state.environment);
    const current = dbSelect.value || (editing ? existing.database : '');
    fill(dbSelect, dbs.length ? dbs.map(d => h('option', {value: d.id, text: d.name, selected: d.id === current})) : h('option', {value: '', text: 'No databases in this environment'}));
    dbSelect.disabled = !dbs.length;
    envHelp.textContent = enabledIn(state.team).includes(state.environment) ? '' : 'Ingestion is not enabled here yet; enable it before the first run.';
  }
  const name = h('input', {id: 'f-name', value: start.name, maxlength: 64, required: true, autocomplete: 'off', placeholder: 'crm customers'});
  const description = h('input', {id: 'f-desc', value: start.description, maxlength: 500, autocomplete: 'off', placeholder: 'What it loads and why'});
  const namespace = h('input', {id: 'f-ns', value: start.namespace, maxlength: 64, autocomplete: 'off', pattern: '[a-z_][a-z0-9_]*'});
  const editor = h('textarea', {id: 'f-source', class: 'editor', spellcheck: 'false', autocapitalize: 'off', 'aria-describedby': 'source-status'});
  editor.value = JSON.stringify(start.source, null, 2);
  const sourceStatus = h('span', {id: 'source-status', class: 'help', 'aria-live': 'polite'});
  const sourceField = h('label', {class: 'field'}, h('span', {text: `Source definition · ${SOURCE_LABELS[type]}`}), editor, sourceStatus);
  const hintsBody = h('tbody');
  const result = h('div', {class: 'card result', 'aria-live': 'polite'});

  function parse() {
    try {
      const value = JSON.parse(editor.value);
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The definition must be a JSON object.');
      sourceField.classList.remove('invalid'); sourceStatus.className = 'help';
      sourceStatus.textContent = 'Refer to secrets as "$secret:<name>"; never put their values here.';
      state.source = value; return value;
    } catch (error) {
      sourceField.classList.add('invalid'); sourceStatus.className = 'error-text';
      sourceStatus.textContent = `Not valid JSON: ${error.message}`; return null;
    }
  }
  function renderHints() {
    const names = resourceNames(type, state.source || {});
    fill(hintsBody, names.length ? names.map(n => {
      const hint = hints[n] = hints[n] || {writeDisposition: 'append', primaryKey: [], partition: []};
      return h('tr', {},
        h('td', {text: n}),
        h('td', {}, h('select', {'aria-label': `How ${n} is written`, onchange: e => { hint.writeDisposition = e.target.value; }},
          Object.entries(DISPOSITIONS).map(([v, label]) => h('option', {value: v, text: label, selected: (hint.writeDisposition || 'append') === v})))),
        h('td', {}, h('input', {class: 'inline', 'aria-label': `Primary key of ${n}`, value: (hint.primaryKey || []).join(', '), placeholder: 'id',
          oninput: e => { hint.primaryKey = list(e.target.value); }})),
        h('td', {}, h('input', {class: 'inline', 'aria-label': `Partition columns of ${n}`, value: (hint.partition || []).join(', '), placeholder: '—',
          oninput: e => { hint.partition = list(e.target.value); }})));
    }) : h('tr', {}, h('td', {colspan: 4, class: 'muted', text: 'The definition names no endpoints, tables or file yet.'})));
  }
  let timer = null;
  editor.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(() => { if (parse()) renderHints(); }, 250); });
  editor.addEventListener('keydown', e => {
    if (e.key !== 'Tab' || e.shiftKey) return;
    e.preventDefault(); editor.setRangeText('  ', editor.selectionStart, editor.selectionEnd, 'end');
  });
  function hintsFor(source) {
    const names = resourceNames(type, source);
    return Object.fromEntries(names.map(n => [n, hints[n] || {writeDisposition: 'append'}]));
  }
  function body() {
    const source = parse();
    if (!source) return null;
    return {source, resources: hintsFor(source), name: name.value.trim(), description: description.value.trim(),
            namespace: namespace.value.trim(), database: dbSelect.value};
  }
  async function check() {
    const data = body(); if (!data) return null;
    try {
      const checked = await send('/pipelines/validate', 'POST', {sourceType: type, source: data.source, resources: data.resources,
        team: state.team, environment: state.environment, database: data.database || undefined, namespace: data.namespace || undefined});
      fill(result, h('div', {class: `notice ${checked.valid ? 'ok' : 'error'}`},
        checked.valid ? 'The definition is valid.' : h('ul', {class: 'plain'}, checked.problems.map(p => h('li', {text: p})))),
        h('dl', {class: 'facts'},
          h('dt', {text: 'Loads'}), h('dd', {text: checked.resourceNames.join(', ')}),
          h('dt', {text: 'Secrets'}), h('dd', {text: checked.secretsNeeded.length ? checked.secretsNeeded.join(', ') + ' (set after saving)' : 'none'})),
        h('p', {class: 'small-print', text: checked.note}));
      return checked.valid;
    } catch (error) {
      fill(result, h('div', {class: 'notice error', text: error.message + (error.detail?.field ? ` (${error.detail.field})` : '')}));
      if (error.detail?.field?.startsWith('source')) editor.focus();
      return false;
    }
  }
  async function save(event) {
    event.preventDefault();
    const data = body(); if (!data) return editor.focus();
    if (!data.name) { notice('Give the pipeline a name.', 'error'); return name.focus(); }
    const button = event.submitter; if (button) button.disabled = true;
    try {
      if (editing) {
        const changed = await send(`/pipelines/${existing.id}`, 'PATCH', {...data, expected_revision: existing.revision});
        go(`#/pipelines/${existing.id}`, [changed.hint || 'Saved. The next run uses the new definition.']);
      } else {
        const created = await send('/pipelines', 'POST', {...data, team: state.team, environment: state.environment, sourceType: type});
        go(`#/pipelines/${created.id}`, [created.secretsMissing.length
          ? `Created. Set ${created.secretsMissing.join(', ')} below before the first run.` : 'Created. Run it when you are ready.']);
      }
    } catch (error) {
      failure(error);
      fill(result, h('div', {class: 'notice error', text: error.message}));
    } finally { if (button) button.disabled = false; }
  }

  const form = h('form', {class: 'stack', onsubmit: save, novalidate: true},
    h('section', {class: 'card'},
      h('h2', {text: 'Where it loads'}),
      h('div', {class: 'form-grid'},
        h('label', {class: 'field'}, h('span', {text: 'Name'}), name),
        h('label', {class: 'field'}, h('span', {text: 'Team'}), teamSelect),
        h('label', {class: 'field'}, h('span', {text: 'Environment'}), envSelect, envHelp),
        h('label', {class: 'field'}, h('span', {text: 'Database'}), dbSelect),
        h('label', {class: 'field'}, h('span', {text: 'Namespace'}), namespace, h('span', {class: 'help', text: 'Lowercase, such as raw_crm.'}))),
      h('label', {class: 'field'}, h('span', {text: 'Description'}), description)),
    h('section', {class: 'card'},
      template && h('p', {class: 'small', text: template.description}),
      sourceField),
    h('section', {class: 'card'},
      h('h2', {text: 'How each resource is written'}),
      h('p', {class: 'small', text: 'Replace keeps the table and its identity and replaces its rows. Merge updates rows by their primary key and inserts new ones.'}),
      h('div', {class: 'table-wrap'}, h('table', {class: 'hints'},
        h('thead', {}, h('tr', {}, ['Resource', 'Write', 'Primary key', 'Partition by'].map(t => h('th', {scope: 'col', text: t})))), hintsBody))),
    result,
    h('div', {class: 'actions'},
      h('button', {type: 'button', class: 'quiet', text: 'Check', onclick: check}),
      h('button', {type: 'submit', text: editing ? 'Save changes' : 'Create pipeline'}),
      h('a', {class: 'button quiet', href: editing ? `#/pipelines/${existing.id}` : '#/', text: 'Cancel'})));
  refreshDatabases(); parse(); renderHints();
  fill($('#content'), head, picker, form);
}

// One pipeline
async function renderPipeline(token) {
  const p = await api(`/pipelines/${route.id}`);
  if (token !== renderToken) return;
  crumbs([`${teamName(p.team)} · ${p.name}`]);
  const writer = isWriter(p.role), admin = isAdmin(p.role);
  const active = p.recentRuns.find(r => ACTIVE.has(r.status));
  const enabled = enabledIn(p.team).includes(p.environment);
  const fullRefresh = h('input', {type: 'checkbox', id: 'full-refresh'});
  const blocked = p.secretsMissing.length ? `Set ${p.secretsMissing.join(', ')} first.` : !enabled ? 'Ingestion is not enabled in this environment.' : active ? 'A run is in progress.' : '';
  const runButton = writer && h('button', {type: 'button', text: active ? 'Running…' : 'Run now', disabled: Boolean(blocked), title: blocked || null,
    onclick: async event => {
      event.target.disabled = true;
      try {
        const run = await send(`/pipelines/${p.id}/runs`, 'POST', {full_refresh: fullRefresh.checked});
        go(location.hash, [`Run started${run.fullRefresh ? ' with a full refresh' : ''}.`]);
      } catch (error) { failure(error); event.target.disabled = false; }
    }});
  const status = p.lastRun ? chip(p.lastRun.status, `Last run ${p.lastRun.status}`) : chip('idle', 'Never run');
  fill($('#content'),
    heading(`${teamName(p.team)} · ${LABELS[p.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: p.name}), p.description,
      runButton, writer && h('label', {class: 'check', title: 'Ignore incremental state and replace the tables\' rows'}, fullRefresh, 'Full refresh'),
      admin && h('a', {class: 'button quiet', href: `#/pipelines/${p.id}/edit`, text: 'Edit'}),
      admin && h('button', {type: 'button', class: 'danger', text: 'Delete', onclick: () => deletePipeline(p)})),
    h('div', {class: 'status-line spaced'}, status, typeChip(p.sourceType), h('span', {class: 'chip'}, target(p)),
      p.schedule && h('span', {class: 'chip', text: p.schedule.enabled ? `Every ${p.schedule.cron} UTC` : 'Schedule paused'}),
      blocked && !active ? h('span', {class: 'small-print', text: blocked}) : null),
    h('div', {class: 'columns'},
      h('div', {class: 'stack'}, runsCard(p), definitionCard(p)),
      h('div', {class: 'stack'}, secretsCard(p), scheduleCard(p), detailsCard(p))));
  if (active) schedulePoll(() => render(true), 3000);
}

function runsTable(runs) {
  if (!runs.length) return h('p', {class: 'muted small', text: 'No runs yet.'});
  return h('div', {class: 'table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, h('th', {scope: 'col', text: 'Status'}), h('th', {scope: 'col', text: 'Started'}),
      h('th', {scope: 'col', text: 'Duration'}), h('th', {scope: 'col', class: 'num', text: 'Rows'}), h('th', {scope: 'col', text: 'By'}))),
    h('tbody', {}, runs.map(r => h('tr', {class: 'run-row', onclick: event => { if (!event.target.closest('a')) location.hash = `#/runs/${r.id}`; }},
      h('td', {}, h('a', {href: `#/runs/${r.id}`, 'aria-label': `Run ${r.status}, ${when(r.createdAt)}`}, chip(r.status))),
      h('td', {class: 'nowrap'}, time(r.startedAt || r.createdAt)),
      h('td', {class: 'nowrap mono', text: duration(r)}),
      h('td', {class: 'num', text: rows(r)}),
      h('td', {class: 'wide small'}, r.schedule ? 'schedule' : r.triggeredBy, r.agent && h('span', {class: 'agent', text: 'agent'}),
        r.fullRefresh && h('span', {class: 'agent', text: 'full refresh'})))))));
}

function runsCard(p) {
  return h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Runs'}),
    h('span', {class: 'small-print', text: 'Newest first'})), runsTable(p.recentRuns));
}

function definitionCard(p) {
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Definition'}), h('span', {class: 'small-print', text: `Revision ${p.revision}`})),
    h('pre', {class: 'code', text: JSON.stringify(p.source, null, 2)}),
    h('div', {class: 'table-wrap'}, h('table', {},
      h('thead', {}, h('tr', {}, ['Resource', 'Write', 'Primary key', 'Partition by'].map(t => h('th', {scope: 'col', text: t})))),
      h('tbody', {}, Object.entries(p.resources).map(([name, hint]) => h('tr', {},
        h('td', {class: 'mono', text: name}), h('td', {text: DISPOSITIONS[hint.writeDisposition] || hint.writeDisposition}),
        h('td', {class: 'mono', text: hint.primaryKey.join(', ') || '—'}), h('td', {class: 'mono', text: hint.partition.join(', ') || '—'})))))),
    h('p', {class: 'small-print', text: p.note}));
}

function secretsCard(p) {
  const admin = isAdmin(p.role);
  const names = [...new Set([...p.secretsSet, ...p.secretsMissing])].sort();
  const rowsFor = names.map(name => {
    const set = p.secretsSet.includes(name);
    const input = h('input', {type: 'password', class: 'inline', autocomplete: 'new-password', 'aria-label': `New value for ${name}`,
      placeholder: set ? 'Replace value' : 'Value', required: true});
    return h('div', {class: 'secret-row'},
      h('div', {class: 'head'}, h('span', {text: name}), chip(set ? 'set' : 'missing', set ? 'set' : 'missing')),
      admin && h('form', {onsubmit: async event => {
        event.preventDefault();
        if (!input.value) return input.focus();
        try {
          await send(`/pipelines/${p.id}/secrets`, 'PUT', {secrets: {[name]: input.value}});
          input.value = ''; go(location.hash, [`${name} is ${set ? 'replaced' : 'set'}.`]);
        } catch (error) { failure(error); }
      }}, input, h('button', {type: 'submit', class: 'small', text: set ? 'Replace' : 'Set'}),
        set && h('button', {type: 'button', class: 'quiet small', text: 'Remove', onclick: async () => {
          if (!await confirmDialog(`Remove ${name}?`, 'Runs fail until it is set again.', 'Remove', true)) return;
          try { await send(`/pipelines/${p.id}/secrets`, 'PUT', {secrets: {[name]: null}}); go(location.hash, [`${name} is removed.`]); }
          catch (error) { failure(error); }
        }})));
  });
  return h('section', {class: 'card'}, h('h2', {text: 'Secrets'}),
    names.length ? rowsFor : h('p', {class: 'small', text: 'This pipeline needs no secrets.'}),
    h('p', {class: 'small-print', text: 'Stored encrypted and write-only: nobody can read a secret back, and runs mask them in logs. Changing where the pipeline connects removes them.'}),
    !admin && names.length ? h('p', {class: 'small-print', text: 'A team administrator sets secrets.'}) : null);
}

function scheduleCard(p) {
  const admin = isAdmin(p.role), s = p.schedule;
  const cron = h('input', {class: 'inline', value: s?.cron || '0 5 * * *', 'aria-label': 'Cron expression in UTC', autocomplete: 'off', spellcheck: 'false'});
  const enabled = h('input', {type: 'checkbox', checked: s ? s.enabled : true});
  return h('section', {class: 'card'}, h('h2', {text: 'Schedule'}),
    s ? h('dl', {class: 'facts'},
      h('dt', {text: 'Cron (UTC)'}), h('dd', {text: s.cron + (s.enabled ? '' : ' · paused')}),
      h('dt', {text: 'Next run'}), h('dd', {}, s.enabled ? time(s.nextRunAt) : '—'),
      h('dt', {text: 'Set by'}), h('dd', {text: s.createdBy}),
      s.lastRun && h('dt', {text: 'Last run'}), s.lastRun && h('dd', {}, h('a', {href: `#/runs/${s.lastRun}`, text: 'Open'})))
      : h('p', {class: 'small', text: 'Not scheduled. Runs start only when someone runs the pipeline.'}),
    admin && h('form', {class: 'stack', onsubmit: async event => {
      event.preventDefault();
      try { await send(`/pipelines/${p.id}/schedule`, 'PUT', {cron: cron.value.trim(), enabled: enabled.checked}); go(location.hash, ['Schedule saved.']); }
      catch (error) { failure(error); }
    }},
      h('div', {class: 'presets', role: 'group', 'aria-label': 'Presets'}, CRON_PRESETS.map(([label, value]) =>
        h('button', {type: 'button', class: 'quiet', text: label, onclick: () => { cron.value = value; cron.focus(); }}))),
      h('div', {class: 'cron-row'}, cron, h('label', {class: 'check'}, enabled, 'Enabled')),
      h('p', {class: 'small-print', text: 'Five fields in UTC, at most every 5 minutes. A due run is skipped while the pipeline is still running.'}),
      h('div', {class: 'actions'}, h('button', {type: 'submit', class: 'small', text: s ? 'Save schedule' : 'Schedule'}),
        s && h('button', {type: 'button', class: 'quiet small', text: 'Remove', onclick: async () => {
          try { await send(`/pipelines/${p.id}/schedule`, 'DELETE'); go(location.hash, ['Schedule removed.']); } catch (error) { failure(error); }
        }}))));
}

function detailsCard(p) {
  return h('section', {class: 'card'}, h('h2', {text: 'Details'}), h('dl', {class: 'facts'},
    h('dt', {text: 'Database'}), h('dd', {text: `${p.databaseName || '—'} (${p.database})`}),
    h('dt', {text: 'Namespace'}), h('dd', {text: p.namespace}),
    h('dt', {text: 'Created'}), h('dd', {}, `${p.createdBy}, `, time(p.createdAt)),
    h('dt', {text: 'Changed'}), h('dd', {}, `${p.updatedBy}, `, time(p.updatedAt)),
    h('dt', {text: 'Your role'}), h('dd', {text: p.role})));
}

async function deletePipeline(p) {
  const input = h('input', {class: 'inline', autocomplete: 'off', 'aria-label': 'Pipeline name'});
  const ok = await confirmDialog(`Delete ${p.name}?`,
    'Its runs, schedule and secrets go. The tables it loaded stay in the database, with their data. Type the pipeline\'s name to confirm.',
    'Delete', true, input, () => input.value === p.name);
  if (!ok) return;
  try { await send(`/pipelines/${p.id}/delete`, 'POST', {confirm_name: input.value}); go('#/', [`${p.name} is deleted. Its tables stay.`]); }
  catch (error) { failure(error); }
}

// One run
async function renderRun(token) {
  const run = await api(`/runs/${route.id}`);
  const [p, logs] = await Promise.all([api(`/pipelines/${run.pipeline}`), api(`/runs/${run.id}/logs?tail=2000`)]);
  if (token !== renderToken) return;
  crumbs([`${teamName(p.team)} · ${p.name}`, `#/pipelines/${p.id}`], ['Run']);
  const active = ACTIVE.has(run.status), summary = run.summary || {};
  const previous = $('#run-log');
  const stick = !previous || previous.scrollTop + previous.clientHeight >= previous.scrollHeight - 24;
  const log = h('pre', {id: 'run-log', class: 'log', tabindex: '0', 'aria-label': 'Run log',
    text: logs.lines.length ? logs.lines.join('\n') : active ? 'Waiting for output…' : 'No log for this run.'});
  const tables = summary.tables || [];
  fill($('#content'),
    heading(`RUN · ${LABELS[run.environment]}`.toUpperCase(), h('h1', {class: 'compact'}, p.name), null,
      active && isWriter(p.role) && h('button', {type: 'button', class: 'danger', text: 'Stop run', onclick: async event => {
        event.target.disabled = true;
        try { await send(`/runs/${run.id}/cancel`, 'POST'); go(location.hash, ['Stopping the run…']); } catch (error) { failure(error); }
      }}),
      h('a', {class: 'button quiet', href: `#/pipelines/${p.id}`, text: 'Back to pipeline'})),
    h('div', {class: 'columns'},
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Log'}),
          h('span', {class: 'small-print', text: active ? 'Updates every 2 seconds' : 'Secrets are masked'})), log),
        h('section', {class: 'card'}, h('h2', {text: 'Tables'}),
          tables.length ? h('div', {class: 'table-wrap'}, h('table', {},
            h('thead', {}, h('tr', {}, h('th', {scope: 'col', text: 'Table'}), h('th', {scope: 'col', class: 'num', text: 'Rows loaded'}))),
            h('tbody', {}, tables.map(t => h('tr', {}, h('td', {class: 'mono', text: `${p.namespace}.${t.name}`}), h('td', {class: 'num', text: number.format(t.rows)}))),
              h('tr', {class: 'total'}, h('td', {text: 'Total'}), h('td', {class: 'num', text: number.format(summary.rows || 0)})))))
            : h('p', {class: 'small muted', text: active ? 'Tables appear when the run finishes.' : 'No tables were loaded.'}),
          summary.published?.errors?.length ? h('div', {class: 'notice error'}, 'Table properties could not be set:',
            h('ul', {class: 'plain'}, summary.published.errors.map(e => h('li', {text: typeof e === 'string' ? e : JSON.stringify(e)})))) : null)),
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'status-line'}, chip(run.status), run.fullRefresh && h('span', {class: 'chip', text: 'full refresh'})),
          run.error && h('p', {class: 'error-text', text: run.error}),
          h('dl', {class: 'facts'},
            h('dt', {text: 'Queued'}), h('dd', {}, time(run.createdAt)),
            h('dt', {text: 'Started'}), h('dd', {}, time(run.startedAt)),
            h('dt', {text: 'Finished'}), h('dd', {}, active ? '—' : time(run.finishedAt)),
            h('dt', {text: 'Duration'}), h('dd', {text: duration(run)}),
            h('dt', {text: 'Started by'}), h('dd', {}, run.schedule ? 'schedule' : run.triggeredBy, run.agent && h('span', {class: 'agent', text: run.agent})),
            h('dt', {text: 'Revision'}), h('dd', {text: String(run.revision)}),
            summary.loadId && h('dt', {text: 'dlt load id'}), summary.loadId && h('dd', {text: summary.loadId}),
            summary.dltVersion && h('dt', {text: 'dlt'}), summary.dltVersion && h('dd', {text: summary.dltVersion}),
            run.exitCode !== null && run.exitCode !== undefined && h('dt', {text: 'Exit code'}),
            run.exitCode !== null && run.exitCode !== undefined && h('dd', {text: String(run.exitCode)}))),
        h('section', {class: 'card'}, h('h2', {text: 'Where the data is'}),
          h('p', {class: 'small'}, 'Tables land in ', target(p), '. They show "Produced by dlt" in the catalog of the user portal.'),
          me.userPortalUrl && h('a', {class: 'button quiet small', href: me.userPortalUrl, target: '_blank', rel: 'noopener', text: 'Open the catalog ↗'})))));
  if (stick) log.scrollTop = log.scrollHeight; else log.scrollTop = previous.scrollTop;
  if (active) schedulePoll(() => render(true), 2000);
}

// Dialog
function confirmDialog(title, text, action, danger = false, extra = null, valid = null) {
  const dialog = $('#dialog');
  return new Promise(resolve => {
    const confirm = h('button', {type: 'submit', class: danger ? 'danger' : '', text: action, disabled: valid ? !valid() : false});
    if (extra && valid) extra.addEventListener('input', () => { confirm.disabled = !valid(); });
    fill(dialog, h('form', {method: 'dialog', class: 'stack', onsubmit: event => { event.preventDefault(); dialog.close('ok'); }},
      h('h2', {id: 'dialog-title', text: title}), h('p', {text}), extra,
      h('div', {class: 'actions'}, confirm, h('button', {type: 'button', class: 'quiet', text: 'Cancel', onclick: () => dialog.close('cancel')}))));
    dialog.onclose = () => resolve(dialog.returnValue === 'ok');
    dialog.returnValue = '';
    dialog.showModal();
    (extra || confirm).focus();
  });
}

function theme() {
  const saved = (() => { try { return localStorage.getItem('iceberg-theme'); } catch { return null; } })();
  const apply = mode => { document.documentElement.dataset.theme = mode;
    document.querySelector('meta[name="theme-color"]').setAttribute('content', mode === 'light' ? '#f5f5f5' : '#000000');
    $('#theme').textContent = mode === 'light' ? 'Dark / ●' : 'Light / ○';
    $('#theme').setAttribute('aria-label', `Switch to ${mode === 'light' ? 'dark' : 'light'} mode`); };
  apply(saved === 'light' ? 'light' : 'dark');
  $('#theme').onclick = () => { const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'; apply(next); try { localStorage.setItem('iceberg-theme', next); } catch { /* private mode */ } };
}
$('#logout').onclick = async () => {
  const result = await (await fetch('/auth/logout', {method: 'POST', headers: {'X-Iceberg-Ingestion': '1'}, credentials: 'same-origin'})).json();
  location.href = result.logoutUrl || '/';
};
window.addEventListener('hashchange', () => render());
theme();
render();
