'use strict';
// The Maintenance page over the REST API: table health of your teams' databases, their Apache Polaris policies
// and runs.
// Agents do the same through the MCP server; the server checks every permission again.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
const ACTIVE = new Set(['queued', 'running']);
// Every run status of the platform's extensions; Maintenance itself never reports incomplete.
const RUN_STATUSES = new Set(['queued', 'running', 'success', 'incomplete', 'partial', 'failed', 'error', 'cancelled']);
// The most table health one request returns; the response's total says how many there are.
const TABLE_LIMIT = 5000;
const STATES = {healthy: 'Healthy', attention: 'Needs attention', failed: 'Failed', 'no-policy': 'No policy', excluded: 'Excluded'};
const STATE_CLASS = {healthy: 'healthy', attention: 'attention', failed: 'failed', 'no-policy': 'idle', excluded: 'idle'};
const MODES = {inspect: 'Health check', 'dry-run': 'Dry run', maintain: 'Maintenance'};
const OPERATIONS = {
  compaction: 'Compact data files', danglingDeletes: 'Remove dangling deletes', rewriteManifests: 'Rewrite manifests',
  orphanFiles: 'Remove orphan files', expireSnapshots: 'Expire snapshots', deadline: 'Time limit',
};
// The four Polaris system policy types, in the order a run applies them.
const TYPES = {
  dataCompaction: 'Compact data files', metadataCompaction: 'Rewrite manifests', orphanFileRemoval: 'Remove orphan files',
  snapshotExpiry: 'Expire snapshots',
};
const POLARIS_TYPES = {dataCompaction: 'system.data-compaction', metadataCompaction: 'system.metadata-compaction',
  orphanFileRemoval: 'system.orphan-file-removal', snapshotExpiry: 'system.snapshot-expiry'};
const ATTACHED = {catalog: 'the database', namespace: 'its namespace', table: 'the table'};
const MB = 1048576;
const ACTION_CLASS = {done: 'done', planned: 'planned', skipped: 'idle', off: 'idle', failed: 'failed'};
const SEEN_BY = {inspect: 'health check', 'dry run': 'dry run', maintain: 'maintenance'};
const BUCKETS = ['<1 MB', '1–4', '4–16', '16–64', '64–128', '128–256', '>256 MB'];
const CRON_PRESETS = [['Nightly 02:00', '0 2 * * *'], ['Every 6 hours', '0 */6 * * *'], ['Sundays 03:00', '0 3 * * 0']];
const number = new Intl.NumberFormat();
let me = null, route = {}, poller = null, flash = null, renderToken = 0;

function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'value') node.value = value;
    // The page's CSP refuses style attributes; CSSOM properties are allowed.
    else if (key === 'style') for (const rule of value.split(';')) { const [k, ...v] = rule.split(':'); if (k.trim()) node.style.setProperty(k.trim(), v.join(':').trim()); }
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
    headers: {...(options.body ? {'Content-Type': 'application/json'} : {}), ...(write ? {'X-Iceberg-Maintenance': '1'} : {})},
  });
  const body = response.headers.get('content-type')?.includes('json') ? await response.json() : {};
  if (response.status === 401) { showSignin(); throw Object.assign(new Error(body.error || 'Sign in to continue.'), {signin: true}); }
  if (!response.ok) throw Object.assign(new Error(body.error || `Request failed (${response.status}).`), {code: body.code, detail: body.detail});
  return body;
}
const send = (path, method, body) => api(path, {method, body: body === undefined ? undefined : JSON.stringify(body)});

// Formatting. The API's times are RFC 3339 strings in UTC, such as 2026-11-24T09:15:02.123Z.
function when(value) {
  if (!value) return '—';
  const date = new Date(value), diff = (Date.now() - date) / 1000, ahead = diff < 0, d = Math.abs(diff);
  const text = d < 60 ? (ahead ? 'in a moment' : 'just now') : d < 3600 ? `${Math.round(d / 60)} min` : d < 86400 ? `${Math.round(d / 3600)} h` : null;
  if (!text) return date.toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'});
  return d < 60 ? text : ahead ? `in ${text}` : `${text} ago`;
}
function time(value) {
  return h('time', {datetime: value || null,
                    title: value ? new Date(value).toLocaleString(undefined, {dateStyle: 'full', timeStyle: 'long'}) : null,
                    text: when(value)});
}
function age(ms) {
  if (!ms) return '—';
  const hours = (Date.now() - ms) / 3600000;
  return hours < 1 ? `${Math.max(1, Math.round(hours * 60))} min` : hours < 48 ? `${Math.round(hours)} h` : `${Math.round(hours / 24)} d`;
}
function duration(run) {
  if (!run.startedAt) return '—';
  const s = Math.max(0, Math.round(((run.finishedAt ? Date.parse(run.finishedAt) : Date.now()) - Date.parse(run.startedAt)) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min`;
}
function bytes(b) {
  if (!b) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(b) / Math.log(1024)));
  return `${(b / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
}
function days(value) { return value === 1 ? '1 day' : `${value} days`; }
function chip(status, label) { return h('span', {class: `chip ${status}`}, h('i', {class: `status ${status}`, 'aria-hidden': 'true'}), label || status); }
function runChip(status) { return chip(RUN_STATUSES.has(status) ? status : 'idle', status); }
function stateChip(state) { return chip(STATE_CLASS[state] || 'idle', STATES[state] || state); }
function teamName(id) { return (me?.memberships.find(m => m.team === id) || {}).teamName || id; }
const isAdmin = role => ADMINS.includes(role);
const isWriter = role => WRITERS.includes(role);
function roleIn(team) { return (me?.memberships.find(m => m.team === team) || {}).role; }
function enabledIn(team) { return (me?.enabledEnvironments.find(e => e.team === team) || {}).environments || []; }
function deleteFiles(health) { return (health?.positionDeleteFiles || 0) + (health?.equalityDeleteFiles || 0) + (health?.deletionVectors || 0); }
function tableHref(t) { return `#/databases/${t.database}/tables/${encodeURIComponent(t.name)}`; }

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
  if (parts[0] === 'databases' && parts[1] && parts[2] === 'policy') return {page: 'policy', id: parts[1]};
  if (parts[0] === 'databases' && parts[1] && parts[2] === 'tables' && parts[3]) return {page: 'table', id: parts[1], table: decodeURIComponent(parts[3])};
  if (parts[0] === 'databases' && parts[1]) return {page: 'database', id: parts[1]};
  if (parts[0] === 'runs' && parts[1]) return {page: 'run', id: parts[1]};
  return {page: 'home', team: params.get('team') || '', environment: ENVIRONMENTS.includes(params.get('environment')) ? params.get('environment') : '',
          attention: params.get('attention') === '1'};
}
function crumbs(...items) {
  fill($('#crumbs'), h('a', {href: '#/', text: 'Table health'}),
       items.flatMap(([text, href]) => [' / ', href ? h('a', {href, text}) : h('span', {text, 'aria-current': 'page'})]));
}
function heading(eyebrow, title, text, ...actions) {
  return h('section', {class: 'page-heading'},
    h('div', {}, h('p', {class: 'eyebrow', text: eyebrow}), title, text && h('p', {text})),
    actions.filter(Boolean).length ? h('div', {class: 'actions'}, actions) : null);
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
      // Who is signed in, with Sign out, before /me: an account /me refuses (not linked to a portal user, or
      // someone else's Keycloak session) must still be able to sign out and sign in as someone else.
      $('#identity').hidden = false; $('#username').textContent = session.user || '';
      me = await api('/me');
      $('#identity').hidden = false; $('#username').textContent = me.user.name;
      IcebergApps.mount($('#apps'), {...me.apps, current: me.extension});
    }
    show('page');
    if (flash) { notice(...flash); flash = null; } else notice('');
    const pages = {home: renderHome, database: renderDatabase, policy: renderPolicy, table: renderTable, run: renderRun};
    await pages[route.page](token);
    if (token === renderToken && !keep) window.scrollTo(0, 0);
  } catch (error) {
    if (error.signin) return;
    if (!me) { show('loading'); $('#loading p').textContent = error.message; return; }
    show('page'); failure(error);
  }
}
function go(hash, message) { if (message) flash = message; if (location.hash === hash) render(true); else location.hash = hash; }

async function startRun(database, mode, tables) {
  if (mode === 'maintain' && !await confirmDialog(`Run maintenance on ${database.name}?`,
    `Applies the policy now in ${LABELS[database.environment]}, as ${teamName(database.team)}'s service account. Expired snapshots can no longer be used for time travel.`,
    'Run maintenance', true)) return;
  try {
    const run = await send(`/databases/${database.id}/runs`, 'POST', {mode, tables: tables || null});
    go(`#/runs/${run.id}`, [`${MODES[mode]} started.`]);
  } catch (error) { failure(error); }
}

// Table health of every database you can see
async function renderHome(token) {
  crumbs();
  const query = new URLSearchParams();
  if (route.team) query.set('team', route.team);
  if (route.environment) query.set('environment', route.environment);
  const tableQuery = new URLSearchParams([...query, ['limit', TABLE_LIMIT]]);
  const [{databases}, {tables, total}] = await Promise.all([api(`/databases?${query}`), api(`/tables?${tableQuery}`)]);
  if (token !== renderToken) return;
  const setFilter = changes => {
    const next = new URLSearchParams({team: route.team, environment: route.environment, attention: route.attention ? '1' : '', ...changes});
    for (const [k, v] of [...next]) if (!v) next.delete(k);
    location.hash = `#/?${next}`;
  };
  const needs = tables.filter(t => ['attention', 'failed'].includes(t.state));
  const shown = route.attention ? needs : tables;
  const runsBusy = databases.some(d => ACTIVE.has(d.lastRun?.status));
  const filters = h('div', {class: 'filters'},
    me.memberships.length > 1 && h('select', {class: 'pill', 'aria-label': 'Team', onchange: e => setFilter({team: e.target.value})},
      h('option', {value: '', text: 'All teams'}),
      me.memberships.map(m => h('option', {value: m.team, text: m.teamName, selected: m.team === route.team}))),
    h('div', {class: 'segmented', role: 'group', 'aria-label': 'Environment'},
      [['', 'All'], ...ENVIRONMENTS.map(e => [e, LABELS[e]])].map(([value, label]) =>
        h('button', {type: 'button', 'aria-pressed': String(route.environment === value), text: label, onclick: () => setFilter({environment: value})}))),
    h('div', {class: 'segmented', role: 'group', 'aria-label': 'Health'},
      h('button', {type: 'button', 'aria-pressed': String(!route.attention), text: `All ${total}`, onclick: () => setFilter({attention: ''})}),
      h('button', {type: 'button', 'aria-pressed': String(route.attention), text: `Needs attention ${needs.length}`, onclick: () => setFilter({attention: '1'})})));
  fill($('#content'),
    heading('MAINTENANCE', h('h1', {}, 'Table health', h('span', {class: 'dot-accent', text: '.'})),
      'Snapshots, small files and delete files in your teams\' Iceberg databases, and the policies that keep them in check. Runs act as each team\'s service account.'),
    filters, statsRow(databases, total, needs),
    total > tables.length && h('div', {class: 'notice', text: `Showing the first ${number.format(tables.length)} of ${number.format(total)} tables. Choose a team or an environment, or open a database, to see the rest.`}),
    tables.length ? tableList(shown) : emptyState(databases),
    databasesCard(databases),
    h('div', {class: 'columns spaced-top'}, environmentsCard(), agentCard()));
  if (runsBusy) schedulePoll(() => render(true), 4000);
}

function statsRow(databases, total, needs) {
  const runs = databases.map(d => d.lastRun).filter(Boolean);
  const expired = runs.reduce((sum, r) => sum + (r.summary?.totals?.snapshotsExpired || 0), 0);
  const compacted = runs.reduce((sum, r) => sum + (r.summary?.totals?.filesCompacted || 0), 0);
  const policies = databases.filter(d => d.policy).length;
  return h('div', {class: 'stats'},
    h('div', {class: 'stat'}, h('span', {class: 'label', text: 'Tables'}), h('span', {class: 'value', text: number.format(total)}),
      h('span', {class: 'sub', text: `${databases.length} databases · ${policies} with a policy`})),
    h('div', {class: 'stat'}, h('span', {class: 'label', text: 'Needs attention'}), h('span', {class: `value ${needs.length ? 'warn' : ''}`, text: number.format(needs.length)}),
      h('span', {class: 'sub', text: needs.length ? 'small files, snapshots or deletes' : 'all tables are in shape'})),
    h('div', {class: 'stat'}, h('span', {class: 'label', text: 'Expired · last runs'}), h('span', {class: 'value', text: number.format(expired)}),
      h('span', {class: 'sub', text: 'snapshots'})),
    h('div', {class: 'stat'}, h('span', {class: 'label', text: 'Compacted · last runs'}), h('span', {class: 'value', text: number.format(compacted)}),
      h('span', {class: 'sub', text: 'data files rewritten'})));
}

function smallMeter(health) {
  if (!health || !health.dataFiles) return h('span', {class: 'muted small', text: health ? 'No data files' : '—'});
  const ratio = health.smallFiles / health.dataFiles;
  return h('div', {class: 'meter'}, h('div', {class: 'bar'}, h('i', {class: ratio >= 0.5 ? 'warn' : ratio < 0.1 ? 'success' : '', style: `width:${Math.round(ratio * 100)}%`})),
    h('span', {text: `${number.format(health.smallFiles)} of ${number.format(health.dataFiles)} · avg ${bytes(health.dataBytes / health.dataFiles)}`}));
}

function lastRunCell(t) {
  if (!t.lastRun) return h('span', {class: 'muted small', text: 'Never'});
  return h('div', {class: 'run-cell'}, h('a', {href: `#/runs/${t.lastRun}`, class: 'plain-link'}, chip(t.lastStatus === 'success' ? 'success' : t.lastStatus || 'idle', t.lastStatus || 'run')),
    h('span', {class: 'small-print'}, SEEN_BY[t.inspectedBy] || t.inspectedBy, ' · ', time(t.inspectedAt)));
}

function tableList(tables, withDatabase = true) {
  if (!tables.length) return h('section', {class: 'card'}, h('p', {class: 'small', text: 'No tables need attention.'}));
  return h('div', {class: 'card table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, ['Table', 'Health', 'Snapshots', 'Small files', 'Delete files', 'Last run'].map((t, i) =>
      h('th', {scope: 'col', class: [2, 4].includes(i) ? 'num' : null, text: t})))),
    h('tbody', {}, tables.map(t => h('tr', {},
      h('td', {class: 'name-cell wide'}, h('a', {href: tableHref(t), text: withDatabase ? `${t.databaseName} · ${t.name}` : t.name}),
        h('p', {}, `${teamName(t.team)} · ${LABELS[t.environment]}`, t.health?.producer && h('span', {class: 'agent', text: `Produced by ${t.health.producer}`}))),
      h('td', {class: 'wide'}, stateChip(t.state), t.reasons.length ? h('div', {class: 'reasons', text: t.reasons.join(' · ')}) : null),
      h('td', {class: 'num'}, number.format(t.health?.snapshots || 0), h('div', {class: 'small-print', text: `oldest ${age(t.health?.oldestSnapshotMs)}`})),
      h('td', {class: 'wide'}, smallMeter(t.health)),
      h('td', {class: 'num', text: number.format(deleteFiles(t.health))}),
      h('td', {class: 'wide'}, lastRunCell(t)))))));
}

function emptyState(databases) {
  const first = databases.find(d => isWriter(d.role) && enabledIn(d.team).includes(d.environment));
  return h('section', {class: 'card empty-state'},
    h('p', {class: 'eyebrow', text: 'NO TABLE HEALTH YET'}),
    h('h2', {text: 'Check your tables first.'}),
    h('p', {text: 'A health check reads every table\'s snapshots, files and delete files from the catalog, and changes nothing. Then a team administrator sets a policy per database: how long history stays, how big files should be and when maintenance runs.'}),
    h('ol', {class: 'how'},
      h('li', {}, h('span', {text: '[ 01 ]'}), 'Enable an environment'),
      h('li', {}, h('span', {text: '[ 02 ]'}), 'Check a database\'s health'),
      h('li', {}, h('span', {text: '[ 03 ]'}), 'Set a policy and a schedule')),
    first ? h('div', {class: 'actions'}, h('button', {type: 'button', text: `Check ${first.name}`, onclick: () => startRun(first, 'inspect')}))
      : h('p', {class: 'small-print', text: databases.length ? 'Enable Maintenance for an environment below to check its databases.' : 'Your teams have no databases yet.'}));
}

function databasesCard(databases) {
  if (!databases.length) return null;
  return h('section', {class: 'card table-wrap spaced-top'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Databases'}), h('span', {class: 'small-print', text: 'Policies live in Apache Polaris'})),
    h('table', {class: 'responsive'},
      h('thead', {}, h('tr', {}, ['Database', 'Policy', 'Schedule (UTC)', 'Tables', 'Last run'].map((t, i) => h('th', {scope: 'col', class: i === 3 ? 'num' : null, text: t})))),
      h('tbody', {}, databases.map(d => h('tr', {},
        h('td', {class: 'name-cell wide'}, h('a', {href: `#/databases/${d.id}`, text: d.name}), h('p', {text: `${d.teamName} · ${LABELS[d.environment]}`})),
        h('td', {}, d.policy ? chip('success', 'Policy') : d.policy === false ? chip('idle', 'No policy') : h('span', {class: 'muted small', title: 'Maintenance is not enabled here, so it cannot read the policies.', text: '—'})),
        h('td', {class: 'mono'}, d.schedule ? (d.schedule.enabled ? d.schedule.cron : `${d.schedule.cron} (paused)`) : h('span', {class: 'muted', text: '—'})),
        h('td', {class: 'num'}, number.format(d.tables), d.attention ? h('div', {class: 'reasons', text: `${d.attention} need attention`}) : null),
        h('td', {class: 'wide'}, d.lastRun ? h('div', {class: 'run-cell'}, h('a', {href: `#/runs/${d.lastRun.id}`}, runChip(d.lastRun.status)),
          h('span', {class: 'small-print'}, MODES[d.lastRun.mode], ' · ', time(d.lastRun.createdAt))) : h('span', {class: 'muted small', text: 'Never'})))))));
}

function environmentsCard() {
  const items = me.memberships.map(m => {
    const on = enabledIn(m.team);
    return h('div', {class: 'env-row'}, h('span', {class: 'team', text: m.teamName}),
      ENVIRONMENTS.map(env => h('span', {class: `env ${on.includes(env) ? 'on' : 'off'}`},
        h('i', {class: `status ${on.includes(env) ? 'success' : ''}`, 'aria-hidden': 'true'}),
        `${LABELS[env]}${on.includes(env) ? '' : ' · off'}`,
        !on.includes(env) && isAdmin(m.role) ? h('button', {type: 'button', class: 'quiet', text: 'Enable',
          'aria-label': `Enable Maintenance for ${m.teamName} in ${LABELS[env]}`, onclick: () => enableEnvironment(m, env)}) : null)));
  });
  return h('section', {class: 'card agent-hint'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Where maintenance can run'})),
    h('p', {class: 'small', text: 'Runs only happen in environments a team administrator enabled. Each runs as that team\'s service account for the environment, which can rewrite and delete files in the team\'s databases.'}),
    h('div', {class: 'env-list'}, items));
}

async function enableEnvironment(membership, env) {
  const ok = await confirmDialog(`Enable Maintenance in ${LABELS[env]}?`,
    `Runs for ${membership.teamName} in ${LABELS[env]} will rewrite and delete files as the team's service account. The platform creates that account now; a team administrator can disable it again. Nothing is maintained until you save a policy.`,
    'Enable');
  if (!ok) return;
  try {
    await send(`/teams/${membership.team}/environments/${env}`, 'POST');
    me = await api('/me');
    go(location.hash, [`Maintenance is enabled for ${membership.teamName} in ${LABELS[env]}.`]);
  } catch (error) { failure(error); }
}

function agentCard() {
  return h('section', {class: 'card agent-hint'},
    h('h2', {text: 'With an AI agent'}),
    h('p', {class: 'small', text: 'Every action here is also an MCP tool and a REST operation, with your permissions. Ask your agent which tables need attention, or to dry-run a policy.'}),
    h('pre', {text: `claude mcp add --transport http --client-id ext-maintenance-mcp --callback-port 3010 iceberg-maintenance ${location.origin}/mcp`}),
    h('p', {class: 'small-print'}, 'API reference: ', h('a', {href: '/api/v1/openapi.json', text: '/api/v1/openapi.json'}), ' · MCP: ', h('code', {text: '/mcp'})));
}

// One database: its tables, policy and runs
async function renderDatabase(token) {
  const [{databases}, {tables, total}, policy, {runs}] = await Promise.all([api('/databases'), api(`/tables?database=${route.id}&limit=${TABLE_LIMIT}`),
    readPolicy(route.id), api(`/databases/${route.id}/runs?limit=10`)]);
  if (token !== renderToken) return;
  const d = databases.find(x => x.id === route.id);
  if (!d) throw new Error('No such database in your teams.');
  crumbs([`${d.teamName} · ${d.name}`]);
  const active = runs.find(r => ACTIVE.has(r.status));
  const enabled = enabledIn(d.team).includes(d.environment);
  const blocked = !enabled ? 'Maintenance is not enabled in this environment.' : active ? 'A run is in progress.' : '';
  const writer = isWriter(d.role);
  const button = (mode, label, cls) => writer && h('button', {type: 'button', class: cls, text: label, disabled: Boolean(blocked) || (mode === 'maintain' && !policy?.exists),
    title: blocked || (mode === 'maintain' && !policy?.exists ? 'Set a policy first.' : null), onclick: () => startRun(d, mode)});
  fill($('#content'),
    heading(`${d.teamName} · ${LABELS[d.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: d.name}), null,
      button('inspect', 'Check health', 'quiet'), button('dry-run', 'Dry run', 'quiet'), button('maintain', 'Run maintenance'),
      h('a', {class: 'button quiet', href: `#/databases/${d.id}/policy`, text: isAdmin(d.role) ? 'Edit policy' : 'Policy'})),
    h('div', {class: 'status-line spaced'}, policy?.exists ? chip('success', 'Policy') : chip('idle', policy ? 'No policy' : 'Policies unknown'),
      policy?.schedule && h('span', {class: 'chip', text: policy.schedule.enabled ? `Every ${policy.schedule.cron} UTC` : 'Schedule paused'}),
      blocked && !active ? h('span', {class: 'small-print', text: blocked}) : null),
    total > tables.length && h('div', {class: 'notice', text: `Showing the first ${number.format(tables.length)} of ${number.format(total)} tables.`}),
    tables.length ? tableList(tables, false) : h('section', {class: 'card'},
      h('h2', {text: 'Tables'}), h('p', {class: 'small', text: 'No health yet. Check health reads every table and changes nothing.'})),
    h('div', {class: 'columns spaced-top'},
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Runs'}), h('span', {class: 'small-print', text: 'Newest first'})), runsTable(runs))),
      h('div', {class: 'stack'}, policySummary(policy, d), policy && scheduleCard(policy, d))));
  if (active) schedulePoll(() => render(true), 3000);
}

function runsTable(runs) {
  if (!runs.length) return h('p', {class: 'muted small', text: 'No runs yet.'});
  return h('div', {class: 'table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, ['Status', 'Kind', 'Started', 'Duration', 'By'].map(t => h('th', {scope: 'col', text: t})))),
    h('tbody', {}, runs.map(r => h('tr', {class: 'run-row', onclick: event => { if (!event.target.closest('a')) location.hash = `#/runs/${r.id}`; }},
      h('td', {}, h('a', {href: `#/runs/${r.id}`, 'aria-label': `Run ${r.status}, ${when(r.createdAt)}`}, runChip(r.status))),
      h('td', {class: 'small', text: MODES[r.mode] + (r.tables.length ? ` · ${r.tables.length} tables` : '')}),
      h('td', {class: 'nowrap'}, time(r.startedAt || r.createdAt)),
      h('td', {class: 'nowrap mono', text: duration(r)}),
      h('td', {class: 'wide small'}, r.scheduled ? 'schedule' : r.triggeredBy, r.agent && h('span', {class: 'agent', text: 'agent'})))))));
}

// A database's policies are read as the team's service account: without one (Maintenance not enabled), null.
function readPolicy(id) {
  return api(`/databases/${id}/policy`).catch(error => { if (error.code === 'environment_not_enabled') return null; throw error; });
}

function describe(key, s) {
  if (!s.enabled) return 'off';
  if (key === 'snapshotExpiry') return `older than ${days(s.maxSnapshotAgeDays)} · keep last ${s.minSnapshotsToKeep}`;
  if (key === 'dataCompaction') return `${bytes(s.targetFileSizeBytes)} files · ≥ ${s.minInputFiles} inputs · ${bytes(s.maxInputBytesPerRun)} per table and run${s.removeDanglingDeletes ? ' · dangling deletes removed' : ''}`;
  if (key === 'metadataCompaction') return `merged above ${s.minCountToMerge} manifests`;
  return `older than ${days(s.maxOrphanFileAgeInDays)}`;
}

function policyFacts(settings) {
  return h('dl', {class: 'facts'}, Object.entries(TYPES).flatMap(([key, title]) => [h('dt', {text: title}), h('dd', {text: describe(key, settings[key])})]));
}

// What Polaris resolved for one table at its last run: per type the settings and where its policy is attached.
function appliedFacts(policies) {
  return h('dl', {class: 'facts'}, Object.entries(TYPES).flatMap(([key, title]) => {
    const p = policies[key];
    return [h('dt', {text: title}), h('dd', {},
      p.policy ? describe(key, p.settings) : 'no policy',
      p.policy && h('div', {class: 'small-print', text: `${p.policy.namespace}.${p.policy.name}, on ${ATTACHED[p.policy.appliedAt]}`}),
      p.problem && h('div', {class: 'error-text', text: `Not applied: ${p.problem}`}))];
  }));
}

function exclusionText(e) {
  const all = e.types.length === Object.keys(TYPES).length;
  return `${e.target}${e.level === 'namespace' ? ' (namespace)' : ''}${all ? '' : `: ${e.types.map(t => TYPES[t].toLowerCase()).join(', ')}`}`;
}

function policySummary(policy, d) {
  if (!policy) return h('section', {class: 'card'}, h('h2', {text: 'Policy'}),
    h('p', {class: 'small', text: 'Maintenance is not enabled in this environment, so it cannot read the database\'s policies. A team administrator enables it on the start page.'}));
  const foreign = Object.values(policy.policies).filter(p => p && !p.managed);
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Policy'}), policy.exists ? h('span', {class: 'small-print', text: 'Apache Polaris'}) : chip('idle', 'Defaults')),
    policyFacts(policy.settings),
    policy.exclusions.length ? h('p', {class: 'small-print', text: `Excluded: ${policy.exclusions.map(exclusionText).join(', ')}`}) : null,
    foreign.length ? h('p', {class: 'small-print', text: `Set by another tool: ${foreign.map(p => `${p.namespace}.${p.name}`).join(', ')}`}) : null,
    !policy.exists && h('p', {class: 'small-print', text: 'No policy yet. These are the defaults a new policy starts with; a type without a policy is not maintained.'}),
    h('a', {class: 'button quiet small', href: `#/databases/${d.id}/policy`, text: isAdmin(d.role) ? (policy.exists ? 'Edit policy' : 'Create policy') : 'Open policy'}));
}

// A database's schedule: one per database, set and removed on its own (PUT and DELETE …/schedule). It applies the
// policy, so it needs one.
function scheduleFacts(s) {
  return h('dl', {class: 'facts'},
    h('dt', {text: 'Cron (UTC)'}), h('dd', {text: s.cron + (s.enabled ? '' : ' · paused')}),
    h('dt', {text: 'Next run'}), h('dd', {}, s.enabled ? time(s.nextRunAt) : '—'),
    s.lastRun && h('dt', {text: 'Last scheduled run'}), s.lastRun && h('dd', {}, h('a', {href: `#/runs/${s.lastRun}`, text: 'Open'})));
}

function scheduleCard(policy, d) {
  const admin = isAdmin(d.role), s = policy.schedule;
  const cron = h('input', {class: 'inline', value: s?.cron || '0 2 * * *', placeholder: '0 2 * * *', required: true, maxlength: '100',
    'aria-label': 'Cron expression in UTC', autocomplete: 'off', spellcheck: 'false'});
  const enabled = h('input', {type: 'checkbox', checked: s ? s.enabled : true});
  const save = async event => {
    event.preventDefault();
    try { await send(`/databases/${d.id}/schedule`, 'PUT', {cron: cron.value.trim(), enabled: enabled.checked}); go(location.hash, ['Schedule saved.']); }
    catch (error) { failure(error); }
  };
  const remove = async () => {
    try { await send(`/databases/${d.id}/schedule`, 'DELETE'); go(location.hash, ['Schedule removed.']); } catch (error) { failure(error); }
  };
  return h('section', {class: 'card'}, h('h2', {text: 'Schedule'}),
    s ? scheduleFacts(s) : h('p', {class: 'small', text: 'Not scheduled. Maintenance runs only when someone starts it.'}),
    admin && policy.exists && h('form', {class: 'stack', onsubmit: save},
      h('div', {class: 'presets', role: 'group', 'aria-label': 'Presets'}, CRON_PRESETS.map(([label, value]) =>
        h('button', {type: 'button', class: 'quiet', text: label, onclick: () => { cron.value = value; cron.focus(); }}))),
      h('div', {class: 'cron-row'}, cron, h('label', {class: 'check'}, enabled, 'Enabled')),
      h('p', {class: 'small-print', text: 'Five fields in UTC, at most once an hour.'}),
      h('div', {class: 'actions'}, h('button', {type: 'submit', class: 'small', text: s ? 'Save schedule' : 'Schedule'}),
        s && h('button', {type: 'button', class: 'quiet small', text: 'Remove', onclick: remove}))),
    !policy.exists && h('p', {class: 'small-print', text: admin ? 'A schedule applies the policy: create the policy first.' : 'A schedule needs a policy, which a team administrator creates.'}),
    policy.exists && !admin && h('p', {class: 'small-print', text: 'A team administrator sets the schedule.'}),
    h('p', {class: 'small-print', text: 'A scheduled run applies the policies that apply to each table when it starts, and is skipped while another run of the database is in progress.'}));
}

// The policy editor
function sw(checked, label, disabled, onchange) {
  return h('button', {type: 'button', class: 'switch', role: 'switch', 'aria-checked': String(checked), 'aria-label': label, disabled,
    onclick: event => { const next = event.currentTarget.getAttribute('aria-checked') !== 'true'; event.currentTarget.setAttribute('aria-checked', String(next)); onchange(next); }});
}
function select(value, options, label, disabled, onchange) {
  const values = options.some(([v]) => v === value) ? options : [[value, String(value)], ...options];
  return h('select', {'aria-label': label, disabled, onchange: e => onchange(Number(e.target.value))},
    values.map(([v, text]) => h('option', {value: String(v), text, selected: v === value})));
}
function numberInput(value, label, min, max, disabled, onchange) {
  return h('input', {type: 'number', value: String(value), min: String(min), max: String(max), step: '1', inputmode: 'numeric', 'aria-label': label, disabled,
    oninput: e => onchange(Number(e.target.value))});
}
function field(label, control, help) { return h('label', {class: 'field'}, h('span', {text: label}), control, help && h('span', {class: 'help', text: help})); }

async function renderPolicy(token) {
  const [{databases}, {tables}, policy] = await Promise.all([api('/databases'), api(`/tables?database=${route.id}&limit=${TABLE_LIMIT}`), readPolicy(route.id)]);
  if (token !== renderToken) return;
  const d = databases.find(x => x.id === route.id);
  if (!d) throw new Error('No such database in your teams.');
  crumbs([`${d.teamName} · ${d.name}`, `#/databases/${d.id}`], ['Policy']);
  if (!policy) {
    fill($('#content'), heading(`${d.teamName} · ${LABELS[d.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: `${d.name} · policy`}), null),
      h('section', {class: 'card'}, h('p', {class: 'small', text: 'Maintenance is not enabled in this environment. Its policies are Apache Polaris policies that Maintenance reads and writes as the team\'s service account, so a team administrator enables it first, on the start page.'})));
    return;
  }
  const admin = isAdmin(d.role), locked = !admin;
  const s = structuredClone(policy.settings);
  let exclusions = policy.exclusions.map(({target, level, types}) => ({target, level, types: [...types]}));
  const source = key => {
    const p = policy.policies[key];
    if (!p) return h('p', {class: 'small-print', text: `No ${POLARIS_TYPES[key]} policy yet: saving creates one and attaches it to the database.`});
    return h('p', {class: 'small-print'}, p.managed ? `Polaris policy ${p.name}, version ${p.version}.`
      : `Set by another tool: ${p.namespace}.${p.name}, version ${p.version}. Saving updates it in place and keeps its other settings.`,
      p.problem ? h('span', {class: 'error-text', text: ` Not applied: ${p.problem}`}) : null);
  };
  const op = (key, text, fields) => {
    const section = h('div', {class: `op ${s[key].enabled ? '' : 'off'}`},
      h('div', {class: 'op-head'}, h('div', {}, h('h3', {text: TYPES[key]}), h('p', {text}), source(key)),
        sw(s[key].enabled, TYPES[key], locked, on => { s[key].enabled = on; section.classList.toggle('off', !on); update(); })),
      fields && h('div', {class: 'form-grid'}, fields));
    return section;
  };
  const set = (key, name) => value => { s[key][name] = value; update(); };
  const sizes = mb => mb.map(m => [m * MB, m >= 1024 ? `${m / 1024} GB` : `${m} MB`]);
  const sections = [
    op('dataCompaction', 'Rewrites small files, and files with many deleted rows, into fewer, larger ones per partition. Sorted when the table has a sort order. Rows don\'t change; the old files leave with snapshot expiry.', [
      field('Target file size', select(s.dataCompaction.targetFileSizeBytes, sizes([64, 128, 256, 512]), 'Target file size', locked, set('dataCompaction', 'targetFileSizeBytes')), 'files below 75% of it count as small'),
      field('At least', numberInput(s.dataCompaction.minInputFiles, 'Minimum input files', 1, 1000, locked, set('dataCompaction', 'minInputFiles')), 'input files per group'),
      field('Deleted rows', select(Math.round(s.dataCompaction.deleteRatioThreshold * 100), [[10, '10%'], [20, '20%'], [30, '30%'], [50, '50%']], 'Deleted rows threshold', locked, v => { s.dataCompaction.deleteRatioThreshold = v / 100; update(); }), 'rewrites a file whose deletes reach this'),
      field('Per table and run', select(s.dataCompaction.maxInputBytesPerRun, sizes([512, 1024, 2048, 8192]), 'Input per table and run', locked, set('dataCompaction', 'maxInputBytesPerRun')), 'the rest waits for the next run'),
      field('Dangling deletes', sw(s.dataCompaction.removeDanglingDeletes, 'Remove dangling delete files', locked, set('dataCompaction', 'removeDanglingDeletes')), 'drops delete files no data file needs any more')]),
    op('metadataCompaction', 'Merges small manifests so planning a query reads less metadata.', [
      field('When more than', numberInput(s.metadataCompaction.minCountToMerge, 'Manifest threshold', 2, 100000, locked, set('metadataCompaction', 'minCountToMerge')), 'manifests in the current snapshot')]),
    op('orphanFileRemoval', 'Deletes files under a table\'s location that no snapshot references: leftovers of failed writes and the files of expired snapshots. Runs before expiry, so what one run expires stays until the next run.', [
      field('Older than', select(s.orphanFileRemoval.maxOrphanFileAgeInDays, [[1, '1 day'], [3, '3 days'], [7, '7 days'], [14, '14 days']], 'Remove orphan files older than', locked, set('orphanFileRemoval', 'maxOrphanFileAgeInDays')), 'never younger than a day, so running writes are safe')]),
    op('snapshotExpiry', 'Removes old snapshots from each table\'s history. Time travel to an expired snapshot stops working. Branch and tag heads, their own retention and the table properties history.expire.* always win.', [
      field('Older than', select(s.snapshotExpiry.maxSnapshotAgeDays, [[1, '1 day'], [2, '2 days'], [5, '5 days'], [7, '7 days'], [30, '30 days'], [90, '90 days']], 'Expire snapshots older than', locked, set('snapshotExpiry', 'maxSnapshotAgeDays')), 'the time travel window'),
      field('Always keep the last', numberInput(s.snapshotExpiry.minSnapshotsToKeep, 'Snapshots to keep', 1, 10000, locked, set('snapshotExpiry', 'minSnapshotsToKeep')), 'snapshots, however old')]),
  ];
  // Exclusions: a disabled policy per type, attached to a namespace or a table, which overrides the database's.
  const known = tables.map(t => t.name);
  const namespaces = [...new Set(known.map(n => n.split('.').slice(0, -1).join('.')))].sort();
  const rows = h('div', {class: 'chips'});
  const drawRows = () => fill(rows, exclusions.length ? exclusions.map(e => h('span', {class: 'chip'}, exclusionText(e),
    !locked && h('button', {type: 'button', 'aria-label': `Stop excluding ${e.target}`, text: '×', onclick: () => { exclusions = exclusions.filter(x => x !== e); drawRows(); update(); }})))
    : h('span', {class: 'small-print', text: 'Nothing is excluded.'}));
  drawRows();
  const addInput = h('input', {class: 'inline', list: 'known-targets', placeholder: 'namespace or namespace.table', 'aria-label': 'Namespace or table to exclude', disabled: locked});
  const checks = Object.entries(TYPES).map(([key, title]) => h('label', {class: 'check'}, h('input', {type: 'checkbox', value: key, checked: true, disabled: locked}), title));
  const excludeSection = h('div', {class: 'op'}, h('h3', {text: 'Exclusions'}),
    h('p', {class: 'small', text: `Switch policy types off for a namespace or a table. Maintenance still reads their health. ${known.length} tables were seen in the last run.`}), rows,
    !locked && h('form', {class: 'stack', onsubmit: event => {
      event.preventDefault();
      const target = addInput.value.trim();
      const types = checks.map(c => c.querySelector('input')).filter(i => i.checked).map(i => i.value);
      if (!target || !types.length) return;
      const level = known.includes(target) ? 'table' : namespaces.includes(target) || !target.includes('.') ? 'namespace' : 'table';
      exclusions = [...exclusions.filter(e => !(e.target === target && e.level === level)), {target, level, types}]
        .sort((a, b) => a.target.localeCompare(b.target));
      addInput.value = '';
      drawRows(); update();
    }}, h('div', {class: 'cron-row'}, addInput, h('button', {type: 'submit', class: 'quiet small', text: 'Exclude'})),
      h('div', {class: 'presets', role: 'group', 'aria-label': 'Policy types to switch off'}, checks)),
    h('datalist', {id: 'known-targets'}, [...namespaces, ...known].map(n => h('option', {value: n}))));
  // Estimate from the last inspection
  const estimate = h('ul', {});
  function update() {
    const excluded = name => exclusions.some(e => e.types.length === Object.keys(TYPES).length && (e.target === name || name.startsWith(`${e.target}.`)));
    const live = tables.filter(t => !excluded(t.name) && t.health);
    const sum = key => live.reduce((n, t) => n + (t.health[key] || 0), 0);
    const c = s.dataCompaction, small = c.targetFileSizeBytes * 0.75;
    let files = 0, inputBytes = 0;
    for (const t of live) {
      // Small files only: a rough view; the dry run plans exactly.
      const smallFiles = t.health.smallFiles || 0;
      if (smallFiles >= c.minInputFiles) { files += smallFiles; inputBytes += Math.min(smallFiles * small, c.maxInputBytesPerRun); }
    }
    const row = (label, value, on) => h('li', {}, h('span', {text: label}), on ? h('b', {text: value}) : h('span', {class: 'muted', text: 'off'}));
    fill(estimate,
      row('Compact', files ? `${number.format(files)} small files · ${bytes(inputBytes)}` : 'nothing yet', c.enabled),
      row('Delete files', number.format(sum('positionDeleteFiles') + sum('equalityDeleteFiles') + sum('deletionVectors')), c.enabled),
      row('Manifests above threshold', `${live.filter(t => t.health.manifests > s.metadataCompaction.minCountToMerge).length} tables`, s.metadataCompaction.enabled),
      row('Orphan files', `older than ${days(s.orphanFileRemoval.maxOrphanFileAgeInDays)}`, s.orphanFileRemoval.enabled),
      row('Expire snapshots', `${number.format(sum('expirableSnapshots'))} at the last check`, s.snapshotExpiry.enabled));
  }
  update();
  const save = async () => {
    // The versions read: Polaris refuses a type someone changed since. The schedule is set on the database's page.
    const expectedVersions = Object.fromEntries(Object.entries(policy.policies).map(([key, p]) => [key, p ? p.version : null]));
    try {
      await send(`/databases/${d.id}/policy`, 'PATCH', {settings: s, exclusions, expectedVersions});
      go(`#/databases/${d.id}`, [policy.exists ? 'Policy saved.' : 'Policy created. Set a schedule to maintain this database regularly.']);
    } catch (error) {
      failure(error.code === 'revision_conflict' ? Object.assign(error, {message: `${error.message} Reload the page to see the changes.`}) : error);
    }
  };
  const remove = async () => {
    if (!await confirmDialog(`Remove the policy of ${d.name}?`, 'Its Polaris policies are detached from the database, the ones Maintenance created are deleted with its exclusions, and the schedule goes too. Policies other tools attached to namespaces or tables stay. Table health and past runs stay.', 'Remove policy', true)) return;
    try { await send(`/databases/${d.id}/policy`, 'DELETE'); go(`#/databases/${d.id}`, ['Policy removed.']); } catch (error) { failure(error); }
  };
  const enabled = enabledIn(d.team).includes(d.environment);
  fill($('#content'),
    heading(`${d.teamName} · ${LABELS[d.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: `${d.name} · policy`}),
      admin ? 'Applies to every table in the database, except where an exclusion or a policy on a namespace or table overrides it.' : 'Only a team administrator changes the policy.',
      isWriter(d.role) && h('button', {type: 'button', class: 'quiet', text: 'Dry run', disabled: !enabled, title: enabled ? 'Plans the saved policies' : 'Maintenance is not enabled in this environment.', onclick: () => startRun(d, 'dry-run')}),
      admin && policy.exists && h('button', {type: 'button', class: 'danger', text: 'Remove', onclick: remove}),
      admin && h('button', {type: 'button', text: policy.exists ? 'Save policy' : 'Create policy', onclick: save})),
    !policy.exists && h('div', {class: 'notice', text: 'No policy yet. These are the defaults, as in Apache Iceberg and Amazon S3 Tables. Saving creates one Apache Polaris policy per type and attaches it to the database.'}),
    h('div', {class: 'columns'},
      h('section', {class: 'card'}, sections, excludeSection),
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('h2', {text: 'Schedule'}),
          policy.schedule ? scheduleFacts(policy.schedule) : h('p', {class: 'small', text: 'Not scheduled. Maintenance runs only when someone starts it.'}),
          h('a', {class: 'button quiet small', href: `#/databases/${d.id}`, text: !admin || !policy.exists ? 'Back to database' : policy.schedule ? 'Change the schedule' : 'Set the schedule'})),
        h('section', {class: 'card estimate'}, h('div', {class: 'section-title'}, h('h2', {text: 'From the last check'}),
          h('span', {class: 'small-print', text: tables.length ? 'estimated' : 'no health yet'})), estimate,
          h('p', {class: 'small-print', text: 'A dry run plans the saved policies exactly and changes nothing.'})),
        h('section', {class: 'card agent-hint'}, h('h2', {text: 'How a run behaves'}),
          h('ul', {class: 'plain'},
            h('li', {text: `Runs as ${d.teamName}'s ${LABELS[d.environment]} service account, in its own container`}),
            h('li', {text: 'Asks Polaris which policies apply to each table, so policies other tools attached count too'}),
            h('li', {text: 'Order: compaction, dangling deletes, manifests, orphan files, then snapshot expiry'}),
            h('li', {text: 'Never expires branch or tag heads; tables with gc.enabled=false keep their history'}),
            h('li', {text: 'Files of expired snapshots are deleted by orphan cleanup in a later run'}),
            h('li', {text: 'A commit that conflicts with a writer is planned and tried again; if it keeps conflicting, that table fails and the next run tries again'}),
            h('li', {text: 'Ends 15 minutes before its catalog token expires'}))))));
}

// One table
async function renderTable(token) {
  const t = await api(`/databases/${route.id}/tables/${encodeURIComponent(route.table)}`);
  if (token !== renderToken) return;
  crumbs([`${teamName(t.team)} · ${t.databaseName}`, `#/databases/${t.database}`], [t.name]);
  const health = t.health || {}, d = {id: t.database, name: t.databaseName, team: t.team, environment: t.environment};
  const writer = isWriter(t.role), enabled = enabledIn(t.team).includes(t.environment);
  const [namespace, ...rest] = t.name.split('.');
  fill($('#content'),
    heading(`${t.databaseName} · ${namespace} · ${LABELS[t.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: rest.join('.') || t.name}),
      health.producer ? `Produced by ${health.producer}.` : null,
      writer && h('button', {type: 'button', class: 'quiet', text: 'Check health', disabled: !enabled, onclick: () => startRun(d, 'inspect', [t.name])}),
      writer && h('button', {type: 'button', class: 'quiet', text: 'Dry run', disabled: !enabled, onclick: () => startRun(d, 'dry-run', [t.name])}),
      writer && t.state !== 'no-policy' && h('button', {type: 'button', text: 'Maintain table', disabled: !enabled || t.state === 'excluded', onclick: () => startRun(d, 'maintain', [t.name])})),
    h('div', {class: 'status-line spaced'}, stateChip(t.state), h('span', {class: 'small-print'}, 'Seen ', time(t.inspectedAt), ` by a ${SEEN_BY[t.inspectedBy] || t.inspectedBy}`)),
    t.reasons.length ? h('div', {class: 'notice warn', text: `Needs attention: ${t.reasons.join(', ')}.`}) : null,
    t.lastError ? h('div', {class: 'notice error', text: t.lastError}) : null,
    h('div', {class: 'columns'},
      h('div', {class: 'stack'}, sizesCard(health), snapshotsCard(health), lastActionsCard(t),
        h('section', {class: 'card'}, h('h2', {text: 'Recent runs'}), runsTable(t.recentRuns))),
      h('div', {class: 'stack'}, tableFacts(health),
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Policies that applied'}),
          t.state === 'excluded' ? chip('idle', 'Excluded') : t.state === 'no-policy' ? chip('idle', 'None') : h('span', {class: 'small-print', text: 'at the last run'})),
          appliedFacts(t.policies),
          h('p', {class: 'small-print', text: 'Polaris resolves them per table: a policy on the table wins over its namespace\'s, which wins over the database\'s.'}),
          Object.keys(health.expiryOverrides || {}).length ? h('p', {class: 'small-print', text: `Table properties override the expiry: ${Object.entries(health.expiryOverrides).map(([k, v]) => `${k}=${v}`).join(', ')}`}) : null,
          h('a', {class: 'button quiet small', href: `#/databases/${t.database}/policy`, text: 'Open policy'})))));
}

function sizesCard(health) {
  const counts = health.sizeHistogram || [];
  const max = Math.max(1, ...counts);
  const threshold = health.smallFileThreshold || 0;
  const bounds = [1, 4, 16, 64, 128, 256].map(m => m * 1048576);
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'File sizes'}), h('span', {class: 'small-print', text: `${number.format(health.dataFiles || 0)} data files · ${bytes(health.dataBytes || 0)}`})),
    h('div', {class: 'hist', role: 'img', 'aria-label': `Data files per size: ${BUCKETS.map((b, i) => `${b}: ${counts[i] || 0}`).join(', ')}`},
      BUCKETS.map((label, i) => h('div', {title: `${counts[i] || 0} files`},
        h('i', {class: (i === 0 ? 0 : bounds[i - 1]) < threshold ? 'small' : '', style: `height:${Math.round(((counts[i] || 0) / max) * 100)}%`}),
        h('span', {text: label})))),
    h('div', {class: 'legend'}, h('span', {}, h('i', {style: 'background:var(--warn)'}), `Below the small-file size (${bytes(threshold)})`),
      h('span', {}, h('i', {style: 'background:var(--border-visible)'}), 'A good size')),
    health.compactableFiles ? h('p', {class: 'small-print', text: `Compaction would rewrite ${number.format(health.compactableFiles)} files (${bytes(health.compactableBytes)}) into about ${number.format(health.estimatedFilesAfter)}.`}) : null);
}

function snapshotsCard(health) {
  const days = health.snapshotDays || [];
  const max = Math.max(1, ...days);
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Snapshots'}), h('span', {class: 'small-print', text: `${number.format(health.snapshots || 0)} snapshots · oldest ${age(health.oldestSnapshotMs)}`})),
    h('div', {class: 'timeline', role: 'img', 'aria-label': `Snapshots per day over the last 14 days: ${days.join(', ')}`},
      days.map((n, i) => h('i', {title: `${n} snapshots, ${13 - i} days ago`, style: `height:${Math.round((n / max) * 100)}%;${n ? 'background:var(--text-secondary)' : ''}`}))),
    h('div', {class: 'legend'}, h('span', {text: '14 days ago'}), h('span', {style: 'margin-left:auto', text: 'today'})),
    h('dl', {class: 'facts'},
      h('dt', {text: 'To expire'}), h('dd', {text: number.format(health.expirableSnapshots || 0)}),
      h('dt', {text: 'Branches'}), h('dd', {text: number.format(health.branches || 0)}),
      h('dt', {text: 'Tags'}), h('dd', {text: (health.tags || []).join(', ') || '—'})),
    h('p', {class: 'small-print', text: 'Branch and tag heads are never expired. They stay until a team member removes the branch or tag.'}));
}

function actionsList(actions) {
  if (!actions?.length) return h('p', {class: 'small muted', text: 'No actions.'});
  return h('ul', {class: 'actions-list'}, actions.map(a => h('li', {},
    h('span', {text: OPERATIONS[a.operation] || a.operation}), chip(ACTION_CLASS[a.status] || 'idle', a.status),
    a.detail ? h('p', {text: a.detail}) : null,
    a.policy ? h('p', {class: 'small-print', text: `Policy ${a.policy.namespace}.${a.policy.name} (version ${a.policy.version}), on ${ATTACHED[a.policy.appliedAt]}`}) : null)));
}

function lastActionsCard(t) {
  return h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Last run on this table'}),
    t.lastRun && h('a', {class: 'small-print', href: `#/runs/${t.lastRun}`, text: 'Open run'})),
    t.lastActions.length ? actionsList(t.lastActions) : h('p', {class: 'small muted', text: 'Health checks read the table only. A dry run or maintenance lists its actions here.'}),
    t.lastMaintainedAt ? h('p', {class: 'small-print'}, 'Last maintained ', time(t.lastMaintainedAt)) : null);
}

function tableFacts(health) {
  return h('section', {class: 'card'}, h('h2', {text: 'Table'}), h('dl', {class: 'facts'},
    h('dt', {text: 'Format'}), h('dd', {text: `Iceberg v${health.formatVersion || '?'}`}),
    h('dt', {text: 'Partition fields'}), h('dd', {text: String(health.partitionFields ?? '—')}),
    h('dt', {text: 'Sort order'}), h('dd', {text: health.sortOrder ? 'yes: compaction sorts' : 'none: compaction bin-packs'}),
    h('dt', {text: 'Rows'}), h('dd', {text: number.format(health.records || 0)}),
    h('dt', {text: 'Current snapshot'}), h('dd', {text: health.currentSnapshotMs ? `${age(health.currentSnapshotMs)} ago` : '—'}),
    h('dt', {text: 'Manifests'}), h('dd', {text: number.format(health.manifests || 0)}),
    h('dt', {text: 'Delete files'}), h('dd', {text: `${number.format(health.positionDeleteFiles || 0)} position · ${number.format(health.equalityDeleteFiles || 0)} equality · ${number.format(health.deletionVectors || 0)} vectors`}),
    h('dt', {text: 'gc.enabled'}), h('dd', {text: health.gcEnabled === false ? 'false: history is kept' : 'true'}),
    h('dt', {text: 'Location'}), h('dd', {class: 'mono', text: health.location || '—'})));
}

// One run
async function renderRun(token) {
  const run = await api(`/runs/${route.id}`);
  const logs = await api(`/runs/${run.id}/logs?limit=2000`);
  if (token !== renderToken) return;
  crumbs([`${teamName(run.team)} · ${run.databaseName}`, `#/databases/${run.database}`], ['Run']);
  const active = ACTIVE.has(run.status), totals = run.summary?.totals || {};
  const previous = $('#run-log');
  const stick = !previous || previous.scrollTop + previous.clientHeight >= previous.scrollHeight - 24;
  const log = h('pre', {id: 'run-log', class: 'log', tabindex: '0', 'aria-label': 'Run log',
    text: logs.lines.length ? logs.lines.join('\n') : active ? 'Waiting for output…' : 'No log for this run.'});
  const stat = (label, value, sub) => h('div', {class: 'stat'}, h('span', {class: 'label', text: label}), h('span', {class: 'value', text: value}), sub && h('span', {class: 'sub', text: sub}));
  const maintain = run.mode !== 'inspect';
  const results = run.results || [];
  if (run.mode === 'dry-run') {
    // A dry run commits nothing, so its totals are what the planned actions would do.
    const planned = (operation, metric) => results.reduce((n, r) => n + r.actions.filter(a => a.operation === operation && a.status === 'planned')
      .reduce((m, a) => m + (Number(a.metrics?.[metric]) || 0), 0), 0);
    Object.assign(totals, {snapshotsExpired: planned('expireSnapshots', 'snapshots'), filesCompacted: planned('compaction', 'inputFiles'),
      filesWritten: planned('compaction', 'estimatedOutputFiles'), deleteFilesRemoved: planned('danglingDeletes', 'deleteFiles'),
      orphanFiles: planned('orphanFiles', 'files'), orphanBytes: planned('orphanFiles', 'bytes')});
  }
  fill($('#content'),
    heading(`${run.databaseName} · ${LABELS[run.environment]} · ${run.scheduled ? 'scheduled' : run.triggeredBy}`.toUpperCase(), h('h1', {class: 'compact', text: MODES[run.mode]}), null,
      active && isWriter(run.role) && h('button', {type: 'button', class: 'danger', text: 'Stop run', onclick: async event => {
        event.target.disabled = true;
        try { await send(`/runs/${run.id}/cancel`, 'POST'); go(location.hash, ['Stopping the run…']); } catch (error) { failure(error); }
      }}),
      h('a', {class: 'button quiet', href: `#/databases/${run.database}`, text: 'Back to database'})),
    h('div', {class: 'status-line spaced'}, runChip(run.status), run.mode === 'dry-run' && h('span', {class: 'chip', text: 'nothing changed'}),
      h('span', {class: 'small-print', text: `${duration(run)}${run.tables.length ? ` · ${run.tables.join(', ')}` : ''}`})),
    run.error && h('div', {class: 'notice error', text: run.error}),
    maintain && !active && h('div', {class: 'stats'},
      stat(run.mode === 'dry-run' ? 'Would expire' : 'Snapshots expired', number.format(totals.snapshotsExpired || 0)),
      stat(run.mode === 'dry-run' ? 'Would compact' : 'Files compacted', number.format(totals.filesCompacted || 0), totals.filesWritten ? `into ${number.format(totals.filesWritten)} files` : null),
      stat(run.mode === 'dry-run' ? 'Would remove' : 'Delete files removed', number.format(totals.deleteFilesRemoved || 0)),
      stat('Orphan files', number.format(totals.orphanFiles || 0), totals.orphanBytes ? bytes(totals.orphanBytes) : null)),
    h('div', {class: 'columns'},
      h('div', {class: 'stack'},
        h('section', {class: 'card table-wrap'}, h('div', {class: 'section-title'}, h('h2', {text: 'Per table'}),
          h('span', {class: 'small-print', text: `${number.format(totals.tables || results.length)} tables${totals.failed ? ` · ${totals.failed} failed` : ''}`})),
          results.length ? h('table', {class: 'responsive'},
            h('thead', {}, h('tr', {}, h('th', {scope: 'col', text: 'Table'}), h('th', {scope: 'col', text: 'Status'}), h('th', {scope: 'col', text: maintain ? 'Actions' : 'Health'}))),
            h('tbody', {}, results.map(r => h('tr', {},
              h('td', {class: 'name-cell wide'}, h('a', {href: tableHref({database: run.database, name: r.name}), text: r.name}), r.error && h('p', {class: 'error-text', text: r.error})),
              h('td', {}, chip(r.status === 'success' ? 'success' : r.status === 'skipped' ? 'idle' : 'failed', r.status)),
              h('td', {class: 'wide'}, maintain ? actionsList(r.actions.filter(a => a.status !== 'off')) : h('span', {class: 'small muted', text: 'Health read'}))))))
            : h('p', {class: 'small muted', text: active ? 'Tables appear when the run finishes.' : 'No tables.'})),
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Log'}),
          h('span', {class: 'small-print', text: active ? 'Updates every 2 seconds' : `runner ${run.summary?.runnerVersion || '—'} · iceberg-go ${run.summary?.icebergGo || '—'}`})), log)),
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('dl', {class: 'facts'},
          h('dt', {text: 'Kind'}), h('dd', {text: MODES[run.mode]}),
          h('dt', {text: 'Queued'}), h('dd', {}, time(run.createdAt)),
          h('dt', {text: 'Started'}), h('dd', {}, time(run.startedAt)),
          h('dt', {text: 'Finished'}), h('dd', {}, active ? '—' : time(run.finishedAt)),
          h('dt', {text: 'Started by'}), h('dd', {}, run.scheduled ? 'schedule' : run.triggeredBy, run.agent && h('span', {class: 'agent', text: run.agent})),
          run.exitCode !== null && run.exitCode !== undefined && h('dt', {text: 'Exit code'}),
          run.exitCode !== null && run.exitCode !== undefined && h('dd', {text: String(run.exitCode)}))),
        h('section', {class: 'card'}, h('h2', {text: 'What a run changes'}),
          h('p', {class: 'small', text: run.mode === 'maintain' ? 'Compaction and manifest rewrites add snapshots that keep every row. Expiry removes snapshots older than the policy\'s age; their files go with a later orphan cleanup. A commit that conflicts with another writer is planned and tried again.' : 'Nothing: health checks and dry runs only read the catalog and storage, with a read-only token.'})))));
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
  const result = await (await fetch('/auth/logout', {method: 'POST', headers: {'X-Iceberg-Maintenance': '1'}, credentials: 'same-origin'})).json();
  location.href = result.logoutUrl || '/';
};
window.addEventListener('hashchange', () => render());
theme();
render();
