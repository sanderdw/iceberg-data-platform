'use strict';
// The Maintenance page over the REST API: table health of your teams' databases, their policies and runs.
// Agents do the same through the MCP server; the server checks every permission again.
const $ = selector => document.querySelector(selector);
const ENVIRONMENTS = ['development', 'acceptance', 'production'];
const LABELS = {development: 'Development', acceptance: 'Acceptance', production: 'Production'};
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
const ACTIVE = new Set(['queued', 'running']);
const STATES = {healthy: 'Healthy', attention: 'Needs attention', failed: 'Failed', 'no-policy': 'No policy', excluded: 'Excluded'};
const STATE_CLASS = {healthy: 'healthy', attention: 'attention', failed: 'failed', 'no-policy': 'idle', excluded: 'idle'};
const MODES = {inspect: 'Health check', 'dry-run': 'Dry run', maintain: 'Maintenance'};
const OPERATIONS = {
  compaction: 'Compact data files', danglingDeletes: 'Remove dangling deletes', rewriteManifests: 'Rewrite manifests',
  orphanFiles: 'Remove orphan files', expireSnapshots: 'Expire snapshots', excluded: 'Excluded', deadline: 'Time limit',
};
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
function age(ms) {
  if (!ms) return '—';
  const hours = (Date.now() - ms) / 3600000;
  return hours < 1 ? `${Math.max(1, Math.round(hours * 60))} min` : hours < 48 ? `${Math.round(hours)} h` : `${Math.round(hours / 24)} d`;
}
function duration(run) {
  if (!run.startedAt) return '—';
  const s = Math.max(0, Math.round((run.finishedAt || Date.now() / 1000) - run.startedAt));
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
function hours(value) {
  return value % 24 === 0 ? (value === 24 ? '1 day' : `${value / 24} days`) : `${value} hours`;
}
function chip(status, label) { return h('span', {class: `chip ${status}`}, h('i', {class: `status ${status}`, 'aria-hidden': 'true'}), label || status); }
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
  const [databases, tables] = await Promise.all([api(`/databases?${query}`), api(`/tables?${query}`)]);
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
      h('button', {type: 'button', 'aria-pressed': String(!route.attention), text: `All ${tables.length}`, onclick: () => setFilter({attention: ''})}),
      h('button', {type: 'button', 'aria-pressed': String(route.attention), text: `Needs attention ${needs.length}`, onclick: () => setFilter({attention: '1'})})));
  fill($('#content'),
    heading('MAINTENANCE', h('h1', {}, 'Table health', h('span', {class: 'dot-accent', text: '.'})),
      'Snapshots, small files and delete files in your teams\' Iceberg databases, and the policies that keep them in check. Runs act as each team\'s service account.'),
    filters, statsRow(databases, tables, needs),
    tables.length ? tableList(shown) : emptyState(databases),
    databasesCard(databases),
    h('div', {class: 'columns spaced-top'}, environmentsCard(), agentCard()));
  if (runsBusy) schedulePoll(() => render(true), 4000);
}

function statsRow(databases, tables, needs) {
  const runs = databases.map(d => d.lastRun).filter(Boolean);
  const expired = runs.reduce((sum, r) => sum + (r.summary?.totals?.snapshotsExpired || 0), 0);
  const compacted = runs.reduce((sum, r) => sum + (r.summary?.totals?.filesCompacted || 0), 0);
  const policies = databases.filter(d => d.policy).length;
  return h('div', {class: 'stats'},
    h('div', {class: 'stat'}, h('span', {class: 'label', text: 'Tables'}), h('span', {class: 'value', text: number.format(tables.length)}),
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
    h('div', {class: 'section-title'}, h('h2', {text: 'Databases'}), h('span', {class: 'small-print', text: 'One policy per database'})),
    h('table', {class: 'responsive'},
      h('thead', {}, h('tr', {}, ['Database', 'Policy', 'Schedule (UTC)', 'Tables', 'Last run'].map((t, i) => h('th', {scope: 'col', class: i === 3 ? 'num' : null, text: t})))),
      h('tbody', {}, databases.map(d => h('tr', {},
        h('td', {class: 'name-cell wide'}, h('a', {href: `#/databases/${d.id}`, text: d.name}), h('p', {text: `${d.teamName} · ${LABELS[d.environment]}`})),
        h('td', {}, d.policy ? chip('success', 'Policy') : chip('idle', 'Defaults')),
        h('td', {class: 'mono'}, d.schedule ? (d.schedule.enabled ? d.schedule.cron : `${d.schedule.cron} (paused)`) : h('span', {class: 'muted', text: '—'})),
        h('td', {class: 'num'}, number.format(d.tables), d.attention ? h('div', {class: 'reasons', text: `${d.attention} need attention`}) : null),
        h('td', {class: 'wide'}, d.lastRun ? h('div', {class: 'run-cell'}, h('a', {href: `#/runs/${d.lastRun.id}`}, chip(d.lastRun.status)),
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
  const [databases, tables, policy, runs] = await Promise.all([api('/databases'), api(`/tables?database=${route.id}`),
    api(`/databases/${route.id}/policy`), api(`/databases/${route.id}/runs?limit=10`)]);
  if (token !== renderToken) return;
  const d = databases.find(x => x.id === route.id);
  if (!d) throw new Error('No such database in your teams.');
  crumbs([`${d.teamName} · ${d.name}`]);
  const active = runs.find(r => ACTIVE.has(r.status));
  const enabled = enabledIn(d.team).includes(d.environment);
  const blocked = !enabled ? 'Maintenance is not enabled in this environment.' : active ? 'A run is in progress.' : '';
  const writer = isWriter(d.role);
  const button = (mode, label, cls) => writer && h('button', {type: 'button', class: cls, text: label, disabled: Boolean(blocked) || (mode === 'maintain' && !policy.exists),
    title: blocked || (mode === 'maintain' && !policy.exists ? 'Set a policy first.' : null), onclick: () => startRun(d, mode)});
  fill($('#content'),
    heading(`${d.teamName} · ${LABELS[d.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: d.name}), null,
      button('inspect', 'Check health', 'quiet'), button('dry-run', 'Dry run', 'quiet'), button('maintain', 'Run maintenance'),
      h('a', {class: 'button quiet', href: `#/databases/${d.id}/policy`, text: isAdmin(d.role) ? 'Edit policy' : 'Policy'})),
    h('div', {class: 'status-line spaced'}, policy.exists ? chip('success', 'Policy') : chip('idle', 'No policy: defaults'),
      policy.schedule && h('span', {class: 'chip', text: policy.schedule.enabled ? `Every ${policy.schedule.cron} UTC` : 'Schedule paused'}),
      blocked && !active ? h('span', {class: 'small-print', text: blocked}) : null),
    tables.length ? tableList(tables, false) : h('section', {class: 'card'},
      h('h2', {text: 'Tables'}), h('p', {class: 'small', text: 'No health yet. Check health reads every table and changes nothing.'})),
    h('div', {class: 'columns spaced-top'},
      h('div', {class: 'stack'},
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Runs'}), h('span', {class: 'small-print', text: 'Newest first'})), runsTable(runs))),
      h('div', {class: 'stack'}, policySummary(policy, d), scheduleSummary(policy))));
  if (active) schedulePoll(() => render(true), 3000);
}

function runsTable(runs) {
  if (!runs.length) return h('p', {class: 'muted small', text: 'No runs yet.'});
  return h('div', {class: 'table-wrap'}, h('table', {class: 'responsive'},
    h('thead', {}, h('tr', {}, ['Status', 'Kind', 'Started', 'Duration', 'By'].map(t => h('th', {scope: 'col', text: t})))),
    h('tbody', {}, runs.map(r => h('tr', {class: 'run-row', onclick: event => { if (!event.target.closest('a')) location.hash = `#/runs/${r.id}`; }},
      h('td', {}, h('a', {href: `#/runs/${r.id}`, 'aria-label': `Run ${r.status}, ${when(r.createdAt)}`}, chip(r.status))),
      h('td', {class: 'small', text: MODES[r.mode] + (r.tables.length ? ` · ${r.tables.length} tables` : '')}),
      h('td', {class: 'nowrap'}, time(r.startedAt || r.createdAt)),
      h('td', {class: 'nowrap mono', text: duration(r)}),
      h('td', {class: 'wide small'}, r.schedule ? 'schedule' : r.triggeredBy, r.agent && h('span', {class: 'agent', text: 'agent'})))))));
}

function policyFacts(s) {
  const fact = (on, text) => on ? text : 'off';
  return h('dl', {class: 'facts'},
    h('dt', {text: 'Expire snapshots'}), h('dd', {text: fact(s.expireSnapshots.enabled, `older than ${hours(s.expireSnapshots.olderThanHours)} · keep last ${s.expireSnapshots.retainLast}`)}),
    h('dt', {text: 'Compact'}), h('dd', {text: fact(s.compaction.enabled, `${s.compaction.targetFileSizeMb} MB files · ≥ ${s.compaction.minInputFiles} inputs · ${bytes(s.compaction.maxInputMbPerRun * 1048576)} per table and run`)}),
    h('dt', {text: 'Dangling deletes'}), h('dd', {text: fact(s.danglingDeletes.enabled, 'removed')}),
    h('dt', {text: 'Manifests'}), h('dd', {text: fact(s.rewriteManifests.enabled, `merged above ${s.rewriteManifests.minManifests}`)}),
    h('dt', {text: 'Orphan files'}), h('dd', {text: fact(s.orphanFiles.enabled, `older than ${hours(s.orphanFiles.olderThanHours)}`)}));
}

function policySummary(policy, d) {
  return h('section', {class: 'card'},
    h('div', {class: 'section-title'}, h('h2', {text: 'Policy'}), policy.exists ? h('span', {class: 'small-print', text: `Revision ${policy.revision}`}) : chip('idle', 'Defaults')),
    policyFacts(policy.settings),
    policy.exclude.length ? h('p', {class: 'small-print', text: `Excluded: ${policy.exclude.join(', ')}`}) : null,
    policy.exists ? h('p', {class: 'small-print'}, `Changed by ${policy.updatedBy}, `, time(policy.updatedAt))
      : h('p', {class: 'small-print', text: 'No policy yet: health checks and dry runs use these defaults. Maintenance and schedules need a policy.'}),
    h('a', {class: 'button quiet small', href: `#/databases/${d.id}/policy`, text: isAdmin(d.role) ? (policy.exists ? 'Edit policy' : 'Create policy') : 'Open policy'}));
}

function scheduleSummary(policy) {
  const s = policy.schedule;
  return h('section', {class: 'card'}, h('h2', {text: 'Schedule'}),
    s ? h('dl', {class: 'facts'},
      h('dt', {text: 'Cron (UTC)'}), h('dd', {text: s.cron + (s.enabled ? '' : ' · paused')}),
      h('dt', {text: 'Next run'}), h('dd', {}, s.enabled ? time(s.nextRunAt) : '—'))
      : h('p', {class: 'small', text: 'Not scheduled. Maintenance runs only when someone starts it.'}),
    h('p', {class: 'small-print', text: 'A scheduled run applies the policy to every table that is not excluded, and is skipped while another run of the database is in progress.'}));
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
  const [databases, tables, policy] = await Promise.all([api('/databases'), api(`/tables?database=${route.id}`), api(`/databases/${route.id}/policy`)]);
  if (token !== renderToken) return;
  const d = databases.find(x => x.id === route.id);
  if (!d) throw new Error('No such database in your teams.');
  crumbs([`${d.teamName} · ${d.name}`, `#/databases/${d.id}`], ['Policy']);
  const admin = isAdmin(d.role), locked = !admin;
  const s = structuredClone(policy.settings);
  let exclude = [...policy.exclude];
  const ops = [];
  const op = (key, title, text, fields) => {
    const section = h('div', {class: `op ${s[key].enabled ? '' : 'off'}`},
      h('div', {class: 'op-head'}, h('div', {}, h('h3', {text: title}), h('p', {text})),
        sw(s[key].enabled, title, locked, on => { s[key].enabled = on; section.classList.toggle('off', !on); update(); })),
      fields && h('div', {class: 'form-grid'}, fields));
    ops.push(section);
    return section;
  };
  const set = (key, name) => value => { s[key][name] = value; update(); };
  const DAYS = [[24, '1 day'], [48, '2 days'], [120, '5 days'], [168, '7 days'], [720, '30 days'], [2160, '90 days']];
  const sections = [
    op('expireSnapshots', 'Expire snapshots', 'Removes old snapshots from each table\'s history. Time travel to an expired snapshot stops working. Branch and tag heads, their own retention and the table properties history.expire.* always win.', [
      field('Older than', select(s.expireSnapshots.olderThanHours, DAYS, 'Expire snapshots older than', locked, set('expireSnapshots', 'olderThanHours')), 'The time travel window'),
      field('Always keep the last', numberInput(s.expireSnapshots.retainLast, 'Snapshots to keep', 1, 10000, locked, set('expireSnapshots', 'retainLast')), 'snapshots, however old')]),
    op('compaction', 'Compact data files', 'Rewrites small files, and files with many deleted rows, into fewer, larger ones per partition. Sorted when the table has a sort order. Rows don\'t change; the old files leave with snapshot expiry.', [
      field('Target file size', select(s.compaction.targetFileSizeMb, [[64, '64 MB'], [128, '128 MB'], [256, '256 MB'], [512, '512 MB']], 'Target file size', locked, set('compaction', 'targetFileSizeMb')), 'files below 75% of it count as small'),
      field('At least', numberInput(s.compaction.minInputFiles, 'Minimum input files', 1, 1000, locked, set('compaction', 'minInputFiles')), 'input files per group'),
      field('Deleted rows', select(Math.round(s.compaction.deleteRatio * 100), [[10, '10%'], [20, '20%'], [30, '30%'], [50, '50%']], 'Deleted rows threshold', locked, v => { s.compaction.deleteRatio = v / 100; update(); }), 'rewrites a file whose deletes reach this'),
      field('Per table and run', select(s.compaction.maxInputMbPerRun, [[512, '512 MB'], [1024, '1 GB'], [2048, '2 GB'], [8192, '8 GB']], 'Input per table and run', locked, set('compaction', 'maxInputMbPerRun')), 'the rest waits for the next run')]),
    op('danglingDeletes', 'Remove dangling deletes', 'Drops delete files that no live data file needs any more, for example after compaction. Metadata only.'),
    op('rewriteManifests', 'Rewrite manifests', 'Merges small manifests so planning a query reads less metadata.', [
      field('When more than', numberInput(s.rewriteManifests.minManifests, 'Manifest threshold', 2, 100000, locked, set('rewriteManifests', 'minManifests')), 'manifests in the current snapshot')]),
    op('orphanFiles', 'Remove orphan files', 'Deletes files under a table\'s location that no snapshot references: leftovers of failed writes and the files of expired snapshots. Runs before expiry, so what one run expires stays until the next run.', [
      field('Older than', select(s.orphanFiles.olderThanHours, [[24, '1 day'], [72, '3 days'], [168, '7 days'], [336, '14 days']], 'Remove orphan files older than', locked, set('orphanFiles', 'olderThanHours')), 'never younger than a day, so running writes are safe')]),
  ];
  // Schedule
  const cron = h('input', {class: 'inline', value: policy.schedule?.cron || '', placeholder: '0 2 * * *', 'aria-label': 'Cron expression in UTC', autocomplete: 'off', spellcheck: 'false', disabled: locked});
  const scheduleOn = h('input', {type: 'checkbox', checked: policy.schedule ? policy.schedule.enabled : true, disabled: locked});
  const schedule = h('div', {class: 'op'}, h('h3', {text: 'Schedule'}),
    h('div', {class: 'cron-row'}, cron, h('label', {class: 'check'}, scheduleOn, 'Enabled')),
    !locked && h('div', {class: 'presets', role: 'group', 'aria-label': 'Presets'}, [...CRON_PRESETS, ['Off', '']].map(([label, value]) =>
      h('button', {type: 'button', class: 'quiet', text: label, onclick: () => { cron.value = value; cron.focus(); }}))),
    h('p', {class: 'small-print', text: 'Five fields in UTC, at most once an hour. Empty: no schedule.'}));
  // Excluded tables
  const known = tables.map(t => t.name);
  const chips = h('div', {class: 'chips'});
  const addInput = h('input', {class: 'inline', list: 'known-tables', placeholder: 'namespace.table', 'aria-label': 'Table to exclude', disabled: locked});
  const drawChips = () => fill(chips, exclude.length ? exclude.map(name => h('span', {class: 'chip'}, name,
    !locked && h('button', {type: 'button', 'aria-label': `Stop excluding ${name}`, text: '×', onclick: () => { exclude = exclude.filter(n => n !== name); drawChips(); }})))
    : h('span', {class: 'small-print', text: 'No table is excluded.'}));
  drawChips();
  const excludeSection = h('div', {class: 'op'}, h('h3', {text: 'Excluded tables'}),
    h('p', {class: 'small', text: `Maintenance reads their health but never changes them. ${known.length} tables were seen in the last run.`}), chips,
    !locked && h('form', {class: 'cron-row', onsubmit: event => {
      event.preventDefault();
      const name = addInput.value.trim();
      if (name && name.includes('.') && !exclude.includes(name)) { exclude = [...exclude, name].sort(); drawChips(); }
      addInput.value = '';
    }}, addInput, h('button', {type: 'submit', class: 'quiet small', text: 'Exclude'})),
    h('datalist', {id: 'known-tables'}, known.map(n => h('option', {value: n}))));
  // Estimate from the last inspection
  const estimate = h('ul', {});
  function update() {
    const live = tables.filter(t => !exclude.includes(t.name) && t.health);
    const sum = key => live.reduce((n, t) => n + (t.health[key] || 0), 0);
    const target = s.compaction.targetFileSizeMb * 1048576, small = target * 0.75;
    let files = 0, inputBytes = 0;
    for (const t of live) {
      // Small files only: a rough view; the dry run plans exactly.
      const smallFiles = t.health.smallFiles || 0;
      if (smallFiles >= s.compaction.minInputFiles) { files += smallFiles; inputBytes += Math.min(smallFiles * small, s.compaction.maxInputMbPerRun * 1048576); }
    }
    const row = (label, value, on) => h('li', {}, h('span', {text: label}), on ? h('b', {text: value}) : h('span', {class: 'muted', text: 'off'}));
    fill(estimate,
      row('Expire snapshots', `${number.format(sum('expirableSnapshots'))} at the last check`, s.expireSnapshots.enabled),
      row('Compact', files ? `${number.format(files)} small files · ${bytes(inputBytes)}` : 'nothing yet', s.compaction.enabled),
      row('Delete files', number.format(sum('positionDeleteFiles') + sum('equalityDeleteFiles') + sum('deletionVectors')), s.danglingDeletes.enabled || s.compaction.enabled),
      row('Manifests above threshold', `${live.filter(t => t.health.manifests > s.rewriteManifests.minManifests).length} tables`, s.rewriteManifests.enabled),
      row('Orphan files', `older than ${hours(s.orphanFiles.olderThanHours)}`, s.orphanFiles.enabled));
  }
  update();
  const save = async () => {
    const body = {settings: s, exclude, cron: cron.value.trim(), scheduleEnabled: scheduleOn.checked, expected_revision: policy.revision};
    try { await send(`/databases/${d.id}/policy`, 'PUT', body); go(`#/databases/${d.id}`, ['Policy saved.']); }
    catch (error) { failure(error); }
  };
  const remove = async () => {
    if (!await confirmDialog(`Remove the policy of ${d.name}?`, 'Scheduled maintenance stops. Table health and past runs stay.', 'Remove policy', true)) return;
    try { await send(`/databases/${d.id}/policy`, 'DELETE'); go(`#/databases/${d.id}`, ['Policy removed.']); } catch (error) { failure(error); }
  };
  const enabled = enabledIn(d.team).includes(d.environment);
  fill($('#content'),
    heading(`${d.teamName} · ${LABELS[d.environment]}`.toUpperCase(), h('h1', {class: 'compact', text: `${d.name} · policy`}),
      admin ? 'Applies to every table in the database except the excluded ones.' : 'Only a team administrator changes the policy.',
      isWriter(d.role) && h('button', {type: 'button', class: 'quiet', text: 'Dry run', disabled: !enabled, title: enabled ? 'Plans the saved policy, or the defaults' : 'Maintenance is not enabled in this environment.', onclick: () => startRun(d, 'dry-run')}),
      admin && policy.exists && h('button', {type: 'button', class: 'danger', text: 'Remove', onclick: remove}),
      admin && h('button', {type: 'button', text: policy.exists ? 'Save policy' : 'Create policy', onclick: save})),
    !policy.exists && h('div', {class: 'notice', text: 'No policy yet. These are the defaults, as in Apache Iceberg and Amazon S3 Tables: saving creates the policy.'}),
    h('div', {class: 'columns'},
      h('section', {class: 'card'}, sections, excludeSection, schedule),
      h('div', {class: 'stack'},
        h('section', {class: 'card estimate'}, h('div', {class: 'section-title'}, h('h2', {text: 'From the last check'}),
          h('span', {class: 'small-print', text: tables.length ? 'estimated' : 'no health yet'})), estimate,
          h('p', {class: 'small-print', text: 'A dry run plans the saved policy exactly and changes nothing.'})),
        h('section', {class: 'card agent-hint'}, h('h2', {text: 'How a run behaves'}),
          h('ul', {class: 'plain'},
            h('li', {text: `Runs as ${d.teamName}'s ${LABELS[d.environment]} service account, in its own container`}),
            h('li', {text: 'Order: compaction, dangling deletes, manifests, orphan files, then snapshot expiry'}),
            h('li', {text: 'Never expires branch or tag heads; tables with gc.enabled=false keep their history'}),
            h('li', {text: 'Files of expired snapshots are deleted by orphan cleanup in a later run'}),
            h('li', {text: 'A commit conflict with a writer fails that table only; it is retried next run'}),
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
      writer && t.policy.exists && h('button', {type: 'button', text: 'Maintain table', disabled: !enabled || t.state === 'excluded', onclick: () => startRun(d, 'maintain', [t.name])})),
    h('div', {class: 'status-line spaced'}, stateChip(t.state), h('span', {class: 'small-print'}, 'Seen ', time(t.inspectedAt), ` by a ${SEEN_BY[t.inspectedBy] || t.inspectedBy}`)),
    t.reasons.length ? h('div', {class: 'notice warn', text: `Needs attention: ${t.reasons.join(', ')}.`}) : null,
    t.lastError ? h('div', {class: 'notice error', text: t.lastError}) : null,
    h('div', {class: 'columns'},
      h('div', {class: 'stack'}, sizesCard(health), snapshotsCard(health), lastActionsCard(t),
        h('section', {class: 'card'}, h('h2', {text: 'Recent runs'}), runsTable(t.recentRuns))),
      h('div', {class: 'stack'}, tableFacts(health),
        h('section', {class: 'card'}, h('div', {class: 'section-title'}, h('h2', {text: 'Effective policy'}),
          t.state === 'excluded' ? chip('idle', 'Excluded') : t.policy.exists ? chip('success', 'Database policy') : chip('idle', 'Defaults')),
          policyFacts(t.policy.settings),
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
    a.detail ? h('p', {text: a.detail}) : null)));
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
  const logs = await api(`/runs/${run.id}/logs?tail=2000`);
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
    heading(`${run.databaseName} · ${LABELS[run.environment]} · ${run.schedule ? 'scheduled' : run.triggeredBy}`.toUpperCase(), h('h1', {class: 'compact', text: MODES[run.mode]}), null,
      active && isWriter(run.role) && h('button', {type: 'button', class: 'danger', text: 'Stop run', onclick: async event => {
        event.target.disabled = true;
        try { await send(`/runs/${run.id}/cancel`, 'POST'); go(location.hash, ['Stopping the run…']); } catch (error) { failure(error); }
      }}),
      h('a', {class: 'button quiet', href: `#/databases/${run.database}`, text: 'Back to database'})),
    h('div', {class: 'status-line spaced'}, chip(run.status), run.mode === 'dry-run' && h('span', {class: 'chip', text: 'nothing changed'}),
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
          h('dt', {text: 'Started by'}), h('dd', {}, run.schedule ? 'schedule' : run.triggeredBy, run.agent && h('span', {class: 'agent', text: run.agent})),
          run.exitCode !== null && run.exitCode !== undefined && h('dt', {text: 'Exit code'}),
          run.exitCode !== null && run.exitCode !== undefined && h('dd', {text: String(run.exitCode)}))),
        h('section', {class: 'card'}, h('h2', {text: 'What a run changes'}),
          h('p', {class: 'small', text: run.mode === 'maintain' ? 'Compaction and manifest rewrites add snapshots that keep every row. Expiry removes snapshots older than the policy\'s age; their files go with a later orphan cleanup.' : 'Nothing: health checks and dry runs only read the catalog and storage, with a read-only token.'})))));
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
