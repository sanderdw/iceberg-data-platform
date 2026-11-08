// The Evaluations page: question sets, runs and comparisons, over the same REST API agents use.
// Text from the API is only ever set with textContent, never parsed as HTML.
'use strict';

const $ = (id) => document.getElementById(id);
const state = { me: null, roles: {}, sets: [], set: null, view: 'runs', picked: [] };
const WRITERS = ['writer', 'admin', 'bucket-admin'];
const ADMINS = ['admin', 'bucket-admin'];
const TEMPLATE = `cases:
- name: delay_by_carrier
  inputs:
    question: Which carrier has the highest average departure delay?
  expected_output:
    query:
      metrics: [average_departure_delay]
      dimensions: [{field: CARRIER.name}]
      order_by: [{name: average_departure_delay, desc: true}]
- name: runway_split
  inputs:
    question: What is the average delay by runway length?
  expected_output: {refusal: fan_out}
`;

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key === 'class') node.className = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== undefined && value !== null && value !== false) node.setAttribute(key, value === true ? '' : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

async function api(method, path, body) {
  const response = await fetch('/api/v1' + path, {
    method, credentials: 'same-origin',
    headers: { 'content-type': 'application/json', 'x-iceberg-evaluations': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401) { show('signin'); throw new Error(data.error || 'Sign in again.'); }
  if (!response.ok) throw new Error(data.error || `The request failed (${response.status}).`);
  return data;
}

function show(id) {
  for (const main of document.querySelectorAll('main')) main.hidden = main.id !== id;
}

function notice(text, error = false) {
  const box = $('notice');
  box.textContent = text;
  box.className = 'notice' + (error ? ' error' : '');
  box.hidden = !text;
}

function crumbs(...items) {
  $('crumbs').replaceChildren(...items.flatMap((item, i) => [
    ...(i ? [el('span', {}, '/')] : []),
    item.href ? el('a', { href: item.href }, item.label) : el('span', {}, item.label),
  ]));
}

// The team's databases in an environment, by name; the API takes their id.
function databaseOptions(team, environment, selected) {
  const found = (state.me.databases || []).filter((d) => d.team === team && d.environment === environment);
  return [el('option', { value: '' }, 'None'),
    ...found.map((d) => el('option', { value: d.id, selected: d.id === selected }, d.name))];
}

const when = (t) => (t ? new Date(t * 1000).toLocaleString() : '');
const pct = (r) => (r === null || r === undefined ? '–' : `${Math.round(r * 100)}%`);
const rateClass = (r) => (r === null || r === undefined ? '' : r >= 0.9 ? 'high' : r >= 0.6 ? 'mid' : 'low');
const teamName = (team) => (state.me.memberships.find((m) => m.team === team) || {}).teamName || team;

// A done run with too many unanswered questions has no pass rate: it measured the outage, not the agent.
const unreliable = (run) => run.status === 'done' && run.summary.reliable === false;

function statusChip(run) {
  return unreliable(run) ? el('span', { class: 'chip failed' }, 'not reliable')
    : el('span', { class: `chip ${run.status}` }, run.status);
}

// Home: question sets

async function home() {
  show('home');
  crumbs({ label: 'Question sets' });
  const { questionSets } = await api('GET', '/question-sets');
  state.sets = questionSets;
  $('new-set').hidden = !state.me.memberships.some((m) => WRITERS.includes(m.role));
  $('set-list').replaceChildren(...(questionSets.length ? questionSets.map((s) => {
    const last = s.lastRun;
    const rate = last && last.summary ? last.summary.passRate : null;
    return el('a', { class: 'card set-card', href: `#/sets/${s.id}` },
      el('p', { class: 'eyebrow' }, `${teamName(s.team)} · ${s.environment}`),
      el('h2', {}, s.name),
      el('div', { class: `rate ${last && last.status === 'done' ? rateClass(rate) : ''}` },
        last && last.status === 'done' ? pct(rate) : '–'),
      el('p', { class: 'small' }, last ? `Last run ${when(last.created_at)} · ${unreliable(last) ? 'not reliable' : last.status}` : 'Not run yet'));
  }) : [el('p', {}, 'No question sets yet.')]));
  $('live-links').replaceChildren(...state.me.memberships.flatMap((m) => ['development', 'acceptance', 'production']
    .map((e) => el('a', { class: 'button quiet', href: `#/live/${m.team}/${e}` }, `${m.teamName} · ${e}`))));
  $('mcp-command').textContent = `claude mcp add --transport http --client-id ext-evaluations-mcp --callback-port 3010 iceberg-evaluations ${location.origin}/mcp`;
}

async function newSetForm() {
  const form = $('new-set-form');
  const teams = state.me.memberships.filter((m) => WRITERS.includes(m.role));
  const team = el('select', { name: 'team' }, teams.map((m) => el('option', { value: m.team }, m.teamName)));
  const environment = el('select', { name: 'environment' },
    ['development', 'acceptance', 'production'].map((e) => el('option', { value: e }, e)));
  const model = el('select', { name: 'model' }, el('option', { value: '' }, 'Choose a team and environment'));
  const name = el('input', { type: 'text', name: 'name', placeholder: 'flights_checks', required: true });
  const target = el('select', { name: 'target' });
  const yaml = el('textarea', { class: 'code editor', name: 'yaml', spellcheck: 'false' }, TEMPLATE);
  const error = el('p', { class: 'small error' });
  let models = [];
  async function loadModels() {
    model.replaceChildren(el('option', { value: '' }, 'Loading…'));
    try {
      ({ models } = await api('GET', `/teams/${team.value}/environments/${environment.value}/agent-models`));
      model.replaceChildren(...(models.length ? models.map((m, i) => el('option', { value: i },
        `${m.databaseName} / ${m.model.namespace.join('.')} / ${m.name}${m.shared ? ' (shared)' : ''}`))
        : [el('option', { value: '' }, 'No semantic models here')]));
      pickTarget();
      error.textContent = '';
    } catch (e) {
      models = [];
      model.replaceChildren(el('option', { value: '' }, 'Not available'));
      error.textContent = e.message;
    }
  }
  // By default the results go to the database of the model, when it is the team's own.
  function pickTarget() {
    const chosen = models[Number(model.value)];
    target.replaceChildren(...databaseOptions(team.value, environment.value,
      chosen && !chosen.shared ? chosen.model.database : ''));
  }
  team.addEventListener('change', loadModels);
  environment.addEventListener('change', loadModels);
  model.addEventListener('change', pickTarget);
  form.replaceChildren(
    el('h2', {}, 'New question set'),
    el('div', { class: 'form-grid' },
      el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Team'), team),
      el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Environment'), environment),
      el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Name'), name)),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Semantic model'), model),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Results to this team database (Iceberg)'), target),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Cases (YAML)'), yaml),
    error,
    el('div', { class: 'actions' },
      el('button', { type: 'submit' }, 'Create'),
      el('button', { type: 'button', class: 'quiet', onclick: () => { form.hidden = true; } }, 'Cancel')));
  form.onsubmit = async (event) => {
    event.preventDefault();
    const chosen = models[Number(model.value)];
    if (!chosen) { error.textContent = 'Choose a semantic model.'; return; }
    try {
      const created = await api('POST', '/question-sets', {
        team: team.value, environment: environment.value, name: name.value, model: chosen.model, yaml: yaml.value,
        target_database: target.value,
      });
      location.hash = `#/sets/${created.id}`;
    } catch (e) { error.textContent = e.message; }
  };
  form.hidden = false;
  await loadModels();
}

// One question set

async function questionSet(id, view) {
  show('set');
  notice('');
  const set = state.set = await api('GET', `/question-sets/${id}`);
  state.view = view || state.view;
  state.picked = [];
  crumbs({ label: 'Question sets', href: '#/' }, { label: set.name });
  $('set-team').textContent = `${teamName(set.team)} · ${set.environment} · version ${set.version}`;
  $('set-name').textContent = set.name;
  const mayRun = set.role && (set.environment === 'development' ? WRITERS : ADMINS).includes(set.role);
  const model = el('input', { type: 'text', placeholder: 'LLM: the agent\'s default', size: 28 });
  $('set-actions').replaceChildren(...(mayRun ? [model, el('button', { type: 'button', onclick: async () => {
    try {
      const run = await api('POST', `/question-sets/${id}/runs`, { llm_model: model.value.trim() });
      state.view = 'runs';
      await questionSet(id);
      notice(`Run ${run.id} started.`);
    } catch (e) { notice(e.message, true); }
  } }, 'Run now'), el('button', { type: 'button', class: 'quiet', title: 'One run with the semantic model and one where the same LLM has only the table schemas', onclick: async () => {
    try {
      await api('POST', `/question-sets/${id}/measure-semantic-model`, { llm_model: model.value.trim() });
      state.view = 'runs';
      await questionSet(id);
      notice('Measuring: one run with the semantic model and one with only the table schemas.');
    } catch (e) { notice(e.message, true); }
  } }, 'Measure the semantic model')] : []));
  for (const button of $('set-views').querySelectorAll('button')) {
    button.setAttribute('aria-selected', String(button.dataset.view === state.view));
    button.onclick = () => { state.view = button.dataset.view; renderSetView(); };
  }
  renderSetView();
}

function renderSetView() {
  for (const button of $('set-views').querySelectorAll('button')) {
    button.setAttribute('aria-selected', String(button.dataset.view === state.view));
  }
  const views = { runs: runsView, cases: casesView, schedules: schedulesView, settings: settingsView };
  $('view').replaceChildren(...views[state.view](state.set));
  watchRuns();
}

// While a run is going the list keeps itself current, and leaves a page someone has moved on from alone.
let runsTimer;
function watchRuns() {
  clearTimeout(runsTimer);
  const { id, runs } = state.set;
  if (state.view !== 'runs' || !runs.some((r) => ['queued', 'running'].includes(r.status))) return;
  const current = () => !$('set').hidden && state.set?.id === id && state.view === 'runs';
  runsTimer = setTimeout(async () => {
    if (!current()) return;
    const set = await api('GET', `/question-sets/${id}`).catch(() => null);
    if (!current()) return;
    if (set) { state.set = set; renderSetView(); } else watchRuns();
  }, 3000);
}

function runsView(set) {
  if (!set.runs.length) return [el('p', {}, 'No runs yet. Run the question set to see how the agent does.')];
  const compare = el('button', { type: 'button', class: 'quiet', disabled: state.picked.length !== 2, onclick: () => {
    const [a, b] = state.picked;
    location.hash = `#/compare/${a}/${b}`;
  } }, 'Compare two runs');
  const rows = set.runs.map((run) => {
    const pick = el('input', { type: 'checkbox', 'aria-label': 'Pick for comparison', checked: state.picked.includes(run.id), onchange: (event) => {
      state.picked = event.target.checked ? [...state.picked, run.id].slice(-2) : state.picked.filter((r) => r !== run.id);
      compare.disabled = state.picked.length !== 2;
    } });
    const s = run.summary || {};
    const partner = run.pair && set.runs.find((r) => r.pair === run.pair && r.arm !== run.arm);
    const measured = run.arm === 'control' && partner && partner.status === 'done' && run.status === 'done';
    return el('tr', { class: 'run-row' },
      el('td', {}, pick),
      el('td', { class: 'nowrap' }, el('a', { href: `#/runs/${run.id}` }, when(run.created_at))),
      el('td', {}, statusChip(run)),
      el('td', { class: 'nowrap' }, unreliable(run) ? `${s.errors} of ${s.cases} got no answer`
        : run.status === 'done' ? `${pct(s.passRate)} (${s.passed}/${s.answered ?? s.cases})` : run.error || ''),
      el('td', {}, run.llm_model || 'default',
        run.arm === 'control' ? el('span', { class: 'agent' }, 'schemas only') : null,
        measured ? el('a', { class: 'agent', href: `#/compare/${run.id}/${partner.id}` }, 'what the model adds') : null),
      el('td', { class: 'nowrap' }, s.inputTokens !== undefined ? `${s.inputTokens + s.outputTokens} tokens` : ''),
      el('td', {}, run.triggered_by));
  });
  return [el('div', { class: 'actions' }, compare),
    el('table', {}, el('thead', {}, el('tr', {}, ['', 'Started', 'Status', 'Passed', 'LLM', 'Usage', 'By'].map((h) => el('th', {}, h)))),
      el('tbody', {}, rows))];
}

function casesView(set) {
  const editable = set.role && (set.environment === 'development' ? WRITERS : ADMINS).includes(set.role);
  const yaml = el('textarea', { class: 'code editor', spellcheck: 'false', readonly: !editable }, set.yaml);
  const note = el('input', { type: 'text', placeholder: 'What changed (for the version history)' });
  const sql = el('textarea', { class: 'code editor', spellcheck: 'false', placeholder: '-- Q: …\n-- query_semantic_model: {…}' });
  const save = async () => {
    try {
      await api('POST', '/question-sets', { id: set.id, yaml: yaml.value, note: note.value || null });
      state.view = 'cases';
      await questionSet(set.id);
      notice('Saved as a new version.');
    } catch (e) { notice(e.message, true); }
  };
  const importSql = async () => {
    try {
      const result = await api('POST', `/question-sets/${set.id}/import`, { questions_sql: sql.value });
      state.view = 'cases';
      await questionSet(set.id);
      notice(`Imported ${result.imported} questions.`);
    } catch (e) { notice(e.message, true); }
  };
  return [
    el('section', { class: 'card' }, el('h2', {}, 'Cases'),
      el('p', { class: 'small' }, 'Each case has a question and one expected outcome: query (the governed query that answers it), refusal (an error code the agent must explain), clarify (ask back) or decline (no answer from this model).'),
      yaml, editable ? el('div', { class: 'actions' }, note, el('button', { type: 'button', onclick: save }, 'Save')) : null),
    editable ? el('section', { class: 'card' }, el('h2', {}, 'Import questions'),
      el('p', { class: 'small' }, 'Paste a semantic model\'s <name>.questions.sql. Questions with the same name are replaced.'),
      sql, el('div', { class: 'actions' }, el('button', { type: 'button', class: 'quiet', onclick: importSql }, 'Import'))) : null,
    el('section', { class: 'card' }, el('h2', {}, 'Versions'),
      el('table', {}, el('tbody', {}, set.versions.map((v) => el('tr', {},
        el('td', { class: 'nowrap' }, `v${v.version}`), el('td', {}, `${v.cases} cases`), el('td', {}, v.note),
        el('td', { class: 'nowrap' }, when(v.created_at)), el('td', {}, v.created_by)))))),
  ];
}

function schedulesView(set) {
  const admin = ADMINS.includes(set.role);
  const cron = el('input', { type: 'text', placeholder: '0 6 * * 1 (UTC)' });
  const model = el('input', { type: 'text', placeholder: 'LLM: default' });
  const rows = set.schedules.map((s) => el('tr', {},
    el('td', {}, el('code', {}, s.cron)), el('td', {}, s.llm_model || 'default'),
    el('td', {}, s.enabled ? 'on' : 'off'), el('td', { class: 'nowrap' }, `next ${when(s.next_run_at)}`),
    el('td', {}, admin ? el('button', { type: 'button', class: 'quiet', onclick: async () => {
      await api('DELETE', `/question-sets/${set.id}/schedules/${s.id}`);
      state.view = 'schedules';
      await questionSet(set.id);
    } }, 'Delete') : '')));
  return [
    rows.length ? el('table', {}, el('tbody', {}, rows)) : el('p', {}, 'No schedules.'),
    admin ? el('section', { class: 'card' }, el('h2', {}, 'Add a schedule'),
      el('div', { class: 'form-grid' }, cron, model),
      el('div', { class: 'actions' }, el('button', { type: 'button', onclick: async () => {
        try {
          await api('POST', `/question-sets/${set.id}/schedules`, { cron: cron.value.trim(), llm_model: model.value.trim() });
          state.view = 'schedules';
          await questionSet(set.id);
        } catch (e) { notice(e.message, true); }
      } }, 'Add'))) : el('p', { class: 'small' }, 'Team administrators schedule runs.'),
  ];
}

function settingsView(set) {
  const editable = set.role && (set.environment === 'development' ? WRITERS : ADMINS).includes(set.role);
  const description = el('input', { type: 'text', value: set.description || '' });
  const rubric = el('textarea', { rows: 4 }, set.rubric || '');
  const target = el('select', {}, databaseOptions(set.team, set.environment, set.target_database));
  return [el('section', { class: 'card' },
    el('dl', { class: 'facts' },
      el('dt', {}, 'Agent'), el('dd', {}, set.agent),
      el('dt', {}, 'Model'), el('dd', {}, `${set.model.database} / ${set.model.namespace.join('.')} / ${set.model.name}`),
      el('dt', {}, 'Created'), el('dd', {}, `${when(set.created_at)} by ${set.created_by}`)),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Description'), description),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Judge rubric (with an LLM judge configured)'), rubric),
    el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Results to this team database (Iceberg)'), target),
    editable ? el('div', { class: 'actions' },
      el('button', { type: 'button', onclick: async () => {
        try {
          await api('POST', '/question-sets', { id: set.id, description: description.value, rubric: rubric.value,
            target_database: target.value.trim() });
          state.view = 'settings';
          await questionSet(set.id);
          notice('Saved.');
        } catch (e) { notice(e.message, true); }
      } }, 'Save'),
      el('button', { type: 'button', class: 'danger', onclick: async () => {
        if (!confirm(`Delete ${set.name} with its runs and schedules? Results in Iceberg stay.`)) return;
        try { await api('DELETE', `/question-sets/${set.id}`); location.hash = '#/'; } catch (e) { notice(e.message, true); }
      } }, 'Delete')) : null)];
}

// One run, and two runs compared

function scoreChips(scores) {
  return el('div', { class: 'chips' }, Object.entries(scores || {}).map(([name, s]) =>
    el('span', { class: `chip ${s.value ? 'pass' : 'fail'}`, title: s.reason }, name)));
}

async function runPage(id) {
  show('run');
  const run = await api('GET', `/runs/${id}`);
  crumbs({ label: 'Question sets', href: '#/' }, { label: run.questionSetName, href: `#/sets/${run.question_set}` },
    { label: 'Run' });
  $('run-eyebrow').textContent = `${run.questionSetName} · version ${run.version} · ${run.llm_model || 'default LLM'}`;
  $('run-title').textContent = unreliable(run) ? 'Not reliable'
    : run.status === 'done' ? `${pct(run.summary.passRate)} passed` : run.status;
  const s = run.summary || {};
  const view = $('run-view');
  view.replaceChildren(
    el('section', { class: 'summary' },
      el('div', {}, el('span', { class: 'eyebrow' }, 'Cases'), `${s.passed ?? '–'} / ${s.cases ?? '–'}`),
      el('div', {}, el('span', { class: 'eyebrow' }, 'Median'), s.medianSeconds !== undefined ? `${s.medianSeconds} s` : '–'),
      el('div', {}, el('span', { class: 'eyebrow' }, 'Tokens'), s.inputTokens !== undefined ? `${s.inputTokens} in · ${s.outputTokens} out` : '–'),
      el('div', {}, el('span', { class: 'eyebrow' }, 'Results'), run.published || '–'),
      el('div', {}, el('span', { class: 'eyebrow' }, 'Started by'), run.triggered_by)),
    run.error ? el('p', { class: 'small error' }, run.error) : '',
    unreliable(run) ? el('p', { class: 'small error' }, `${s.errors} of ${s.cases} questions got no answer, so this run has no pass rate. It says nothing about the agent: see the errors below, fix the cause and run it again.`) : '',
    el('table', { class: 'stacked' }, el('thead', {}, el('tr', {}, ['Case', 'Expected', 'Result', 'Scores', 'Answer'].map((h) => el('th', {}, h)))),
      el('tbody', {}, run.cases.map((c) => el('tr', {},
        el('td', {}, el('strong', {}, c.name), el('div', { class: 'reason' }, c.question)),
        el('td', { 'data-label': 'Expected' }, c.kind),
        el('td', { 'data-label': 'Result' }, el('span', { class: `chip ${c.passed ? 'pass' : 'fail'}` }, c.passed ? 'pass' : 'fail')),
        el('td', { 'data-label': 'Scores' }, scoreChips(c.scores), ...Object.entries(c.scores || {}).filter(([, v]) => !v.value)
          .map(([k, v]) => el('div', { class: 'reason' }, `${k}: ${v.reason}`))),
        el('td', { 'data-label': 'Answer' }, c.error ? el('span', { class: 'small error' }, c.error) : el('details', {},
          el('summary', {}, `${(c.duration_ms / 1000).toFixed(1)} s · ${c.input_tokens + c.output_tokens} tokens`),
          el('p', { class: 'answer' }, c.answer),
          c.trace_id ? el('p', { class: 'reason' }, `trace ${c.trace_id}`) : null)))))));
  if (run.status === 'queued' || run.status === 'running') setTimeout(() => route(), 3000);
}

async function comparePage(a, b) {
  show('run');
  const result = await api('GET', `/runs/${a}/compare/${b}`);
  crumbs({ label: 'Question sets', href: '#/' }, { label: 'Compare' });
  $('run-eyebrow').textContent = `${result.baseline.llm_model || 'default'} → ${result.candidate.llm_model || 'default'}`;
  $('run-title').textContent = result.reliable ? `${result.change.fixed} fixed · ${result.change.broken} broken`
    : 'Not reliable';
  if (!result.reliable) $('run-eyebrow').textContent += ' · one of these runs got too few answers to compare';
  const up = result.uplift;
  if (up) {
    $('run-eyebrow').textContent = 'Same LLM, same questions: only the table schemas → the semantic model';
    $('run-title').textContent = `${pct(up.control)} → ${pct(up.semantic)} (${up.points >= 0 ? '+' : ''}${up.points} points)`;
  }
  $('run-view').replaceChildren(up ? el('section', { class: 'card' },
    el('h2', {}, 'What the semantic model adds'),
    el('p', { class: 'small' }, `${up.questions} questions both answered, on the checks both have. The right answer is the semantic model's own query, so this measures matching its definitions: agreed definitions are part of what it adds.`),
    el('table', {}, el('thead', {}, el('tr', {}, ['Expected', 'Questions', 'Schemas only', 'Semantic model', 'Points'].map((h) => el('th', {}, h)))),
      el('tbody', {}, Object.entries(up.byKind).map(([kind, r]) => el('tr', {},
        el('td', {}, kind), el('td', {}, r.questions), el('td', {}, pct(r.control)), el('td', {}, pct(r.semantic)), el('td', {}, r.points)))))) : '', el('table', { class: 'stacked' },
    el('thead', {}, el('tr', {}, ['Case', 'Change', 'Before', 'After'].map((h) => el('th', {}, h)))),
    el('tbody', {}, result.cases.map((c) => el('tr', {},
      el('td', {}, el('strong', {}, c.name), el('div', { class: 'reason' }, c.question)),
      el('td', { 'data-label': 'Change' }, el('span', { class: `chip ${c.change}` }, c.change)),
      el('td', { 'data-label': 'Before' }, c.baseline ? scoreChips(c.baseline.scores) : '–', c.baseline ? el('p', { class: 'answer' }, c.baseline.answer) : null),
      el('td', { 'data-label': 'After' }, c.candidate ? scoreChips(c.candidate.scores) : '–', c.candidate ? el('p', { class: 'answer' }, c.candidate.answer) : null))))));
}

// Live evaluation of one team environment

async function livePage(team, environment) {
  show('live');
  crumbs({ label: 'Question sets', href: '#/' }, { label: 'Live' });
  $('live-eyebrow').textContent = `${teamName(team)} · ${environment}`;
  const say = (text, error = false) => {
    const box = $('live-notice');
    box.textContent = text; box.className = 'notice' + (error ? ' error' : ''); box.hidden = !text;
  };
  const setting = await api('GET', `/teams/${team}/environments/${environment}/live`);
  const { reviews } = await api('GET', `/teams/${team}/environments/${environment}/reviews?status=open`);
  const sets = state.sets.length ? state.sets : (await api('GET', '/question-sets')).questionSets;
  const targets = sets.filter((s) => s.team === team && s.environment === environment);
  const admin = ADMINS.includes(state.roles[team]);
  const writer = WRITERS.includes(state.roles[team]);
  const enabled = el('input', { type: 'checkbox', checked: setting.enabled });
  const sample = el('input', { type: 'number', min: 1, max: 100, value: setting.sample_pct });
  const cron = el('input', { type: 'text', value: setting.cron });
  const target = el('select', {}, databaseOptions(team, environment, setting.target_database));
  const last = setting.last_summary || {};
  $('live-view').replaceChildren(
    el('section', { class: 'card' }, el('h2', {}, 'Settings'),
      el('p', { class: 'small' }, 'While this is on, Conversational BI keeps this team\'s conversations in this environment for its retention period, and only the Evaluations extension reads them.'),
      el('label', { class: 'check' }, enabled, 'Score new conversations'),
      el('div', { class: 'form-grid' },
        el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Sample %'), sample),
        el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Check (cron, UTC)'), cron),
        el('label', { class: 'field' }, el('span', { class: 'eyebrow' }, 'Scores to database'), target)),
      admin ? el('div', { class: 'actions' }, el('button', { type: 'button', onclick: async () => {
        try {
          await api('PUT', `/teams/${team}/environments/${environment}/live`, { enabled: enabled.checked,
            sample_pct: Number(sample.value), cron: cron.value.trim(), target_database: target.value.trim() });
          await livePage(team, environment);
          say('Saved.');
        } catch (e) { say(e.message, true); }
      } }, 'Save')) : el('p', { class: 'small' }, 'Team administrators change these settings.'),
      el('p', { class: 'reason' }, setting.last_checked ? `Last check ${when(setting.last_checked)}: ${last.error
        || `${last.checked} of ${last.conversations} conversations scored, ${last.flagged} flagged`}` : 'Not checked yet.')),
    el('section', { class: 'card' }, el('h2', {}, `To review (${reviews.length})`),
      reviews.length ? el('table', {}, el('tbody', {}, reviews.map((r) => {
        const into = el('select', {}, targets.map((s) => el('option', { value: s.id }, s.name)));
        return el('tr', {},
          el('td', {}, el('strong', {}, r.question), el('p', { class: 'answer' }, r.answer),
            el('div', { class: 'reason' }, `${when(r.asked_at)} · ${r.llm_model}`)),
          el('td', {}, scoreChips(r.scores), ...Object.entries(r.scores).filter(([, v]) => !v.value)
            .map(([k, v]) => el('div', { class: 'reason' }, `${k}: ${v.reason}`))),
          el('td', {}, writer ? el('div', { class: 'actions' },
            el('button', { type: 'button', class: 'quiet', onclick: async () => {
              await api('POST', `/reviews/${r.id}`, { action: 'dismiss' });
              await livePage(team, environment);
            } }, 'Dismiss'),
            targets.length ? into : null,
            targets.length ? el('button', { type: 'button', onclick: async () => {
              try {
                const done = await api('POST', `/reviews/${r.id}/promote`, { question_set: into.value });
                await livePage(team, environment);
                say(`Added as case ${done.case}.`);
              } catch (e) { say(e.message, true); }
            } }, 'Make a case') : null) : ''));
      }))) : el('p', {}, 'Nothing to review.')));
}

// Routing, session and theme

async function route() {
  const parts = location.hash.replace(/^#\/?/, '').split('/');
  try {
    if (parts[0] === 'sets' && parts[1]) await questionSet(parts[1]);
    else if (parts[0] === 'runs' && parts[1]) await runPage(parts[1]);
    else if (parts[0] === 'compare' && parts[2]) await comparePage(parts[1], parts[2]);
    else if (parts[0] === 'live' && parts[2]) await livePage(parts[1], parts[2]);
    else await home();
  } catch (e) {
    show('home');
    $('set-list').replaceChildren(el('p', { class: 'small error' }, e.message));
  }
}

function theme(next) {
  document.documentElement.dataset.theme = next;
  $('theme').textContent = next === 'dark' ? 'Light / ○' : 'Dark / ●';
  try { localStorage.setItem('theme', next); } catch { /* private mode */ }
}

async function start() {
  let saved = 'dark';
  try { saved = localStorage.getItem('theme') || 'dark'; } catch { /* private mode */ }
  theme(saved);
  $('theme').addEventListener('click', () => theme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'));
  const session = await fetch('/api/session', { credentials: 'same-origin' }).then((r) => r.json());
  if (!session.authenticated) { show('signin'); return; }
  $('identity').hidden = false;
  $('username').textContent = session.user;
  $('logout').addEventListener('click', async () => {
    const { logoutUrl } = await fetch('/auth/logout', { method: 'POST', headers: { 'x-iceberg-evaluations': '1' } }).then((r) => r.json());
    location.href = logoutUrl;
  });
  $('new-set').addEventListener('click', newSetForm);
  state.me = await api('GET', '/me');
  state.roles = Object.fromEntries(state.me.memberships.map((m) => [m.team, m.role]));
  window.addEventListener('hashchange', route);
  await route();
}

start();
