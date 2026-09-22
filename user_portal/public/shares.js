/* Data shares: team administrators hand selected tables and views with teams and external parties. */
const VIEW_WARNING = 'A view shares only its definition. The recipient’s engine reads the underlying tables with their access, so add every table the view reads. The recipient can then read those tables in full; a view is not a row or column filter.';
const objectKey = o => JSON.stringify([o.kind, o.namespace, o.name]);
const objectPath = o => [...o.namespace, o.name].join('.');
function canShare() { return ['admin', 'bucket-admin'].includes(state?.activeRole); }
function field(label, input) { const wrap = element('label', label); wrap.append(input); return wrap; }
function textInput(value, maxLength, required = false) { const input = element('input'); input.value = value || ''; input.maxLength = maxLength; input.required = required; return input; }
function copyButton(label, text, done) { return action(label, async () => { await navigator.clipboard.writeText(text); notice(done); }); }

function renderTeamShares() {
  $('#share-permissions').hidden = canShare();
  const root = $('#team-shares');
  if (root.childElementCount) return;
  const received = element('section', undefined, 'database-shares'); root.append(received);
  loadReceivedShares(received);
  if (!state.databases.length) root.append(element('p', 'This team has no databases in this environment yet.', 'catalog-empty'));
  state.databases.filter(db => !db.shared).forEach(db => {
    const section = element('section', undefined, 'database-shares'), host = element('div', undefined, 'shares');
    section.append(element('h3', db.name), host); root.append(section);
    loadShares(host, db);
  });
}
document.querySelector('#refresh-shares').addEventListener('click', event => busy(event.currentTarget, async () => {
  await loadState(); $('#team-shares').replaceChildren(); renderTeamShares();
}));

async function loadReceivedShares(host) {
  try {
    const shares = await api('/received-shares');
    if (!host.isConnected) return;
    host.replaceChildren(element('h3', 'Shared with this team'), element('p', 'These databases are available in Catalog and Notebooks. Shared objects are read-only.', 'hint'));
    host.append(shares.length ? dataGrid(['Share / database', 'Catalog ID', 'Tables and views', 'Expires'], shares.map(s => [s.name + ' / ' + s.databaseName, s.database, s.objects.map(o => objectPath(o) + (o.granted ? '' : ' (unavailable)')).join(', '), s.expiresAt || 'Never']), 'Received data shares') : element('p', 'No data shares received in this environment.', 'catalog-empty'));
  } catch (error) { host.replaceChildren(element('p', error.message, 'catalog-empty')); }
}

async function refreshTeamShares() {
  const received = $('#team-shares').firstElementChild;
  if (received?.tagName === 'SECTION') await loadReceivedShares(received);
  await Promise.all(state.databases.filter(db => !db.shared).map(db => {
    const host = [...$('#team-shares').querySelectorAll('.shares')].find(h => h.dataset.database === db.id);
    if (host && !host.querySelector('.share-form, .share-issued, .share-confirmation')) return loadShares(host, db, true);
  }));
}
async function loadShares(host, db, background = false) {
  host.dataset.database = db.id;
  if (!background) host.replaceChildren(element('p', 'Loading data shares…', 'catalog-empty'));
  const current = host.firstChild;
  try {
    const result = await api(`/shares?database=${encodeURIComponent(db.id)}`);
    if (!host.isConnected || host.firstChild !== current) return;
    if (background && (pendingActions || host.querySelector('.share-form, .share-issued, .share-confirmation') || host.dataset.shares === JSON.stringify(result))) return;
    renderShares(host, db, result);
  } catch (error) { if (!background) host.replaceChildren(element('p', error.message, 'catalog-empty')); }
}

function renderShares(host, db, result) {
  const intro = element('p', 'Share selected tables and views with another team, externally, or both. Team members use their own accounts; external recipients receive a separate credential.', 'hint');
  const create = action('New data share', async () => shareForm(host, db)); create.classList.remove('quiet');
  create.disabled = !canShare() || result.shares.length >= result.limits.shares;
  if (canShare() && create.disabled) create.title = 'This database has reached its data share limit.';
  const rows = result.shares.map(share => {
    const missing = share.objects.filter(o => !o.granted), extra = share.extraGrants || [];
    const objects = element('div');
    objects.append(element('span', `${share.objects.length} selected`));
    if (missing.length) objects.append(element('small', `Not granted (dropped or recreated): ${missing.map(objectPath).join(', ')}. Edit and save to grant again.`, 'share-drift'));
    if (extra.length) objects.append(element('small', `Still granted under a new name: ${extra.map(objectPath).join(', ')}. Edit and save to remove.`, 'share-drift'));
    const actions = element('div', undefined, 'share-actions');
    if (share.status === 'revoking') actions.append(element('span', 'Revoking…', 'hint'));
    else actions.append(action('Edit', async () => shareForm(host, db, share)), ...(share.external !== false ? [action('New secret', async () => issued(host, db, await api(`/shares/${share.id}/rotate`, 'POST'), 'The previous secret no longer works.'))] : []), revokeButton(host, db, share));
    const name = element('div'); name.append(element('strong', share.name), element('small', [share.recipientTeam ? `Team: ${share.recipientTeamName || share.recipientTeam}` : '', share.external !== false ? `External: ${share.recipient || '—'}` : ''].filter(Boolean).join(' · ')));
    return [name, objects, share.expiresAt ? `${share.expiresAt.slice(0, 10)} · end of day UTC` : 'Never', `${share.createdBy || '—'} · ${timestamp(share.createdAt)}`, actions];
  });
  const list = rows.length ? dataGrid(['Share / recipient', 'Tables and views', 'Expires', 'Created', ''], rows, 'Data shares') : element('p', 'Nothing in this database is shared.', 'catalog-empty');
  host.replaceChildren(intro, create, list);
  host.dataset.shares = JSON.stringify(result);
  if (!canShare()) host.querySelectorAll('button').forEach(button => {
    button.disabled = true;
    button.title = 'Only team administrators can manage data shares.';
    button.setAttribute('aria-describedby', 'share-permissions');
  });
}

function revokeButton(host, db, share) {
  const wrap = element('span', undefined, 'share-actions'), start = element('button', 'Revoke', 'quiet'); start.type = 'button';
  start.addEventListener('click', () => {
    wrap.classList.add('share-confirmation');
    const cancel = element('button', 'Cancel', 'quiet'); cancel.type = 'button'; cancel.addEventListener('click', () => { wrap.classList.remove('share-confirmation'); wrap.replaceChildren(start); });
    wrap.replaceChildren(action(`Revoke ${share.name} now`, async () => { await api(`/shares/${share.id}`, 'DELETE'); notice('Share revoked. Recipients no longer have access through this share.'); await loadShares(host, db); }), cancel);
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

async function shareForm(host, db, share) {
  const teams = await api('/share-teams');
  if (!host.isConnected) return;
  const form = element('form', undefined, 'share-form'), selected = new Map((share?.objects || []).map(o => [objectKey(o), {kind: o.kind, namespace: o.namespace, name: o.name}]));
  const maxObjects = JSON.parse(host.dataset.shares).limits.objects;
  const name = textInput(share?.name, 48, true); name.pattern = '[a-z][a-z0-9_\\-]{2,47}'; name.disabled = Boolean(share); name.title = 'Use 3–48 lowercase letters, digits, hyphens or underscores, starting with a letter.';
  const nameField = field('Share name', name), nameHelp = element('small', name.title, 'hint');
  nameHelp.id = `share-name-help-${db.id}`; name.setAttribute('aria-describedby', nameHelp.id); nameField.append(nameHelp);
  const recipient = textInput(share?.recipient, 120), description = textInput(share?.description, 280);
  const audience = element('fieldset'); audience.append(element('legend', 'Share with'));
  const internal = element('input'); internal.type = 'checkbox'; internal.checked = Boolean(share?.recipientTeam); internal.disabled = Boolean(share);
  const external = element('input'); external.type = 'checkbox'; external.checked = share?.external !== false; external.disabled = Boolean(share);
  const team = element('select'); team.setAttribute('aria-label', 'Recipient team'); team.append(new Option('Choose a team', ''));
  teams.forEach(t => team.append(new Option(t.name, t.id)));
  team.value = share?.recipientTeam || ''; team.disabled = Boolean(share);
  const internalField = field('Another team', internal), externalField = field('Externally', external);
  internalField.className = externalField.className = 'share-object';
  const teamField = field('Recipient team', team);
  audience.append(internalField, teamField, externalField);
  if (share) audience.append(element('p', 'To change sharing destinations, revoke this share and create a new one.', 'hint'));
  function audienceChanged() {
    teamField.hidden = !internal.checked; team.required = internal.checked;
    recipient.disabled = !external.checked;
    if (!share) save.textContent = external.checked ? 'Create share and show credential' : 'Create share';
  }
  internal.addEventListener('change', audienceChanged); external.addEventListener('change', audienceChanged);
  const expiry = element('input'); expiry.type = 'date'; expiry.value = share?.expiresAt ? share.expiresAt.slice(0, 10) : '';
  expiry.min = new Date().toISOString().slice(0, 10);
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
  const picker = element('fieldset'); picker.append(element('legend', 'Tables and views'), element('p', `Select up to ${maxObjects} objects, including at least one table. The recipient can read exactly these objects and cannot list anything else. Share tables and views by their full name.`, 'hint'), objectPicker(db, selected, refresh), chosen, warning);
  const cancel = element('button', 'Cancel', 'quiet'); cancel.type = 'button'; cancel.addEventListener('click', () => loadShares(host, db));
  const buttons = element('div', undefined, 'share-actions'); buttons.append(save, cancel);
  form.append(element('h3', share ? `Edit ${share.name}` : 'New data share'), nameField, audience, field('Recipient', recipient), field('Description', description), field('Expires at the end of this UTC day (optional)', expiry), picker, buttons);
  form.addEventListener('submit', event => {
    event.preventDefault();
    busy(save, async () => {
      const objects = [...selected.values()];
      if (!objects.length) throw new Error('Select at least one table.');
      if (objects.length > maxObjects) throw new Error(`Select no more than ${maxObjects} tables and views.`);
      if (!objects.some(o => o.kind === 'table')) throw new Error('Select the tables a shared view reads.');
      const body = {recipient: recipient.value.trim(), description: description.value.trim(), objects, expiresAt: expiry.value ? `${expiry.value}T23:59:59Z` : null};
      if (share) { await api(`/shares/${share.id}`, 'PATCH', body); notice('Share saved.'); await loadShares(host, db); }
      else {
        if (!internal.checked && !external.checked) throw new Error('Choose another team, external sharing, or both.');
        const result = await api('/shares', 'POST', {...body, database: db.id, name: name.value, external: external.checked, recipientTeam: internal.checked ? team.value : null});
        if (result.credentials) issued(host, db, result, 'Share created.');
        else { notice('Share created. Recipient team members can read the selected objects with their own accounts.'); await loadShares(host, db); }
      }
    });
  });
  audienceChanged(); refresh(); host.replaceChildren(form); name.disabled ? recipient.focus() : name.focus();
}

function duckdbSnippet(share, credentials, connection) {
  const table = share.objects.find(o => o.kind === 'table');
  // Escape SQL values first, then the surrounding Python multiline string.
  const literal = value => ("'" + value.replaceAll("'", "''") + "'").replaceAll('\\', '\\\\').replaceAll('"', '\\"');
  const identifier = value => '"' + value.replaceAll('"', '""') + '"';
  // DuckDB represents nested Iceberg namespaces as one dotted schema name.
  const reference = ['shared', table.namespace.join('.'), table.name].map(identifier).join('.');
  const sharedObjects = share.objects.map(o => `# ${o.kind === 'table' ? 'Table' : 'View'}: ${JSON.stringify(objectPath(o))}`).join('\n');
  return `# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "duckdb",
# ]
# ///
"""Read the shared Iceberg table natively with DuckDB and print its contents.

Run with: uv run read_share_duckdb.py
"""

# Shared objects (full Iceberg names):
${sharedObjects}
# The example below queries the first shared table.

import duckdb


def main() -> None:
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute("INSTALL iceberg; LOAD iceberg;")

    con.execute(
        """
        CREATE SECRET shared_secret (
            TYPE ICEBERG,
            CLIENT_ID ${literal(credentials.clientId)},
            CLIENT_SECRET ${literal(credentials.clientSecret)},
            OAUTH2_SERVER_URI ${literal(connection.oauth2ServerUri)},
            OAUTH2_SCOPE ${literal(connection.scope)}
        );
        """
    )
    con.execute(
        """
        ATTACH ${literal(connection.warehouse)} AS shared (
            TYPE ICEBERG,
            ENDPOINT ${literal(connection.uri)},
            SECRET shared_secret,
            ACCESS_DELEGATION_MODE 'vended_credentials'
        );
        """
    )

    con.sql(${JSON.stringify(`SELECT * FROM ${reference}`)}).show()


if __name__ == "__main__":
    main()
`;
}

function issued(host, db, result, message) {
  // The secret lives in this panel only. Closing it discards the last copy the portal ever had.
  const {share, credentials, connection} = result, panel = element('section', undefined, 'share-issued'), snippet = duckdbSnippet(share, credentials, connection);
  panel.append(element('h3', `Credential for ${share.name}`), element('p', 'This secret is shown once. Send it to the recipient over a secure channel; replace it with “New secret” if it is lost or leaked.', 'share-warning'));
  panel.append(facts([['Client ID', credentials.clientId], ['Client secret', credentials.clientSecret], ['Catalog URI', connection.uri], ['Catalog / warehouse', connection.warehouse], ['Token endpoint', connection.oauth2ServerUri], ['Scope', connection.scope], ['Storage endpoint', connection.s3Endpoint]]));
  panel.append(element('h3', 'Shared names'), element('p', 'The credential cannot list namespaces or tables. The recipient loads these names directly.', 'hint'), dataGrid(['Kind', 'Identifier'], connection.identifiers.map(i => [i.kind, i.identifier]), 'Shared identifiers'));
  const done = action('Done, I stored the secret', async () => loadShares(host, db)); done.classList.remove('quiet');
  const buttons = element('div', undefined, 'share-actions'); buttons.append(copyButton('Copy credential', connection.credential, 'Credential copied.'), copyButton('Copy DuckDB snippet', snippet, 'Snippet copied.'), done);
  panel.append(buttons); host.replaceChildren(panel); notice(message);
}
