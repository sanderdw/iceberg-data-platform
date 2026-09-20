// Portal-owned authoring and filters. Charts arrive as isolated SVG images;
// report names, SQL, data and errors are always rendered as text.
const Reporting = (() => {
  let context = null, generation = 0, items = [], selected = null, draft = null;
  let columns = [], job = null, runVersion = 0, dirty = false, loaded = false, filterValue = '';
  const root = () => document.querySelector('#report-root');
  const el = (tag, text, cls) => element(tag, text, cls);
  function button(text, fn, cls = 'quiet') {
    const node = el('button', text, cls); node.type = 'button';
    node.addEventListener('click', () => busy(node, fn)); return node;
  }
  function label(text, input) { const node = el('label', text); input.setAttribute('aria-label', text); node.append(input); return node; }
  function input(value = '', type = 'text') { const node = el('input'); node.type = type; node.value = value; return node; }
  function select(options, value) {
    const node = el('select');
    options.forEach(option => { const [v, text] = Array.isArray(option) ? option : [option, option]; const o = el('option', text); o.value = v; node.append(o); });
    node.value = value; return node;
  }
  const heading = (number, text) => { const h = el('h3', `${number} / ${text}`, 'report-step'); return h; };
  function fieldset(number, text) { const node = el('fieldset'); node.append(el('legend', `${number} / ${text}`)); return node; }
  function edited() { cancel(); dirty = true; const n = root().querySelector('[data-save-status]'); if (n) n.textContent = 'Unsaved changes'; }
  function cancel() {
    runVersion++;
    const previous = job; job = null;
    if (previous) api(`/report-jobs/${previous}`, 'DELETE').catch(() => {});
    const status = root()?.querySelector('[data-run-status]'); if (status) status.textContent = 'Cancelled';
  }
  function reset() {
    generation++; cancel(); items = []; selected = null; draft = null; columns = []; loaded = false; dirty = false; filterValue = '';
    root()?.replaceChildren();
  }
  function syncState() {
    const next = JSON.stringify([state.user.id || state.user.name, state.activeTeam, state.activeEnvironment]);
    if (context !== next) { reset(); context = next; }
  }
  async function loadItems() {
    const version = generation;
    const result = await api('/reports');
    if (version !== generation) return false;
    items = result.items; loaded = true; return true;
  }
  async function show() {
    try { if (!loaded && !(await loadItems())) return; if (!draft) library(); }
    catch (error) { notice(error.message, true); }
  }
  function home() { generation++; cancel(); selected = null; draft = null; show(); }
  function library() {
    const host = root(); host.replaceChildren();
    const bar = el('div', undefined, 'panel-heading');
    const title = el('div'); title.append(el('h2', 'Reports & dashboards'), el('p', 'Shared with this team in this environment. Each viewer uses their own data access.', 'hint'));
    const controls = el('div', undefined, 'report-actions');
    controls.append(button('New report', () => newReport(), ''), button('New dashboard', () => newDashboard()), button('Refresh list', async () => { if (await loadItems()) library(); }));
    bar.append(title, controls); host.append(bar);
    for (const [kind, title] of [['dashboard', 'Dashboards'], ['report', 'Reports']]) {
      host.append(el('h3', title)); const list = el('div', undefined, 'report-library');
      for (const item of items.filter(i => i.kind === kind)) {
        const card = el('article', undefined, 'report-list-card');
        card.append(button(item.body.name, () => open(item.id)), el('p', item.body.description || (kind === 'dashboard' ? `${item.body.cards.length} cards` : item.body.source.table), 'hint'), el('small', `Revision ${item.revision} · ${new Date(item.updated * 1000).toLocaleDateString()}`)); list.append(card);
      }
      if (!list.children.length) list.append(el('p', `No ${title.toLowerCase()} yet.`, 'empty')); host.append(list);
    }
  }
  function blankReport() {
    return {version: 1, name: 'Untitled report', description: '', source: {database: state.databases[0]?.id || '', namespace: [], table: ''}, mode: 'builder',
      builder: {aggregate: 'count', measure: '', dimensions: [], columns: [], filters: [], sort: '', descending: false, limit: 500},
      sql: '', parameters: {}, visualization: {kind: 'table', x: 'dimension', y: 'value', color: ''}, timezone: 'Europe/Amsterdam'};
  }
  function newReport() { generation++; cancel(); selected = null; draft = blankReport(); columns = []; dirty = true; editor(); saveRoute(); }
  async function fromTable(db, ns, table) {
    showPage('reports'); newReport(); draft.source = {database: db, namespace: [...ns], table}; draft.name = `${table} report`;
    const version = generation; await loadColumns(); if (version === generation) editor();
  }
  function newDashboard() {
    generation++; cancel(); selected = null; draft = {version: 1, name: 'Untitled dashboard', description: '', cards: [], filter_label: 'Filter'};
    dirty = true; editor(); saveRoute();
  }
  async function open(id, route = true) {
    const version = ++generation; cancel();
    const result = await api(`/reports/${id}`); if (version !== generation) return;
    selected = result; draft = structuredClone(result.body); dirty = false; columns = [];
    if (draft.source) await loadColumns();
    if (version !== generation) return;
    if (draft.cards && !loaded) await loadItems();
    if (version !== generation) return;
    editor(); if (route) saveRoute();
  }
  async function loadColumns() {
    if (!draft?.source?.table) return;
    const version = generation, source = draft.source;
    const params = new URLSearchParams({database: source.database, kind: 'table', name: source.table}); source.namespace.forEach(p => params.append('namespace', p));
    try {
      const result = await api(`/details?${params}`);
      if (version === generation && draft.source === source) columns = result.columns;
    } catch (error) { if (version === generation) notice(error.message, true); }
  }
  function canEdit() { return !selected || selected.owner === state.user.id || ['admin', 'bucket-admin'].includes(state.activeRole); }
  async function save(copy = false) {
    if (root().querySelector(':invalid')) { root().querySelector(':invalid').reportValidity(); return; }
    const version = generation, definition = structuredClone(draft), kind = draft.cards ? 'dashboard' : 'report';
    const identifier = !copy && selected?.id;
    const result = await api(`/${kind === 'dashboard' ? 'dashboards' : 'reports'}${identifier ? '/' + identifier : ''}`, identifier ? 'PUT' : 'POST', {definition, revision: identifier ? selected.revision : null});
    if (version !== generation) return;
    selected = result; dirty = JSON.stringify(draft) !== JSON.stringify(definition);
    await loadItems(); if (version !== generation) return;
    editor(); saveRoute(); notice(copy ? 'Copy saved.' : 'Saved for your team.');
  }
  function editor() {
    const host = root(); host.replaceChildren();
    const bar = el('div', undefined, 'report-actions');
    bar.append(button('← All reports', async () => { cancel(); generation++; selected = null; draft = null; if (await loadItems()) library(); saveRoute(); }),
      button('Save', () => save(), ''), button('Save a copy', () => save(true)));
    bar.children[1].disabled = !canEdit();
    if (selected && canEdit()) bar.append(button('Delete', async () => {
      const panel = el('div', undefined, 'report-confirm'); panel.append(el('p', `Delete “${draft.name}”? This removes the saved definition.`), button('Delete permanently', async () => {
        await api(`/reports/${selected.id}`, 'DELETE', {revision: selected.revision}); selected = null; draft = null; generation++; cancel(); await loadItems(); library(); saveRoute();
      }), button('Keep', () => panel.remove())); host.prepend(panel);
    }));
    const status = el('span', dirty ? 'Unsaved changes' : `Saved · revision ${selected?.revision || 1}`, 'hint'); status.dataset.saveStatus = ''; bar.append(status);
    host.append(bar);
    const metadata = el('div', undefined, 'report-fields'); const name = input(draft.name); name.maxLength = 120; name.addEventListener('input', () => { draft.name = name.value; edited(); });
    const description = input(draft.description); description.maxLength = 500; description.addEventListener('input', () => { draft.description = description.value; edited(); });
    metadata.append(label('Name', name), label('Description', description)); host.append(metadata);
    if (draft.cards) dashboardEditor(host); else reportEditor(host);
  }
  function reportEditor(host) {
    const grid = el('div', undefined, 'report-workbench'), form = el('div', undefined, 'report-builder'), preview = el('section', undefined, 'report-preview');
    grid.append(form, preview); host.append(grid);
    const source = fieldset('01', 'Data'); const db = select(state.databases.map(d => [d.id, d.name]), draft.source.database);
    db.addEventListener('change', () => { draft.source = {database: db.value, namespace: [], table: ''}; columns = []; edited(); editor(); });
    source.append(label('Database', db), el('p', draft.source.table ? [...draft.source.namespace, draft.source.table].join(' / ') : 'Choose a namespace and table.', 'hint'));
    const picker = el('div', undefined, 'report-source-picker'); source.append(picker); form.append(source);
    const version = generation;
    async function browse(ns) {
      const sourceDb = draft.source.database; if (!sourceDb) return;
      const params = new URLSearchParams({database: sourceDb}); ns.forEach(p => params.append('namespace', p));
      picker.replaceChildren(el('p', 'Loading tables…', 'hint'));
      try {
        const result = await api(`/contents?${params}`); if (version !== generation || !picker.isConnected || draft.source.database !== sourceDb) return;
        picker.replaceChildren(); if (ns.length) picker.append(button('↑ Parent namespace', () => browse(ns.slice(0, -1))));
        result.namespaces.forEach(parts => picker.append(button(`${parts.at(-1)} /`, () => browse(parts))));
        result.tables.forEach(t => picker.append(button(t.name, async () => {
          const chosen = {database: sourceDb, namespace: [...ns], table: t.name};
          draft.source = chosen; draft.builder = blankReport().builder; columns = []; edited(); editor();
          await loadColumns();
          if (version !== generation || draft.source !== chosen) return;
          draft.builder.columns = columns.slice(0, 6).map(c => c.name); editor();
        })));
        if (!result.namespaces.length && !result.tables.length) picker.append(el('p', 'No tables in this namespace.', 'hint'));
      } catch (error) { if (picker.isConnected) picker.replaceChildren(el('p', error.message, 'error')); }
    }
    browse(draft.source.namespace);
    const mode = fieldset('02', 'Query');
    if (draft.mode === 'builder') {
      mode.append(el('p', 'Visual builder', 'eyebrow'));
      mode.append(button('View SQL', async () => {
        const result = await api('/report-sql', 'POST', {definition: draft});
        if (version !== generation || !mode.isConnected) return;
        const code = el('pre', result.sql + '\n\nParameters: ' + JSON.stringify(result.parameters, null, 2), 'report-sql-preview');
        mode.querySelector('pre')?.remove(); mode.append(code);
      }), button('Edit as SQL copy', async () => {
        const result = await api('/report-sql', 'POST', {definition: draft});
        if (version !== generation || !mode.isConnected) return;
        draft = {...structuredClone(draft), name: draft.name + ' (SQL)', mode: 'sql', sql: result.sql, parameters: result.parameters}; selected = null; edited(); editor(); saveRoute();
      }));
      builderEditor(mode);
    } else {
      mode.append(el('p', 'Query the selected table as source. Use $name for parameters. Supports SELECT, aggregates and CTEs.', 'hint'));
      const code = el('textarea'); code.value = draft.sql; code.rows = 12; code.spellcheck = false; code.setAttribute('aria-label', 'SQL query'); code.className = 'report-sql';
      code.addEventListener('input', () => { draft.sql = code.value; edited(); }); mode.append(code);
      const parameters = el('textarea'); parameters.rows = 3; parameters.value = JSON.stringify(draft.parameters, null, 2); parameters.setAttribute('aria-label', 'SQL parameters (JSON)');
      parameters.addEventListener('input', () => { try { draft.parameters = JSON.parse(parameters.value); parameters.setCustomValidity(''); edited(); } catch { parameters.setCustomValidity('Enter a JSON object.'); } }); mode.append(label('SQL parameters (JSON)', parameters));
      const schema = el('details'), summary = el('summary', 'Available columns'); schema.append(summary);
      columns.forEach(c => schema.append(el('p', `${c.name} · ${c.type}${c.doc ? ' — ' + c.doc : ''}`, 'hint'))); mode.append(schema);
    }
    form.append(mode);
    const chart = fieldset('03', 'Visualize');
    const kind = select(['table', 'kpi', 'bar', 'line', 'area', 'scatter'].map(k => [k, k.toUpperCase()]), draft.visualization.kind);
    kind.addEventListener('change', () => { draft.visualization.kind = kind.value; edited(); }); chart.append(label('Chart type', kind));
    for (const [key, text] of [['x', 'X column'], ['y', 'Y / value column'], ['color', 'Series column (optional)']]) {
      const control = input(draft.visualization[key]); control.addEventListener('input', () => { draft.visualization[key] = control.value; edited(); }); chart.append(label(text, control));
    }
    chart.append(el('p', 'Aggregates produce value; grouping produces dimension and optionally series. Table charts retain exact values.', 'hint'));
    const timezone = select(['Europe/Amsterdam', 'UTC'], draft.timezone); timezone.addEventListener('change', () => { draft.timezone = timezone.value; edited(); }); chart.append(label('Timezone', timezone)); form.append(chart);
    preview.append(heading('04', 'Preview')); runControls(preview, false);
  }
  function builderEditor(host) {
    const builder = draft.builder;
    const columnOptions = [['', 'Choose a column'], ...columns.map(c => [c.name, `${c.name} · ${c.type}`])];
    const aggregate = select([['rows', 'Rows'], ['count', 'Count rows'], ['sum', 'Sum'], ['avg', 'Average'], ['min', 'Minimum'], ['max', 'Maximum'], ['distinct', 'Distinct count']], builder.aggregate);
    aggregate.addEventListener('change', () => { builder.aggregate = aggregate.value; builder.sort = ''; edited(); editor(); }); host.append(label('Summarize', aggregate));
    if (!['count', 'rows'].includes(builder.aggregate)) {
      const measure = select(columnOptions, builder.measure); measure.addEventListener('change', () => { builder.measure = measure.value; edited(); }); host.append(label('Measure', measure));
    }
    if (builder.aggregate === 'rows') {
      const control = el('select'); control.multiple = true; control.size = Math.min(8, columns.length || 3);
      columns.forEach(c => { const o = el('option', c.name); o.value = c.name; o.selected = builder.columns.includes(c.name); control.append(o); });
      control.addEventListener('change', () => { builder.columns = [...control.selectedOptions].map(o => o.value).slice(0, 20); builder.sort = ''; edited(); }); host.append(label('Columns', control));
    } else {
      for (let i = 0; i < 2; i++) {
        const d = builder.dimensions[i] || {column: '', grain: 'none'}, group = select([['', 'No grouping'], ...columnOptions.slice(1)], d.column);
        group.addEventListener('change', () => { if (group.value) builder.dimensions[i] = {column: group.value, grain: d.grain}; else builder.dimensions.splice(i); builder.dimensions = builder.dimensions.filter(Boolean); builder.sort = ''; edited(); editor(); });
        group.disabled = i === 1 && !builder.dimensions[0]; host.append(label(i ? 'Split series by' : 'Group by', group));
        if (d.column) {
          const grain = select(['none', 'hour', 'day', 'week', 'month', 'year'], d.grain); grain.addEventListener('change', () => { d.grain = grain.value; edited(); }); host.append(label(i ? 'Series time bucket' : 'Time bucket', grain));
        }
      }
    }
    const filters = el('div', undefined, 'report-filters'); filters.append(el('h4', 'Filter rows'));
    builder.filters.forEach((f, index) => {
      const row = el('div', undefined, 'report-filter');
      const column = select(columnOptions, f.column); column.setAttribute('aria-label', `Filter ${index + 1} column`); column.addEventListener('change', () => { f.column = column.value; edited(); });
      const operator = select([['eq', '='], ['ne', '≠'], ['gt', '>'], ['gte', '≥'], ['lt', '<'], ['lte', '≤'], ['contains', 'Contains'], ['null', 'Is empty'], ['not_null', 'Is not empty']], f.operator); operator.setAttribute('aria-label', `Filter ${index + 1} operator`); operator.addEventListener('change', () => { f.operator = operator.value; edited(); });
      const value = input(f.value ?? ''); value.setAttribute('aria-label', `Filter ${index + 1} value`); value.addEventListener('input', () => { f.value = value.value; edited(); });
      row.append(column, operator, value, button('Remove', () => { builder.filters.splice(index, 1); edited(); editor(); })); filters.append(row);
    });
    if (builder.filters.length < 12) filters.append(button('Add filter', () => { builder.filters.push({column: columns[0]?.name || '', operator: 'eq', value: ''}); edited(); editor(); })); host.append(filters);
    const output = builder.aggregate === 'rows' ? builder.columns : [...builder.dimensions.map((_, i) => i ? 'series' : 'dimension'), 'value'];
    const sort = select([['', 'Unsorted'], ...output], builder.sort); sort.addEventListener('change', () => { builder.sort = sort.value; edited(); }); host.append(label('Sort by', sort));
    const descending = select([['false', 'Ascending'], ['true', 'Descending']], String(builder.descending)); descending.addEventListener('change', () => { builder.descending = descending.value === 'true'; edited(); }); host.append(label('Direction', descending));
    const limit = input(builder.limit, 'number'); limit.min = 1; limit.max = 1000; limit.addEventListener('input', () => { builder.limit = Number(limit.value); edited(); }); host.append(label('Row limit', limit));
  }
  function dashboardEditor(host) {
    const layout = fieldset('01', 'Arrange reports');
    const available = items.filter(i => i.kind === 'report');
    draft.cards.forEach((card, index) => {
      const row = el('div', undefined, 'report-dashboard-row');
      const chosen = select(available.map(i => [i.id, i.body.name]), card.report_id); chosen.setAttribute('aria-label', `Card ${index + 1} report`); chosen.addEventListener('change', () => { card.report_id = chosen.value; edited(); });
      const width = select([['half', 'Half width'], ['full', 'Full width']], card.width); width.setAttribute('aria-label', `Card ${index + 1} width`); width.addEventListener('change', () => { card.width = width.value; edited(); });
      const mapping = input(card.filter_column); mapping.setAttribute('aria-label', `Card ${index + 1} filter column`); mapping.placeholder = 'Optional source column for shared filter'; mapping.addEventListener('input', () => { card.filter_column = mapping.value; edited(); });
      row.append(chosen, width, mapping, button('↑', () => { if (index) { [draft.cards[index - 1], draft.cards[index]] = [card, draft.cards[index - 1]]; edited(); editor(); } }), button('↓', () => { if (index < draft.cards.length - 1) { [draft.cards[index + 1], draft.cards[index]] = [card, draft.cards[index + 1]]; edited(); editor(); } }), button('Remove', () => { draft.cards.splice(index, 1); edited(); editor(); })); layout.append(row);
    });
    const add = button('Add report', () => { draft.cards.push({report_id: available[0].id, width: 'half', filter_column: ''}); edited(); editor(); }); add.disabled = !available.length || draft.cards.length >= 8; layout.append(add);
    if (!available.length) layout.append(el('p', 'Save a report first, then add it to a dashboard.', 'hint'));
    const filterLabel = input(draft.filter_label); filterLabel.addEventListener('input', () => { draft.filter_label = filterLabel.value; edited(); }); layout.append(label('Shared filter label', filterLabel), el('p', 'Map a source column on each card. The shared filter matches that column exactly; unmapped cards are unchanged.', 'hint'));
    host.append(layout); runControls(host, true);
  }
  function runControls(host, dashboard) {
    const controls = el('div', undefined, 'report-actions');
    if (dashboard) { const filter = input(filterValue); filter.addEventListener('input', () => { filterValue = filter.value; }); controls.append(label(draft.filter_label, filter)); }
    controls.append(button(dashboard ? 'Run dashboard' : 'Run report', () => run(false), ''), button('Refresh now', () => run(true)), button('Cancel run', () => cancel()));
    const status = el('p', 'Run to query the latest committed Iceberg snapshot.', 'hint'); status.dataset.runStatus = ''; status.setAttribute('role', 'status');
    const result = el('div', undefined, dashboard ? 'report-results dashboard-grid' : 'report-results'); result.dataset.results = '';
    host.append(controls, status, result);
  }
  async function run(refresh) {
    if (root().querySelector(':invalid')) { root().querySelector(':invalid').reportValidity(); return; }
    if (draft.cards && (!selected || dirty)) { notice('Save the dashboard before running it.'); return; }
    cancel(); const version = generation, runId = runVersion;
    const status = root().querySelector('[data-run-status]'), results = root().querySelector('[data-results]'); results.replaceChildren(); status.textContent = 'Running…';
    const request = draft.cards ? {dashboard_id: selected.id, filter_value: filterValue || null, refresh} : {definition: structuredClone(draft), refresh};
    try {
      const started = await api('/report-jobs', 'POST', request);
      if (version !== generation || runId !== runVersion) { api(`/report-jobs/${started.id}`, 'DELETE').catch(() => {}); return; }
      job = started.id;
      while (job === started.id && version === generation) {
        await new Promise(resolve => setTimeout(resolve, 500));
        const result = await api(`/report-jobs/${started.id}`);
        if (job !== started.id || version !== generation) return;
        if (result.status === 'running') continue;
        job = null; status.textContent = result.status === 'cancelled' ? 'Cancelled' : `${result.cards.length} report${result.cards.length === 1 ? '' : 's'} completed`;
        result.cards.forEach(card => results.append(resultCard(card))); return;
      }
    } catch (error) { if (version === generation && runId === runVersion) { job = null; status.textContent = error.message; results.replaceChildren(); } }
  }
  function resultCard(card) {
    const node = el('article', undefined, `report-result ${card.width === 'full' ? 'full-width' : ''}`); node.append(el('h3', card.name));
    if (card.error) { node.append(el('p', card.error, 'error')); return node; }
    const {data, svg, cached, executedAt} = card.result;
    node.append(el('p', `${cached ? 'Cached' : 'Fresh'} · ${data.rows.length} rows · ${data.timezone} · ${new Date(executedAt * 1000).toLocaleTimeString()}`, 'hint'));
    if (svg) { const image = el('img'); image.alt = card.name; image.className = 'report-chart'; image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg); node.append(image); }
    if (!data.rows.length) node.append(el('p', 'No rows match these filters.', 'empty'));
    const details = el('details'); details.open = !svg; details.append(el('summary', 'Result data'));
    const scroll = el('div', undefined, 'report-table-scroll'), table = el('table'), head = el('thead'), header = el('tr');
    data.columns.forEach(c => { const th = el('th', c.name); th.title = c.type; header.append(th); }); head.append(header); table.append(head);
    const body = el('tbody'); data.rows.forEach(row => { const tr = el('tr'); row.forEach(v => tr.append(el('td', v === null ? '—' : String(v)))); body.append(tr); }); table.append(body); scroll.append(table); details.append(scroll); node.append(details);
    const meta = el('details'); meta.append(el('summary', 'Read details'), el('p', `Snapshot ${data.snapshotId ?? 'empty'}${card.revision ? ` · Report revision ${card.revision}` : ''}`, 'hint')); node.append(meta); return node;
  }
  return {show, open, home, reset, syncState, fromTable, get routeId() { return selected?.id; }};
})();
