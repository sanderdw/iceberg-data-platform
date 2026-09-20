/* Catalog inspection shares the portal's authenticated API and workspace context. */
let selectedObject = null;

function displayValue(value) {
  if (value === null || value === undefined) return '—';
  return typeof value === 'object' ? JSON.stringify(value) : String(value);
}
function timestamp(value) { return value ? new Date(value).toLocaleString() : '—'; }
function dataGrid(headers, rows, caption) {
  if (!rows.length) return element('p', 'No entries available.', 'catalog-empty');
  const wrap = element('div', undefined, 'data-grid');
  wrap.tabIndex = 0; wrap.setAttribute('role', 'region'); wrap.setAttribute('aria-label', caption);
  const table = element('table'); table.append(element('caption', caption, 'sr-only'));
  const head = element('thead'), heading = element('tr');
  headers.forEach(h => { const cell = element('th', h); cell.scope = 'col'; heading.append(cell); });
  head.append(heading); table.append(head);
  const body = element('tbody');
  rows.forEach(row => { const tr = element('tr'); row.forEach(value => { const td = element('td'); if (value instanceof Node) td.append(value); else td.textContent = displayValue(value); tr.append(td); }); body.append(tr); });
  table.append(body); wrap.append(table); return wrap;
}
function facts(entries) {
  const list = element('dl', undefined, 'catalog-facts');
  entries.forEach(([key, value]) => { const item = element('div'); if (['Location', 'Catalog URI'].includes(key)) item.className = 'catalog-fact-wide'; item.append(element('dt', key), element('dd', displayValue(value))); list.append(item); });
  return list;
}
function propertyGrid(values) { return dataGrid(['Property', 'Value'], Object.entries(values || {}).sort(([a], [b]) => a.localeCompare(b)), 'Properties'); }
function action(label, callback) { const button = element('button', label, 'quiet'); button.type = 'button'; button.addEventListener('click', () => busy(button, callback)); return button; }

function renderCatalogListing(detail, contents, rows) {
  const host = $('#objects'), summary = element('section', undefined, 'catalog-summary');
  const entries = [['Namespaces', contents.namespaces.length], ['Tables', contents.tables.length], ['Views', contents.views.length]];
  if (detail.kind === 'database') {
    const db = detail.database;
    entries.unshift(['Database', db.name], ['Environment', envNames[db.environment]], ['Team', state.teams.find(t => t.id === db.team)?.name || db.team]);
    summary.append(facts(entries));
    const connection = element('details', undefined, 'catalog-disclosure');
    connection.append(element('summary', 'Connection information'), facts([['Catalog / warehouse', db.id], ['Catalog URI', detail.catalogUri], ['Protocol', 'Iceberg REST']]));
    summary.append(connection);
  } else {
    summary.append(facts(entries));
    const props = element('details', undefined, 'catalog-disclosure'); props.append(element('summary', 'Namespace properties'), propertyGrid(detail.properties)); summary.append(props);
  }
  const searchLabel = element('label', 'Filter objects', 'catalog-search');
  const search = element('input'); search.type = 'search'; search.placeholder = 'Find a namespace, table or view'; searchLabel.append(search);
  const list = element('div'); list.append(...rows);
  const empty = element('p', rows.length ? 'No matching objects.' : 'This namespace is empty.', 'catalog-empty'); empty.hidden = rows.length > 0;
  search.addEventListener('input', () => { let visible = 0; rows.forEach(row => { row.hidden = !row.querySelector('.object-label').textContent.toLowerCase().includes(search.value.toLowerCase()); if (!row.hidden) visible++; }); empty.hidden = visible > 0; });
  host.replaceChildren(summary, searchLabel, list, empty);
}

async function inspectObject(db, ns, kind, name) {
  clearNotice(); const version = ++browseVersion;
  database = db; namespace = [...ns]; selectedObject = {kind, name}; $('#create-report').hidden = kind !== 'table'; saveRoute();
  $('#browser').hidden = false; $('#examples').hidden = true;
  $('#namespace-title').textContent = name;
  $('#objects').replaceChildren(element('p', 'Loading details…', 'catalog-empty'));
  const query = new URLSearchParams({database: db, kind, name}); ns.forEach(part => query.append('namespace', part));
  // Replace any prior object breadcrumb while retaining namespace navigation.
  $('#breadcrumbs').querySelectorAll('[data-object-crumb]').forEach(node => node.remove());
  const slash = element('span', '/'), crumb = element('span', name); slash.dataset.objectCrumb = ''; crumb.dataset.objectCrumb = ''; crumb.setAttribute('aria-current', 'page'); $('#breadcrumbs').append(slash, crumb);
  try {
    const detail = await api(`/details?${query}`);
    if (version !== browseVersion) return;
    renderObjectDetails(detail, db, ns, name, version);
  } catch (error) { if (version === browseVersion) { $('#objects').replaceChildren(element('p', error.message, 'catalog-empty')); notice(error.message, true); } }
}

function renderObjectDetails(detail, db, ns, name, version) {
  const host = $('#objects'), toolbar = element('div', undefined, 'catalog-actions');
  toolbar.append(element('span', detail.kind === 'table' ? 'APACHE ICEBERG TABLE' : 'APACHE ICEBERG VIEW', 'eyebrow'));
  toolbar.append(action('Copy identifier', async () => { await navigator.clipboard.writeText([db, ...ns, name].map(p => '"' + p.replaceAll('"', '""') + '"').join('.')); notice('Identifier copied.'); }));
  const tabs = element('div', undefined, 'catalog-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', `${detail.kind} details`);
  const panel = element('section', undefined, 'catalog-panel'); panel.id = 'catalog-tab-panel'; panel.setAttribute('role', 'tabpanel'); panel.tabIndex = 0;
  const tabButtons = [];
  function tab(label, render) {
    const button = element('button', label, 'quiet'); button.type = 'button'; button.id = `tab-${tabButtons.length}`;
    button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', panel.id);
    const select = () => {
      tabButtons.forEach(b => { b.setAttribute('aria-selected', String(b === button)); b.tabIndex = b === button ? 0 : -1; });
      panel.setAttribute('aria-labelledby', button.id); panel.replaceChildren(); render(panel);
    };
    button.addEventListener('click', select); tabButtons.push(button); tabs.append(button); return select;
  }
  tabs.addEventListener('keydown', event => {
    const index = tabButtons.indexOf(document.activeElement);
    if (index < 0 || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabButtons.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabButtons.length) % tabButtons.length;
    tabButtons[next].focus(); tabButtons[next].click();
  });
  const overview = tab('Overview', p => {
    const entries = [['Format version', detail.formatVersion], ['Schema ID', detail.schemaId], ['UUID', detail.uuid], ['Location', detail.location]];
    if (detail.kind === 'table') {
      entries.unshift(['Current snapshot', detail.currentSnapshotId], ['Last updated', timestamp(detail.updatedAt)]);
      const current = detail.snapshots.find(s => s.id === detail.currentSnapshotId);
      p.append(facts(entries));
      p.append(element('h3', 'Current snapshot statistics'));
      p.append(facts([['Data file records', current?.summary['total-records']], ['Data files', current?.summary['total-data-files']], ['Data file size (bytes)', current?.summary['total-files-size']], ['Delete files', current?.summary['total-delete-files']]]));
      p.append(element('p', 'Statistics come from snapshot metadata when available. Record totals may include rows affected by delete files.', 'hint'));
    } else { entries.unshift(['Current version', detail.currentVersionId]); p.append(facts(entries)); }
  });
  tab('Schema', p => p.append(dataGrid(['ID', 'Column', 'Type', 'Required', 'Description'], detail.columns.map(c => [c.id, c.name, c.type, c.required ? 'Yes' : 'No', c.doc]), 'Schema columns')));
  if (detail.kind === 'table') {
    let selectedSnapshot = detail.currentSnapshotId;
    const preview = tab('Preview', p => {
      const controls = element('div', undefined, 'preview-controls');
      const label = element('label', 'Snapshot'), picker = element('select'); picker.setAttribute('aria-label', 'Preview snapshot');
      detail.snapshots.forEach(s => { const option = element('option', `${s.id === detail.currentSnapshotId ? 'Current · ' : ''}${timestamp(s.timestamp)} · ${s.id}`); option.value = s.id; picker.append(option); });
      if (!detail.snapshots.length) { const option = element('option', 'Empty table'); option.value = ''; picker.append(option); }
      picker.value = selectedSnapshot || ''; label.append(picker);
      const output = element('div'); output.setAttribute('aria-live', 'polite');
      picker.addEventListener('change', () => { selectedSnapshot = picker.value || null; output.replaceChildren(); });
      controls.append(label, action('Load preview', async () => {
        const snapshot = picker.value || null; picker.disabled = true;
        output.replaceChildren(element('p', 'Reading up to 100 rows…', 'catalog-empty'));
        try {
          const result = await api('/preview', 'POST', {database: db, namespace: ns, table: name, snapshot_id: snapshot, limit: 100});
          if (version !== browseVersion || !output.isConnected) return;
          output.replaceChildren(element('p', `${result.rows.length} rows shown · Snapshot ${result.snapshotId || 'none'}`, 'hint'), dataGrid(result.columns, result.rows.map(row => row.map(value => value === null ? 'NULL' : value)), 'Table preview'));
          if (result.columnsTruncated || result.cellsTruncated) output.append(element('p', 'Display limited to 50 columns and 512 characters per cell.', 'hint'));
        } catch (error) { if (version === browseVersion && output.isConnected) output.replaceChildren(element('p', error.message, 'catalog-empty')); }
        finally { picker.disabled = false; }
      }));
      p.append(element('p', 'Read up to 100 rows from a snapshot using your data permissions. Row order is unspecified.', 'hint'), controls, output);
    });
    tab('Snapshots', p => {
      p.append(element('h3', 'Snapshots'), dataGrid(['Snapshot', 'Committed', 'Operation', 'Parent', ''], detail.snapshots.map(s => [s.id + (s.id === detail.currentSnapshotId ? ' · current' : ''), timestamp(s.timestamp), s.summary.operation, s.parentId, action('Preview snapshot', () => { selectedSnapshot = s.id; preview(); })]), 'Snapshot history'));
      p.append(element('h3', 'Branches & tags'), dataGrid(['Name', 'Type', 'Snapshot', 'Keep snapshots', 'Snapshot age (ms)', 'Reference age (ms)'], detail.refs.map(r => [r.name, r.type, r.snapshotId, r.minSnapshotsToKeep, r.maxSnapshotAgeMs, r.maxRefAgeMs]), 'Branches and tags'));
      p.append(element('h3', 'Current snapshot history'), dataGrid(['Became current', 'Snapshot'], detail.history.map(h => [timestamp(h.timestamp), h.snapshotId]), 'Current snapshot history'));
    });
    tab('Partitioning & sort', p => {
      p.append(element('p', 'Partition transforms and sort orders describe table layout. Historical specifications remain visible.', 'hint'));
      detail.partitionSpecs.forEach(spec => {
        p.append(element('h3', `Partition spec ${spec['spec-id']}${spec['spec-id'] === detail.defaultSpecId ? ' · default' : ''}`));
        p.append(spec.fields.length ? dataGrid(['Field ID', 'Name', 'Source ID', 'Transform'], spec.fields.map(f => [f['field-id'], f.name, f['source-id'], f.transform]), 'Partition specification') : element('p', 'Unpartitioned', 'catalog-empty'));
      });
      detail.sortOrders.forEach(order => {
        p.append(element('h3', `Sort order ${order['order-id']}${order['order-id'] === detail.defaultSortOrderId ? ' · default' : ''}`));
        p.append(order.fields.length ? dataGrid(['Source ID', 'Transform', 'Direction', 'Null order'], order.fields.map(f => [f['source-id'], f.transform, f.direction, f['null-order']]), 'Sort order') : element('p', 'Unsorted', 'catalog-empty'));
      });
    });
  } else {
    tab('SQL definition', p => {
      const current = detail.versions.find(v => v.id === detail.currentVersionId);
      p.append(element('p', 'Execute this definition in an engine that supports its SQL dialect and referenced objects.', 'hint'));
      (current?.representations || []).forEach(r => { p.append(element('h3', r.dialect || 'SQL'), element('pre', r.sql, 'view-sql')); });
      p.append(facts([['Default catalog', current?.defaultCatalog], ['Default namespace', current?.defaultNamespace.join('.')]]));
    });
    tab('Versions', p => {
      detail.versions.forEach(v => {
        const section = element('details', undefined, 'catalog-disclosure'); section.append(element('summary', `Version ${v.id}${v.id === detail.currentVersionId ? ' · current' : ''} · ${timestamp(v.timestamp)}`));
        section.append(facts([['Schema ID', v.schemaId], ['Default catalog', v.defaultCatalog], ['Default namespace', v.defaultNamespace.join('.')]]));
        v.representations.forEach(r => section.append(element('h3', r.dialect), element('pre', r.sql, 'view-sql'))); p.append(section);
      });
    });
  }
  tab('Properties', p => p.append(propertyGrid(detail.properties)));
  host.replaceChildren(toolbar, tabs, panel); overview();
}
