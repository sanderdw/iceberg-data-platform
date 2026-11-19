'use strict';
// The Replication page over the REST API: replications of your teams, their secrets, schedules and runs.
// Agents do the same through the MCP server; the server checks every permission again.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const SOURCE_LABELS = {postgres: 'PostgreSQL'};
// Shown so people see what comes; only PostgreSQL is available in this release.
const LATER = ['MySQL', 'MongoDB', 'SQL Server', 'Oracle'];
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
const ACTIVE = new Set(['queued', 'running']);
const MODES = {cdc: 'Changes (CDC)', incremental: 'Incremental', full_refresh: 'Full copy'};
const DELETE_MODES = {
  pos: ['Positional deletes', 'Iceberg v2. Read correctly by DuckDB, PyIceberg and Polars in notebooks, Conversational BI and data shares.'],
  dv: ['Deletion vectors', 'Iceberg v3. Smaller delete files; every platform engine reads them, external readers need Iceberg v3 support.'],
};
const TLS = ['verify-full', 'verify-ca', 'require', 'disable'];
const CRON_PRESETS = [['Every 5 min', '*/5 * * * *'], ['Every 15 min', '*/15 * * * *'], ['Hourly', '0 * * * *'], ['Daily 02:00', '0 2 * * *']];
const BLANK = {host: 'db.example.com', port: 5432, database: 'app', username: 'olake_reader', password: '$secret:password',
               tls: 'verify-full', slot: 'iceberg_app', publication: 'iceberg_app'};
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
  for (const child of children.flat(Infinity)) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}
// replaceChildren would print "null" for skipped optional parts.
function fill(parent, ...children) { parent.replaceChildren(...children.flat(Infinity).filter(c => c !== null && c !== undefined && c !== false)); }

async function api(path, options = {}) {
  const write = options.method && options.method !== 'GET';
  const response = await fetch(`/api/v1${path}`, {
    ...options, credentials: 'same-origin',
    headers: {...(options.body ? {'Content-Type': 'application/json'} : {}), ...(write ? {'X-Iceberg-Replication': '1'} : {})},
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
const STATUS_LABELS = {partial: 'continues next run'};
function target(p) {
  return h('span', {class: 'target'}, p.databaseName || p.database, h('span', {class: 'sep', 'aria-hidden': 'true', text: '›'}),
           h('span', {class: 'visually-hidden', text: ' namespace '}), p.namespace);
}
function teamName(id) { return (me?.memberships.find(m => m.team === id) || {}).teamName || id; }
const isAdmin = role => ADMINS.includes(role);
const isWriter = role => WRITERS.includes(role);
function enabledIn(team) { return (me?.enabledEnvironments.find(e => e.team === team) || {}).environments || []; }
function written(run) { return run?.summary?.recordsWritten !== undefined ? number.format(run.summary.recordsWritten) : '—'; }
function source(p) { return `${p.source.host}:${p.source.port}/${p.source.database}`; }

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
  if (parts[0] === 'new') return {page: 'new', template: params.get('template')};
  if (parts[0] === 'replications' && parts[1]) return {page: parts[2] === 'edit' ? 'edit' : 'replication', id: parts[1]};
  if (parts[0] === 'runs' && parts[1]) return {page: 'run', id: parts[1]};
  return {page: 'list', team: params.get('team') || '', environment: ENVIRONMENTS.includes(params.get('environment')) ? params.get('environment') : ''};
}
function crumbs(...items) {
  fill($('#crumbs'), h('a', {href: '#/', text: 'Replications'}),
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
    const pages = {list: renderList, new: renderEditor, edit: renderEditor, replication: renderReplication, run: renderRun};
    await pages[route.page](token);
    if (token === renderToken && !keep) window.scrollTo(0, 0);
  } catch (error) {
    if (error.signin) return;
    if (!me) { show('loading'); $('#loading p').textContent = error.message; return; }
    show('page'); failure(error);
  }
}
function go(hash, message) { if (message) flash = message; if (location.hash === hash) render(true); else location.hash = hash; }

// Replications
async function renderList() {
  crumbs();
  const query = new URLSearchParams();
  if (route.team) query.set('team', route.team);
  if (route.environment) query.set('environment', route.environment);
  const replications = await api(`/replications?${query}`);
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
  const body = replications.length ? replicationTable(replications) : emptyState(adminTeams.length > 0);
  fill($('#content'),
    heading('REPLICATION', h('h1', {}, 'Replication', h('span', {class: 'dot-accent', text: '.'})),
      'Keep operational databases in step with your teams\' Iceberg databases. Each run reads the changes since the last one, writes them as the team\'s service account and stops. For APIs, files and one-time loads, use Ingestion (dlt).',
      adminTeams.length ? h('a', {class: 'button', href: '#/new', text: 'New replication'}) : null),
    filters, body, h('div', {class: 'columns even'}, deletedRowsCard(), agentCard()), environmentsCard());
  if (replications.some(p => ACTIVE.has(p.lastRun?.status))) schedulePoll(() => render(true), 4000);
}

function lastRunCell(run) {
  if (!run) return h('span', {class: 'muted small', text: 'Never run'});
  const detail = run.status === 'partial' ? 'first copy continues next run' : run.error ? run.error
    : `${written(run)} rows written`;
  return h('div', {class: 'run-cell'},
    h('a', {href: `#/runs/${run.id}`, class: 'plain-link'}, chip(run.status)),
    h('span', {class: `small-print ${run.error ? 'error-text' : ''}`}, `${detail} · `, time(run.createdAt)));
}

function replicationTable(replications) {
  return h('div', {class: 'card table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, ['Replication', 'Source', 'Into', 'Read up to', 'Schedule', 'Last run'].map(t => h('th', {scope: 'col', text: t})))),
    h('tbody', {}, replications.map(p => h('tr', {},
      h('td', {class: 'name-cell wide'}, h('a', {href: `#/replications/${p.id}`, text: p.name}),
        h('p', {text: `${teamName(p.team)} · ${LABELS[p.environment]} · ${Object.keys(p.tables).length} tables`}),
        p.secretsMissing.length ? h('p', {class: 'error-text', text: 'Needs its password'})
          : !Object.keys(p.tables).length ? h('p', {class: 'error-text', text: 'Check the source and choose tables'}) : null),
      h('td', {}, typeChip(p.sourceType), h('p', {class: 'small-print mono', text: source(p)})),
      h('td', {}, target(p)),
      h('td', {class: 'mono nowrap', text: p.lastRun?.summary?.readUpTo || '—'}),
      h('td', {class: 'mono nowrap'}, p.schedule ? h('span', {title: `Next run ${new Date(p.schedule.nextRunAt * 1000).toLocaleString()}`},
        p.schedule.enabled ? p.schedule.cron : `${p.schedule.cron} (paused)`) : h('span', {class: 'muted', text: '—'})),
      h('td', {class: 'wide'}, lastRunCell(p.lastRun)))))));
}

function emptyState(admin) {
  return h('section', {class: 'card empty-state'},
    h('p', {class: 'eyebrow', text: 'NO REPLICATIONS YET'}),
    h('h2', {text: 'Keep a database in step, without code.'}),
    h('p', {text: 'A replication connects to an operational database and follows its changes: inserts, updates and deletes from PostgreSQL\'s write-ahead log, or rows whose cursor column grew. Runs happen in an isolated container as the team\'s service account, now or on a schedule.'}),
    h('ol', {class: 'how'},
      h('li', {}, h('span', {text: '[ 01 ]'}), 'Connect a source and set its password'),
      h('li', {}, h('span', {text: '[ 02 ]'}), 'Check the source and choose tables'),
      h('li', {}, h('span', {text: '[ 03 ]'}), 'Run it every few minutes')),
    admin ? h('div', {class: 'actions'}, h('a', {class: 'button', href: '#/new', text: 'New replication'}))
      : h('p', {class: 'small-print', text: 'A team administrator creates replications. You can follow their runs here.'}));
}

function deletedRowsCard() {
  return h('section', {class: 'card agent-hint'},
    h('h2', {text: 'How deleted rows look'}),
    h('p', {class: 'small'}, 'A row deleted in the source stays in the Iceberg table with ', h('code', {text: "_op_type = 'd'"}),
      '. Filter on ', h('code', {text: "_op_type <> 'd'"}), ' for the current rows, in a semantic model, a dbt model or a data share.'),
    h('p', {class: 'small-print', text: 'OLake adds _olake_id, _olake_timestamp, _op_type, _cdc_lsn and _cdc_timestamp to every table.'}));
}

function environmentsCard() {
  const items = me.memberships.map(m => {
    const on = enabledIn(m.team);
    return h('div', {class: 'env-row'}, h('span', {class: 'team', text: m.teamName}),
      ENVIRONMENTS.map(env => h('span', {class: `env ${on.includes(env) ? 'on' : 'off'}`},
        h('i', {class: `status ${on.includes(env) ? 'success' : ''}`, 'aria-hidden': 'true'}),
        `${LABELS[env]}${on.includes(env) ? '' : ' · off'}`,
        !on.includes(env) && isAdmin(m.role) ? h('button', {type: 'button', class: 'quiet', text: 'Enable',
          'aria-label': `Enable Replication for ${m.teamName} in ${LABELS[env]}`, onclick: () => enableEnvironment(m, env)}) : null)));
  });
  return h('section', {class: 'card agent-hint'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Where replications can run'})),
    h('p', {class: 'small', text: 'Runs only happen in environments a team administrator enabled. Each runs as that team\'s service account for the environment.'}),
    h('div', {class: 'env-list'}, items));
}

async function enableEnvironment(membership, env) {
  const ok = await confirmDialog(`Enable Replication in ${LABELS[env]}?`,
    `Replications of ${membership.teamName} in ${LABELS[env]} will write data as the team's service account. The platform creates that account now; a team administrator can disable it again.`,
    'Enable');
  if (!ok) return;
  try {
    await send(`/teams/${membership.team}/environments/${env}`, 'POST');
    me = await api('/me');
    go(location.hash, [`Replication is enabled for ${membership.teamName} in ${LABELS[env]}.`]);
  } catch (error) { failure(error); }
}

function agentCard() {
  return h('section', {class: 'card agent-hint'},
    h('h2', {text: 'Connect an AI agent'}),
    h('p', {class: 'small', text: 'Every action here is also an MCP tool and a REST operation, with your permissions. The replication skill guides an agent from a source check to a first sync.'}),
    h('pre', {text: `claude mcp add --transport http --client-id ext-replication-mcp --callback-port 3010 iceberg-replication ${location.origin}/mcp`}),
    h('p', {class: 'small-print'}, 'API reference: ', h('a', {href: '/api/v1/openapi.json', text: '/api/v1/openapi.json'}), ' · MCP: ', h('code', {text: '/mcp'})));
}

// New and edit: where it connects and where it writes. Tables are chosen after a source check.
async function renderEditor(token) {
  const editing = route.page === 'edit';
  const existing = editing ? await api(`/replications/${route.id}`) : null;
  templates = templates || await api('/templates');
  if (token !== renderToken) return;
  const adminTeams = me.memberships.filter(m => isAdmin(m.role));
  if (!editing && !adminTeams.length) {
    crumbs(['New replication']);
    return fill($('#content'), heading('NEW REPLICATION', h('h1', {class: 'compact', text: 'Only team administrators create replications.'}),
      'Ask an administrator of your team, or follow existing replications.', h('a', {class: 'button quiet', href: '#/', text: 'Back to replications'})));
  }
  crumbs(...(editing ? [[`${teamName(existing.team)} · ${existing.name}`, `#/replications/${existing.id}`], ['Edit']] : [['New replication']]));
  const template = !editing ? templates.find(t => t.id === (route.template || 'postgres-cdc')) : null;
  const start = editing ? existing : {
    team: route.team || adminTeams[0].team, environment: 'development', name: '', description: '',
    namespace: template?.namespace || 'raw_app', source: {...BLANK, ...(template?.source || {})},
    options: template?.options || {deleteMode: 'pos', firstRun: 'copy'}};
  const state = {team: editing ? existing.team : (adminTeams.find(m => m.team === start.team) || adminTeams[0]).team,
                 environment: start.environment};
  const src = start.source;

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
    envHelp.textContent = enabledIn(state.team).includes(state.environment) ? '' : 'Replication is not enabled here yet; enable it before the first run.';
  }
  const input = (id, value, extra = {}) => h('input', {id, value: value ?? '', autocomplete: 'off', spellcheck: 'false', ...extra});
  const f = {
    name: input('f-name', start.name, {maxlength: 64, required: true, placeholder: 'crm-orders'}),
    description: input('f-desc', start.description, {maxlength: 500, placeholder: 'What it keeps in step and why'}),
    namespace: input('f-ns', start.namespace, {maxlength: 64, pattern: '[a-z_][a-z0-9_]*'}),
    host: input('f-host', src.host, {maxlength: 253}),
    port: input('f-port', src.port, {type: 'number', min: 1, max: 65535}),
    database: input('f-database', src.database, {maxlength: 63}),
    username: input('f-user', src.username, {maxlength: 128}),
    tls: h('select', {id: 'f-tls'}, TLS.map(v => h('option', {value: v, text: v, selected: (src.tls || 'verify-full') === v}))),
    slot: input('f-slot', src.slot, {maxlength: 63, placeholder: 'iceberg_app'}),
    publication: input('f-pub', src.publication, {maxlength: 63, placeholder: 'iceberg_app'}),
    firstRun: h('select', {id: 'f-first'},
      h('option', {value: 'copy', text: 'Copy rows, then changes', selected: start.options.firstRun !== 'changes'}),
      h('option', {value: 'changes', text: 'Only new changes', selected: start.options.firstRun === 'changes'})),
  };
  const modes = Object.entries(DELETE_MODES).map(([value, [title, text]]) => h('label', {class: 'mode'},
    h('input', {type: 'radio', name: 'delete-mode', value, checked: (start.options.deleteMode || 'pos') === value}),
    h('span', {}, h('span', {class: 'title'}, h('strong', {text: title}), value === 'pos' && h('span', {class: 'chip success', text: 'Recommended'})), h('small', {text}))));
  modes.push(h('label', {class: 'mode off'}, h('input', {type: 'radio', name: 'delete-mode', disabled: true}),
    h('span', {}, h('span', {class: 'title'}, h('strong', {text: 'Equality deletes'}), h('span', {class: 'chip failed', text: 'Not offered'})),
      h('small', {text: 'PyIceberg and Polars refuse tables with equality deletes, so notebooks and semantic models would fail.'}))));
  const result = h('div', {class: 'result', 'aria-live': 'polite'});
  const field = (label, control, help) => h('label', {class: 'field'}, h('span', {text: label}), control, help && h('span', {class: 'help', text: help}));

  function body() {
    const deleteMode = document.querySelector('input[name="delete-mode"]:checked')?.value || 'pos';
    const sourceValue = {host: f.host.value.trim(), port: Number(f.port.value) || 5432, database: f.database.value.trim(),
      username: f.username.value.trim(), password: '$secret:password', tls: f.tls.value};
    if (f.slot.value.trim()) sourceValue.slot = f.slot.value.trim();
    if (f.publication.value.trim()) sourceValue.publication = f.publication.value.trim();
    return {source: sourceValue, options: {deleteMode, firstRun: f.firstRun.value}, name: f.name.value.trim(),
            description: f.description.value.trim(), namespace: f.namespace.value.trim(), database: dbSelect.value};
  }
  async function save(event) {
    event.preventDefault();
    const data = body();
    if (!data.name) { notice('Give the replication a name.', 'error'); return f.name.focus(); }
    const button = event.submitter; if (button) button.disabled = true;
    try {
      if (editing) {
        const changed = await send(`/replications/${existing.id}`, 'PATCH', {...data, expected_revision: existing.revision});
        go(`#/replications/${existing.id}`, [changed.hint || 'Saved. The next run uses the new settings.']);
      } else {
        const created = await send('/replications', 'POST', {...data, team: state.team, environment: state.environment, sourceType: 'postgres'});
        go(`#/replications/${created.id}`, ['Created. Set the password, then check the source.']);
      }
    } catch (error) {
      failure(error);
      fill(result, h('div', {class: 'notice error', text: error.message + (error.detail?.field ? ` (${error.detail.field})` : '')}));
    } finally { if (button) button.disabled = false; }
  }
  const form = h('form', {class: 'stack', onsubmit: save, novalidate: true},
    h('div', {class: 'columns'},
      h('div', {class: 'stack'},
        h('section', {class: 'card'},
          h('div', {class: 'field'}, h('span', {text: 'Source type'}),
            h('div', {class: 'segmented wrap', role: 'group', 'aria-label': 'Source type'},
              h('button', {type: 'button', 'aria-pressed': 'true', text: 'PostgreSQL'}),
              LATER.map(name => h('button', {type: 'button', disabled: true, title: 'Coming later'}, name, h('span', {class: 'off', text: ' · later'}))))),
          h('div', {class: 'form-grid'},
            field('Host', f.host), field('Port', f.port), field('Database', f.database),
            field('User', f.username), field('Password', h('input', {type: 'password', disabled: true, placeholder: 'Set after saving'}),
              'Write-only. Stored encrypted; never shown again.'),
            field('TLS', f.tls)),
          h('h3', {text: 'Change data capture'}),
          h('div', {class: 'form-grid'}, field('Replication slot', f.slot), field('Publication', f.publication)),
          h('p', {class: 'small-print', text: 'Leave both empty for incremental and full copies only. The slot keeps change log on the source until a run has stored it; delete the slot when you delete the replication.'})),
        h('section', {class: 'card'},
          h('h2', {text: 'Where it writes'}),
          h('div', {class: 'form-grid'},
            field('Name', f.name), field('Team', teamSelect), h('label', {class: 'field'}, h('span', {text: 'Environment'}), envSelect, envHelp),
            field('Database', dbSelect), field('Namespace', f.namespace, 'Lowercase, such as raw_crm.'), field('First run', f.firstRun)),
          field('Description', f.description))),
      h('div', {class: 'stack'},
        h('fieldset', {class: 'card modes'}, h('legend', {text: 'How updates and deletes are written'}), modes),
        template && h('section', {class: 'card'}, h('h2', {text: 'Set up the source'}),
          h('p', {class: 'small', text: 'The database owner runs this once. The replication page shows it again for the tables you choose.'}),
          h('pre', {class: 'code', text: template.setupSql})))),
    result,
    h('div', {class: 'actions'},
      h('button', {type: 'submit', text: editing ? 'Save changes' : 'Create replication'}),
      h('a', {class: 'button quiet', href: editing ? `#/replications/${existing.id}` : '#/', text: 'Cancel'})));
  refreshDatabases();
  fill($('#content'), heading(editing ? 'EDIT REPLICATION' : 'NEW REPLICATION',
    h('h1', {class: 'compact'}, editing ? existing.name : 'Connect a source', h('span', {class: 'dot-accent', text: '.'})),
    editing ? 'Changes apply from the next run. A new host or user removes the password; a new source, slot, database or namespace copies everything again.'
      : 'Where the replication connects and where it writes. Then set the password, check the source and choose its tables.'), form);
}

// One replication
async function renderReplication(token) {
  const p = await api(`/replications/${route.id}`);
  if (token !== renderToken) return;
  crumbs([`${teamName(p.team)} · ${p.name}`]);
  const writer = isWriter(p.role), admin = isAdmin(p.role);
  const active = p.recentRuns.find(r => ACTIVE.has(r.status));
  const enabled = enabledIn(p.team).includes(p.environment);
  const hasTables = Object.keys(p.tables).length > 0;
  const blocked = p.secretsMissing.length ? 'Set the password first.' : !enabled ? 'Replication is not enabled in this environment.'
    : active ? 'A run is in progress.' : '';
  const start = async (event, path, body, message) => {
    event.target.disabled = true;
    try { await send(`/replications/${p.id}/${path}`, 'POST', body); go(location.hash, [message]); }
    catch (error) { failure(error); event.target.disabled = false; }
  };
  const runButton = writer && h('button', {type: 'button', text: active?.kind === 'sync' ? 'Running…' : 'Run now',
    disabled: Boolean(blocked) || !hasTables, title: blocked || (!hasTables ? 'Choose tables first.' : null),
    onclick: e => start(e, 'runs', {}, 'Run started.')});
  const checkButton = writer && h('button', {type: 'button', class: 'quiet', text: active?.kind === 'check' ? 'Checking…' : 'Check source',
    disabled: Boolean(blocked), title: blocked || null, onclick: e => start(e, 'check', undefined, 'Checking the source…')});
  fill($('#content'),
    heading(`${teamName(p.team)} · ${LABELS[p.environment]}`.toUpperCase(),
      h('h1', {class: 'compact'}, p.name, h('span', {class: 'dot-accent', text: '.'})), p.description,
      admin && h('a', {class: 'button quiet', href: `#/replications/${p.id}/edit`, text: 'Edit'}), checkButton, runButton),
    h('div', {class: 'status-line spaced'}, p.lastRun ? chip(p.lastRun.status, `Last run ${p.lastRun.status}`) : chip('idle', 'Never run'),
      typeChip(p.sourceType), h('span', {class: 'chip', text: p.options.deleteMode === 'dv' ? 'Deletion vectors' : 'Positional deletes'}),
      p.schedule && h('span', {class: 'chip', text: p.schedule.enabled ? `${p.schedule.cron} UTC` : 'Schedule paused'}),
      blocked && !active ? h('span', {class: 'small-print', text: blocked}) : null),
    p.warnings.length ? h('div', {class: 'notice error'}, h('ul', {class: 'plain'}, p.warnings.map(w => h('li', {text: w})))) : null,
    h('div', {class: 'columns'},
      h('div', {class: 'stack'},
        hasTables ? lastRunCard(p) : null,
        tablesCard(p),
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Runs'}), h('span', {class: 'small-print', text: 'Newest first'})),
          runsTable(p.recentRuns))),
      h('div', {class: 'stack'}, settingsCard(p), secretsCard(p), checkCard(p), scheduleCard(p), admin && deleteCard(p))));
  if (active) schedulePoll(() => render(true), 3000);
}

function stat(label, value) { return h('div', {class: 'stat'}, h('span', {class: 'eyebrow', text: label}), h('b', {text: value})); }

function lastRunCard(p) {
  const run = p.lastRun;
  if (!run) return h('section', {class: 'card'}, h('h2', {text: 'Last run'}), h('p', {class: 'small muted', text: 'Not run yet. Run it now, or schedule it.'}));
  const s = run.summary || {}, tables = s.tables || [];
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Last run'}),
      h('span', {class: 'small-print'}, time(run.finishedAt || run.createdAt), ` · ${duration(run)}`)),
    run.error && h('p', {class: 'error-text', text: run.error}),
    run.status === 'partial' && h('p', {class: 'small', text: 'The first copy stopped at the time limit; the next run continues where it stopped.'}),
    h('div', {class: 'stats'},
      stat('Changes read', number.format(s.recordsRead || 0)), stat('Rows written', number.format(s.recordsWritten || 0)),
      stat('Replaced or deleted', number.format(tables.reduce((n, t) => n + t.replaced, 0))), stat('Read up to', s.readUpTo || '—')),
    tables.length ? tableCounts(p, tables) : null);
}

function tableCounts(p, tables) {
  return h('div', {class: 'table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, h('th', {scope: 'col', text: 'Iceberg table'}), h('th', {scope: 'col', class: 'num', text: 'Rows written'}),
      h('th', {scope: 'col', class: 'num', text: 'Replaced or deleted'}), h('th', {scope: 'col', class: 'num', text: 'Delete files'}))),
    h('tbody', {}, tables.map(t => h('tr', {},
      h('td', {class: 'mono'}, `${p.namespace}.${t.table}`, h('p', {class: 'small-print', text: `from ${t.name}`})),
      h('td', {class: 'num', text: number.format(t.records)}), h('td', {class: 'num', text: number.format(t.replaced)}),
      h('td', {class: 'num', text: number.format(t.deleteFiles)}))))));
}

// The tables a replication keeps in step, chosen from the last source check.
function tablesCard(p) {
  const admin = isAdmin(p.role), discovered = p.lastCheck?.tables || [];
  const cdc = Boolean(p.source.slot && p.source.publication);
  const names = [...new Set([...discovered.map(t => t.name), ...Object.keys(p.tables)])].sort();
  if (!names.length) {
    return h('section', {class: 'card'}, h('h2', {text: 'Tables'}),
      h('p', {class: 'small', text: p.lastCheck ? 'The last source check found no tables the account can read.' : 'Check the source to list the tables it can replicate.'}));
  }
  const choice = {};
  const rowsFor = names.map(name => {
    const info = discovered.find(t => t.name === name) || {primaryKey: [], cursorFields: [], modes: Object.keys(MODES)};
    const spec = p.tables[name];
    const c = choice[name] = {selected: Boolean(spec), mode: spec?.mode || (cdc ? 'cdc' : 'incremental'), cursor: spec?.cursor || '',
      partition: spec?.partition || '', table: spec?.table || name.split('.')[1].toLowerCase()};
    const modes = Object.keys(MODES).filter(m => (info.modes || []).includes(m) && (m !== 'cdc' || cdc));
    const cursor = h('select', {'aria-label': `Cursor column of ${name}`, disabled: !admin || c.mode !== 'incremental', onchange: e => { c.cursor = e.target.value; }},
      h('option', {value: '', text: '—'}), (info.cursorFields || []).map(col => h('option', {value: col, text: col, selected: col === c.cursor})));
    return h('tr', {},
      h('td', {}, h('input', {type: 'checkbox', checked: c.selected, disabled: !admin, 'aria-label': `Replicate ${name}`, onchange: e => { c.selected = e.target.checked; }})),
      h('td', {class: 'mono'}, name, !discovered.find(t => t.name === name) && p.lastCheck ? h('p', {class: 'error-text', text: 'Not found in the last check'}) : null),
      h('td', {class: 'mono'}, info.primaryKey?.length ? info.primaryKey.join(', ') : h('span', {class: 'chip warn', text: 'No key'})),
      h('td', {}, h('select', {'aria-label': `How ${name} is kept in step`, disabled: !admin, onchange: e => { c.mode = e.target.value; cursor.disabled = c.mode !== 'incremental'; }},
        modes.map(m => h('option', {value: m, text: MODES[m], selected: m === c.mode})))),
      h('td', {}, cursor),
      h('td', {}, h('input', {class: 'inline', value: c.table, disabled: !admin, 'aria-label': `Iceberg table for ${name}`, oninput: e => { c.table = e.target.value.trim(); }})),
      h('td', {}, h('input', {class: 'inline', value: c.partition, disabled: !admin, placeholder: '/{created_at,month}', 'aria-label': `Partition of ${name}`,
        oninput: e => { c.partition = e.target.value.trim(); }})));
  });
  async function save(event) {
    event.preventDefault();
    const tables = {};
    for (const [name, c] of Object.entries(choice)) {
      if (!c.selected) continue;
      tables[name] = {mode: c.mode, table: c.table || undefined, ...(c.mode === 'incremental' ? {cursor: c.cursor} : {}),
                      ...(c.partition ? {partition: c.partition} : {})};
    }
    try {
      const changed = await send(`/replications/${p.id}`, 'PATCH', {tables, expected_revision: p.revision});
      go(location.hash, [changed.hint || `${Object.keys(tables).length} tables chosen.`]);
    } catch (error) { failure(error); }
  }
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: `Tables`}), h('span', {class: 'small-print', text: `${Object.keys(p.tables).length} of ${names.length} chosen`})),
    h('form', {onsubmit: save},
      h('div', {class: 'table-wrap'}, h('table', {class: 'hints'},
        h('thead', {}, h('tr', {}, h('th', {scope: 'col'}, h('span', {class: 'visually-hidden', text: 'Chosen'})),
          ['Source table', 'Primary key', 'Sync', 'Cursor', 'Iceberg table', 'Partition'].map(t => h('th', {scope: 'col', text: t})))),
        h('tbody', {}, rowsFor))),
      h('p', {class: 'small-print', text: `Tables land in ${p.databaseName || p.database} › ${p.namespace}. A table without a primary key is appended to. Partitioned change-capture tables need REPLICA IDENTITY FULL at the source.`}),
      admin && h('div', {class: 'actions'}, h('button', {type: 'submit', class: 'small', text: 'Save tables'}))));
}

function checkCard(p) {
  const c = p.lastCheck;
  const items = (c?.checks || []).map(item => h('div', {class: 'check-row'},
    h('i', {class: `status ${item.passed ? 'success' : item.required ? 'failed' : 'warn'}`, 'aria-hidden': 'true'}),
    h('div', {}, h('div', {text: item.name.replaceAll('_', ' ')}),
      h('p', {class: 'small-print', text: item.passed ? item.currentValue : `${item.currentValue} · wanted: ${item.recommendedValue}`})),
    h('span', {class: `chip ${item.passed ? 'success' : item.required ? 'failed' : 'warn'}`, text: item.required ? 'Required' : 'Advised'})));
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Source checks'}), c && h('span', {class: 'small-print'}, 'checked ', time(c.checkedAt))),
    !c ? h('p', {class: 'small', text: 'Not checked yet. Set the password, then Check source.'})
      : !c.connected ? h('p', {class: 'error-text', text: c.message || 'Could not connect.'})
        : [items, c.ready ? null : h('p', {class: 'small', text: c.message || ''})],
    h('h3', {text: 'Set up the source'}),
    h('p', {class: 'small', text: 'Ask the database owner to run this once, then check again.'}),
    h('pre', {class: 'code', text: p.setupSql}),
    h('button', {type: 'button', class: 'quiet small', text: 'Copy SQL', onclick: async () => {
      try { await navigator.clipboard.writeText(p.setupSql); notice('Setup SQL copied.'); } catch { notice('Copy the SQL by hand.', 'error'); }
    }}));
}

function settingsCard(p) {
  return h('section', {class: 'card'}, h('h2', {text: 'Replication'}), h('dl', {class: 'facts'},
    h('dt', {text: 'Source'}), h('dd', {text: source(p)}),
    h('dt', {text: 'User'}), h('dd', {text: `${p.source.username} · TLS ${p.source.tls}`}),
    h('dt', {text: 'Into'}), h('dd', {}, target(p)),
    h('dt', {text: 'Slot'}), h('dd', {text: p.source.slot || '— (no change data capture)'}),
    h('dt', {text: 'Publication'}), h('dd', {text: p.source.publication || '—'}),
    h('dt', {text: 'First run'}), h('dd', {text: p.options.firstRun === 'changes' ? 'Only changes from then on' : 'Existing rows, then changes'}),
    h('dt', {text: 'Runs as'}), h('dd', {text: `${teamName(p.team)} service account (${p.environment})`}),
    h('dt', {text: 'Changed'}), h('dd', {}, `${p.updatedBy}, `, time(p.updatedAt))));
}

function runsTable(runs) {
  if (!runs.length) return h('p', {class: 'muted small', text: 'No runs yet.'});
  return h('div', {class: 'table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, h('th', {scope: 'col', text: 'Status'}), h('th', {scope: 'col', text: 'Started'}),
      h('th', {scope: 'col', text: 'Duration'}), h('th', {scope: 'col', class: 'num', text: 'Rows written'}), h('th', {scope: 'col', text: 'Read up to'}),
      h('th', {scope: 'col', text: 'By'}))),
    h('tbody', {}, runs.map(r => h('tr', {class: 'run-row', onclick: event => { if (!event.target.closest('a')) location.hash = `#/runs/${r.id}`; }},
      h('td', {}, h('a', {href: `#/runs/${r.id}`, 'aria-label': `Run ${r.status}, ${when(r.createdAt)}`}, chip(r.status)),
        r.kind === 'check' ? h('span', {class: 'agent', text: 'source check'}) : null),
      h('td', {class: 'nowrap'}, time(r.startedAt || r.createdAt)),
      h('td', {class: 'nowrap mono', text: duration(r)}),
      h('td', {class: 'num', text: r.kind === 'check' ? '—' : written(r)}),
      h('td', {class: 'mono nowrap', text: r.summary?.readUpTo || '—'}),
      h('td', {class: 'wide small'}, r.schedule ? 'schedule' : r.triggeredBy, r.agent && h('span', {class: 'agent', text: 'agent'}),
        r.fullRefresh && h('span', {class: 'agent', text: 'copy again'})))))));
}

function secretsCard(p) {
  const admin = isAdmin(p.role);
  const set = p.secretsSet.includes('password');
  const field = h('input', {type: 'password', class: 'inline', autocomplete: 'new-password', 'aria-label': 'Database password',
    placeholder: set ? 'Replace password' : 'Password', required: true});
  return h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Password'}), chip(set ? 'set' : 'missing', set ? 'set' : 'missing')),
    admin && h('form', {class: 'secret-row', onsubmit: async event => {
      event.preventDefault();
      if (!field.value) return field.focus();
      try { await send(`/replications/${p.id}/secrets`, 'PUT', {secrets: {password: field.value}}); field.value = ''; go(location.hash, [set ? 'Password replaced.' : 'Password set. Check the source next.']); }
      catch (error) { failure(error); }
    }}, field, h('button', {type: 'submit', class: 'small', text: set ? 'Replace' : 'Set'})),
    h('p', {class: 'small-print', text: `For ${p.source.username}@${p.source.host}. Stored encrypted and write-only: nobody can read it back, and runs mask it in logs.`}),
    !admin ? h('p', {class: 'small-print', text: 'A team administrator sets the password.'}) : null);
}

function scheduleCard(p) {
  const admin = isAdmin(p.role), s = p.schedule;
  const cron = h('input', {class: 'inline', value: s?.cron || '*/15 * * * *', 'aria-label': 'Cron expression in UTC', autocomplete: 'off', spellcheck: 'false'});
  const enabled = h('input', {type: 'checkbox', checked: s ? s.enabled : true});
  return h('section', {class: 'card'}, h('h2', {text: 'Schedule'}),
    s ? h('dl', {class: 'facts'},
      h('dt', {text: 'Cron (UTC)'}), h('dd', {text: s.cron + (s.enabled ? '' : ' · paused')}),
      h('dt', {text: 'Next run'}), h('dd', {}, s.enabled ? time(s.nextRunAt) : '—'),
      h('dt', {text: 'Set by'}), h('dd', {text: s.createdBy}))
      : h('p', {class: 'small', text: 'Not scheduled. Runs start only when someone runs the replication.'}),
    admin && h('form', {class: 'stack', onsubmit: async event => {
      event.preventDefault();
      try { await send(`/replications/${p.id}/schedule`, 'PUT', {cron: cron.value.trim(), enabled: enabled.checked}); go(location.hash, ['Schedule saved.']); }
      catch (error) { failure(error); }
    }},
      h('div', {class: 'presets', role: 'group', 'aria-label': 'Presets'}, CRON_PRESETS.map(([label, value]) =>
        h('button', {type: 'button', class: 'quiet', text: label, onclick: () => { cron.value = value; cron.focus(); }}))),
      h('div', {class: 'cron-row'}, cron, h('label', {class: 'check'}, enabled, 'Enabled')),
      h('p', {class: 'small-print', text: 'Five fields in UTC, at most every 5 minutes. A due run is skipped while the previous one still runs; the next run reads everything since.'}),
      h('div', {class: 'actions'}, h('button', {type: 'submit', class: 'small', text: s ? 'Save schedule' : 'Schedule'}),
        s && h('button', {type: 'button', class: 'quiet small', text: 'Remove', onclick: async () => {
          try { await send(`/replications/${p.id}/schedule`, 'DELETE'); go(location.hash, ['Schedule removed.']); } catch (error) { failure(error); }
        }}))));
}

function deleteCard(p) {
  return h('section', {class: 'card danger-zone'}, h('h2', {text: 'Delete replication'}),
    h('p', {class: 'small', text: `Stops the schedule and removes its runs and password. The Iceberg tables stay.${p.source.slot ? ` Afterwards the database owner drops replication slot ${p.source.slot}, or the source keeps its change log.` : ''}`}),
    h('div', {class: 'actions'},
      isWriter(p.role) && h('button', {type: 'button', class: 'quiet small', text: 'Copy everything again', onclick: async event => {
        if (!await confirmDialog('Copy everything again?', 'The next run starts without the saved change log position and copies every table again. Rows are upserted on their key, so tables with a key keep one row per key; append-only tables get the rows again.', 'Copy again')) return;
        event.target.disabled = true;
        try { await send(`/replications/${p.id}/runs`, 'POST', {full_refresh: true}); go(location.hash, ['Started a run that copies everything again.']); }
        catch (error) { failure(error); event.target.disabled = false; }
      }}),
      h('button', {type: 'button', class: 'danger small', text: 'Delete', onclick: () => deleteReplication(p)})));
}

async function deleteReplication(p) {
  const input = h('input', {class: 'inline', autocomplete: 'off', 'aria-label': 'Replication name'});
  const ok = await confirmDialog(`Delete ${p.name}?`,
    'Its runs, schedule and password go. The tables it wrote stay in the database, with their data. Type the replication\'s name to confirm.',
    'Delete', true, input, () => input.value === p.name);
  if (!ok) return;
  try {
    const deleted = await send(`/replications/${p.id}/delete`, 'POST', {confirm_name: input.value});
    if (deleted.dropSlotSql) {
      await confirmDialog(`${p.name} is deleted`, `${deleted.warning} Give the database owner this SQL: ${deleted.dropSlotSql}`, 'Done');
    }
    go('#/', [`${p.name} is deleted. Its tables stay.`]);
  } catch (error) { failure(error); }
}

// One run
async function renderRun(token) {
  const run = await api(`/runs/${route.id}`);
  const [p, logs] = await Promise.all([api(`/replications/${run.replication}`), api(`/runs/${run.id}/logs?tail=2000`)]);
  if (token !== renderToken) return;
  crumbs([`${teamName(p.team)} · ${p.name}`, `#/replications/${p.id}`], [run.kind === 'check' ? 'Source check' : 'Run']);
  const active = ACTIVE.has(run.status), summary = run.summary || {};
  const previous = $('#run-log');
  const stick = !previous || previous.scrollTop + previous.clientHeight >= previous.scrollHeight - 24;
  const log = h('pre', {id: 'run-log', class: 'log', tabindex: '0', 'aria-label': 'Run log',
    text: logs.lines.length ? logs.lines.join('\n') : active ? 'Waiting for output…' : 'No log for this run.'});
  const tables = summary.tables || [];
  fill($('#content'),
    heading(`${run.kind === 'check' ? 'SOURCE CHECK' : 'RUN'} · ${LABELS[run.environment]}`.toUpperCase(), h('h1', {class: 'compact'}, p.name), null,
      active && isWriter(p.role) && h('button', {type: 'button', class: 'danger', text: 'Stop run', onclick: async event => {
        event.target.disabled = true;
        try { await send(`/runs/${run.id}/cancel`, 'POST'); go(location.hash, ['Stopping the run…']); } catch (error) { failure(error); }
      }}),
      h('a', {class: 'button quiet', href: `#/replications/${p.id}`, text: 'Back to replication'})),
    h('div', {class: 'columns'},
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Log'}),
          h('span', {class: 'small-print', text: active ? 'Updates every 2 seconds' : 'Secrets are masked'})), log),
        run.kind === 'sync' && h('section', {class: 'card'}, h('h2', {text: 'Tables'}),
          tables.length ? tableCounts(p, tables) : h('p', {class: 'small muted', text: active ? 'Tables appear when the run finishes.' : 'No tables were written.'}),
          summary.published?.errors?.length ? h('div', {class: 'notice error'}, 'Table properties could not be set:',
            h('ul', {class: 'plain'}, summary.published.errors.map(e => h('li', {text: typeof e === 'string' ? e : JSON.stringify(e)})))) : null)),
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'status-line'}, chip(run.status, STATUS_LABELS[run.status] ? `${run.status} · ${STATUS_LABELS[run.status]}` : run.status),
          run.fullRefresh && h('span', {class: 'chip', text: 'copy again'})),
          run.error && h('p', {class: 'error-text', text: run.error}),
          h('dl', {class: 'facts'},
            h('dt', {text: 'Queued'}), h('dd', {}, time(run.createdAt)),
            h('dt', {text: 'Started'}), h('dd', {}, time(run.startedAt)),
            h('dt', {text: 'Finished'}), h('dd', {}, active ? '—' : time(run.finishedAt)),
            h('dt', {text: 'Duration'}), h('dd', {text: duration(run)}),
            h('dt', {text: 'Started by'}), h('dd', {}, run.schedule ? 'schedule' : run.triggeredBy, run.agent && h('span', {class: 'agent', text: run.agent})),
            summary.recordsRead !== undefined && h('dt', {text: 'Changes read'}), summary.recordsRead !== undefined && h('dd', {text: number.format(summary.recordsRead)}),
            summary.readUpTo && h('dt', {text: 'Read up to'}), summary.readUpTo && h('dd', {class: 'mono', text: summary.readUpTo}),
            summary.firstCopyComplete !== undefined && summary.firstCopyComplete !== null && h('dt', {text: 'First copy'}),
            summary.firstCopyComplete !== undefined && summary.firstCopyComplete !== null && h('dd', {text: summary.firstCopyComplete ? 'complete' : 'continues next run'}),
            summary.olakeVersion && h('dt', {text: 'OLake'}), summary.olakeVersion && h('dd', {text: summary.olakeVersion}),
            run.exitCode !== null && run.exitCode !== undefined && h('dt', {text: 'Exit code'}),
            run.exitCode !== null && run.exitCode !== undefined && h('dd', {text: String(run.exitCode)}))),
        h('section', {class: 'card'}, h('h2', {text: 'Where the data is'}),
          h('p', {class: 'small'}, 'Tables land in ', target(p), '. They show "Produced by olake" in the catalog of the user portal.'),
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
  const result = await (await fetch('/auth/logout', {method: 'POST', headers: {'X-Iceberg-Replication': '1'}, credentials: 'same-origin'})).json();
  location.href = result.logoutUrl || '/';
};
window.addEventListener('hashchange', () => render());
theme();
render();
