/* Data shares: team administrators hand selected tables and views to an external party. */
const VIEW_WARNING = 'A view shares only its definition. The recipient’s engine reads the underlying tables with this credential, so add every table the view reads. The recipient can then read those tables in full; a view is not a row or column filter.';
const objectKey = o => JSON.stringify([o.kind, o.namespace, o.name]);
const objectPath = o => [...o.namespace, o.name].join('.');
function canShare() { return ['admin', 'bucket-admin'].includes(state.activeRole); }
function field(label, input) { const wrap = element('label', label); wrap.append(input); return wrap; }
function textInput(value, maxLength, required = false) { const input = element('input'); input.value = value || ''; input.maxLength = maxLength; input.required = required; return input; }
function copyButton(label, text, done) { return action(label, async () => { await navigator.clipboard.writeText(text); notice(done); }); }

function sharesDisclosure(db) {
  const section = element('details', undefined, 'catalog-disclosure'), host = element('div', undefined, 'shares');
  section.append(element('summary', 'Data shares'), host);
  section.addEventListener('toggle', () => { if (section.open && !host.childElementCount) loadShares(host, db); });
  return section;
}

async function loadShares(host, db) {
  host.replaceChildren(element('p', 'Loading data shares…', 'catalog-empty'));
  try { const result = await api(`/shares?database=${encodeURIComponent(db.id)}`); if (host.isConnected) renderShares(host, db, result); }
  catch (error) { host.replaceChildren(element('p', error.message, 'catalog-empty')); }
}

function renderShares(host, db, result) {
  const intro = element('p', 'Give an external party read access to selected tables and views in this database. Each share has its own credential, which you can replace or revoke at any time.', 'hint');
  const create = action('New data share', async () => shareForm(host, db)); create.classList.remove('quiet');
  create.disabled = result.shares.length >= result.limits.shares;
  const rows = result.shares.map(share => {
    const missing = share.objects.filter(o => !o.granted), extra = share.extraGrants || [];
    const objects = element('div');
    objects.append(element('span', `${share.objects.length} selected`));
    if (missing.length) objects.append(element('small', `Not granted (dropped or recreated): ${missing.map(objectPath).join(', ')}. Edit and save to grant again.`, 'share-drift'));
    if (extra.length) objects.append(element('small', `Still granted under a new name: ${extra.map(objectPath).join(', ')}. Edit and save to remove.`, 'share-drift'));
    const actions = element('div', undefined, 'share-actions');
    if (share.status === 'revoking') actions.append(element('span', 'Revoking…', 'hint'));
    else actions.append(action('Edit', async () => shareForm(host, db, share)), action('New secret', async () => issued(host, db, await api(`/shares/${share.id}/rotate`, 'POST'), 'The previous secret no longer works.')), revokeButton(host, db, share));
    const name = element('div'); name.append(element('strong', share.name), element('small', share.recipient || '—'));
    return [name, objects, share.expiresAt ? `${share.expiresAt.slice(0, 10)} · end of day UTC` : 'Never', `${share.createdBy || '—'} · ${timestamp(share.createdAt)}`, actions];
  });
  const list = rows.length ? dataGrid(['Share / recipient', 'Tables and views', 'Expires', 'Created', ''], rows, 'Data shares') : element('p', 'Nothing in this database is shared.', 'catalog-empty');
  host.replaceChildren(intro, create, list);
}

function revokeButton(host, db, share) {
  const wrap = element('span', undefined, 'share-actions'), start = element('button', 'Revoke', 'quiet'); start.type = 'button';
  start.addEventListener('click', () => {
    const cancel = element('button', 'Cancel', 'quiet'); cancel.type = 'button'; cancel.addEventListener('click', () => wrap.replaceChildren(start));
    wrap.replaceChildren(action(`Revoke ${share.name} now`, async () => { await api(`/shares/${share.id}`, 'DELETE'); notice('Share revoked. Its credential no longer opens anything.'); await loadShares(host, db); }), cancel);
  });
  wrap.append(start); return wrap;
}

function objectPicker(db, selected, onChange) {
  const tree = element('div', undefined, 'share-tree');
  function choice(o) {
    const label = element('label', undefined, 'share-object'), box = element('input'); box.type = 'checkbox'; box.checked = selected.has(objectKey(o)); box.dataset.objectKey = objectKey(o);
    box.addEventListener('change', () => { if (box.checked) selected.set(objectKey(o), o); else selected.delete(objectKey(o)); onChange(); });
    label.append(box, symbol(o.kind), element('span', o.name), element('small', o.kind === 'table' ? 'Iceberg table' : 'Iceberg view')); return label;
  }
  async function fill(host, ns) {
    host.replaceChildren(element('p', 'Loading…', 'hint'));
    try {
      const query = new URLSearchParams({database: db.id}); ns.forEach(part => query.append('namespace', part));
      const contents = await api(`/contents?${query}`), nodes = [];
      contents.namespaces.forEach(parts => { const branch = element('details'), body = element('div'); branch.append(element('summary', parts.at(-1)), body); branch.addEventListener('toggle', () => { if (branch.open && !body.childElementCount) fill(body, parts); }); nodes.push(branch); });
      contents.tables.forEach(t => nodes.push(choice({kind: 'table', namespace: ns, name: t.name})));
      contents.views.forEach(v => nodes.push(choice({kind: 'view', namespace: ns, name: v.name})));
      host.replaceChildren(...(nodes.length ? nodes : [element('p', 'Empty.', 'hint')]));
    } catch (error) { host.replaceChildren(element('p', error.message, 'hint')); }
  }
  fill(tree, []);
  return tree;
}

function shareForm(host, db, share) {
  const form = element('form', undefined, 'share-form'), selected = new Map((share?.objects || []).map(o => [objectKey(o), {kind: o.kind, namespace: o.namespace, name: o.name}]));
  const name = textInput(share?.name, 48, true); name.pattern = '[a-z][a-z0-9_-]{2,47}'; name.disabled = Boolean(share); name.title = 'Lowercase letters, digits, - and _; 3 to 48 characters.';
  const recipient = textInput(share?.recipient, 120), description = textInput(share?.description, 280);
  const expiry = element('input'); expiry.type = 'date'; expiry.min = new Date(Date.now() + 86400000).toISOString().slice(0, 10); expiry.value = share?.expiresAt ? share.expiresAt.slice(0, 10) : '';
  const chosen = element('div', undefined, 'share-selection'), warning = element('p', VIEW_WARNING, 'share-warning'); warning.setAttribute('role', 'note');
  const save = element('button', share ? 'Save share' : 'Create share and show credential'); save.type = 'submit';
  function refresh() {
    const objects = [...selected.values()].sort((a, b) => objectPath(a).localeCompare(objectPath(b)));
    chosen.replaceChildren(element('div', `SELECTED · ${objects.length}`, 'eyebrow'), ...objects.map(o => {
      const row = element('div', undefined, 'share-object'), remove = element('button', 'Remove', 'ghost'); remove.type = 'button';
      remove.addEventListener('click', () => { selected.delete(objectKey(o)); const box = [...form.querySelectorAll('input[type=checkbox]')].find(b => b.dataset.objectKey === objectKey(o)); if (box) box.checked = false; refresh(); });
      row.append(symbol(o.kind), element('span', objectPath(o)), remove); return row;
    }));
    warning.hidden = !objects.some(o => o.kind === 'view');
  }
  const picker = element('fieldset'); picker.append(element('legend', 'Tables and views'), element('p', 'The recipient can read exactly these objects and cannot list anything else. Share tables and views by their full name.', 'hint'), objectPicker(db, selected, refresh), chosen, warning);
  const cancel = element('button', 'Cancel', 'quiet'); cancel.type = 'button'; cancel.addEventListener('click', () => loadShares(host, db));
  const buttons = element('div', undefined, 'share-actions'); buttons.append(save, cancel);
  form.append(element('h3', share ? `Edit ${share.name}` : 'New data share'), field('Share name', name), field('Recipient', recipient), field('Description', description), field('Expires at the end of this UTC day (optional)', expiry), picker, buttons);
  form.addEventListener('submit', event => {
    event.preventDefault();
    busy(save, async () => {
      const objects = [...selected.values()];
      if (!objects.length) throw new Error('Select at least one table.');
      if (!objects.some(o => o.kind === 'table')) throw new Error('Select the tables a shared view reads.');
      const body = {recipient: recipient.value.trim(), description: description.value.trim(), objects, expiresAt: expiry.value ? `${expiry.value}T23:59:59Z` : null};
      if (share) { await api(`/shares/${share.id}`, 'PATCH', body); notice('Share saved.'); await loadShares(host, db); }
      else issued(host, db, await api('/shares', 'POST', {...body, database: db.id, name: name.value}), 'Share created.');
    });
  });
  refresh(); host.replaceChildren(form); name.disabled ? recipient.focus() : name.focus();
}

function pythonSnippet(connection) {
  const s = JSON.stringify, table = connection.identifiers.find(i => i.kind === 'table');
  return `from pyiceberg.catalog import load_catalog

catalog = load_catalog(
    "shared",
    type="rest",
    uri=${s(connection.uri)},
    warehouse=${s(connection.warehouse)},
    credential=${s(connection.credential)},
    scope=${s(connection.scope)},
    **{
        "oauth2-server-uri": ${s(connection.oauth2ServerUri)},
        "header.X-Iceberg-Access-Delegation": ${s(connection.accessDelegation)},
    },
)
table = catalog.load_table(${s(table.identifier)})
print(table.scan().to_arrow())`;
}

function issued(host, db, result, message) {
  // The secret lives in this panel only. Closing it discards the last copy the portal ever had.
  const {share, credentials, connection} = result, panel = element('section', undefined, 'share-issued'), snippet = pythonSnippet(connection);
  panel.append(element('h3', `Credential for ${share.name}`), element('p', 'This secret is shown once. Send it to the recipient over a secure channel; replace it with “New secret” if it is lost or leaked.', 'share-warning'));
  panel.append(facts([['Client ID', credentials.clientId], ['Client secret', credentials.clientSecret], ['Catalog URI', connection.uri], ['Catalog / warehouse', connection.warehouse], ['Token endpoint', connection.oauth2ServerUri], ['Scope', connection.scope], ['Storage endpoint', connection.s3Endpoint]]));
  panel.append(element('h3', 'Shared names'), element('p', 'The credential cannot list namespaces or tables. The recipient loads these names directly.', 'hint'), dataGrid(['Kind', 'Identifier'], connection.identifiers.map(i => [i.kind, i.identifier]), 'Shared identifiers'));
  panel.append(element('h3', 'PyIceberg'), element('pre', snippet, 'view-sql'));
  const done = action('Done, I stored the secret', async () => loadShares(host, db)); done.classList.remove('quiet');
  const buttons = element('div', undefined, 'share-actions'); buttons.append(copyButton('Copy credential', connection.credential, 'Credential copied.'), copyButton('Copy PyIceberg snippet', snippet, 'Snippet copied.'), done);
  panel.append(buttons); host.replaceChildren(panel); notice(message);
}
