'use strict';
// The Files view: every file of the project at a branch, YAML and seeds included. Files on main are
// read-only; writers edit on a branch (main changes only through a merge by a team administrator).
// Uses the helpers of app.js (h, fill, api, go, route, project, ...), which loads after this file.

const TEMPLATES = {
  sql: '-- A new model. Describe it and its columns in a YAML file next to it.\nselect 1 as id\n',
  yml: 'version: 2\n\nmodels:\n  - name: my_model\n    description: What one row means.\n    columns:\n      - name: id\n        description: Unique id.\n        data_tests: [unique, not_null]\n',
  csv: 'id,name\n1,example\n',
  md: '# Notes\n',
};
// The branch head this view last read or wrote: every commit expects it, so none overwrites another's.
let filesAt = {revision: null, files: []};
const NEW_FILE = /^[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}\.(sql|yml|yaml|csv|md)$/;

async function renderFiles() {
  const id = project.id, ref = route.ref, token = ++detailToken;
  const writer = WRITERS.includes(project.role), onMain = ref === 'main';
  const [listing, branches] = await Promise.all([
    api(`/projects/${id}/files?ref=${encodeURIComponent(ref)}`), api(`/projects/${id}/branches`)]);
  if (token !== detailToken) return;
  filesAt = {revision: listing.revision, files: listing.files};
  const names = branches.map(b => b.name);
  if (!names.includes(ref)) names.push(ref);

  const select = h('select', {'aria-label': 'Branch'}, ...names.map(name => h('option', {value: name, selected: name === ref, text: name})));
  select.onchange = () => go({ref: select.value});
  const toolbar = h('div', {class: 'toolbar'},
    h('label', {class: 'branch'}, h('span', {class: 'eyebrow', text: 'Branch'}), select),
    h('span', {class: 'small', id: 'files-head', text: `${listing.files.length} files @ ${listing.revision.slice(0, 7)}`}),
    writer ? h('div', {class: 'toolbar-actions'}, h('button', {type: 'button', class: 'quiet', onclick: () => newFile(), text: 'New file'})) : null);

  const tree = h('nav', {class: 'file-list', 'aria-label': 'Files'});
  let folder = null;
  for (const file of listing.files) {
    const parts = file.path.split('/'), dir = parts.slice(0, -1).join('/');
    if (dir !== folder) { folder = dir; tree.append(h('p', {class: 'eyebrow', text: dir || '/'})); }
    tree.append(h('button', {type: 'button', 'aria-current': String(file.path === route.file), title: file.path,
      onclick: () => go({file: file.path}, true), text: parts.at(-1)}));
  }
  const pane = h('div', {class: 'card file-pane'});
  fill($('#files-view'), toolbar, h('div', {class: 'files'}, tree, pane));
  if (!route.file || !listing.files.some(f => f.path === route.file)) {
    fill(pane, h('p', {class: 'small', text: onMain && writer
      ? 'Choose a file. main is read-only here: open a file and start a branch to change it, or pick a branch above.'
      : 'Choose a file.'}));
    return;
  }
  await openFile(pane, listing, writer && !onMain);
}

async function openFile(pane, listing, editable) {
  const id = project.id, path = route.file, ref = route.ref, token = detailToken;
  fill(pane, h('p', {class: 'eyebrow', text: 'Loading…'}));
  const file = await api(`/projects/${id}/files/${filePath(path)}?ref=${encodeURIComponent(listing.revision)}`);
  if (token !== detailToken) return;
  const status = h('p', {class: 'small', role: 'status'});
  const say = (words, error = false) => { status.textContent = words; status.classList.toggle('error', error); };
  const heading = h('div', {}, h('p', {class: 'eyebrow', text: `${ref} @ ${listing.revision.slice(0, 7)}`}), h('h2', {text: path}));

  if (!editable) {
    const start = WRITERS.includes(project.role) ? startBranch(path, listing.revision, say) : null;
    fill(pane, heading, h('pre', {class: 'code file-view', text: file.content}), start, status);
    return;
  }
  const text = h('textarea', {class: 'code editor', spellcheck: 'false', rows: '24', 'aria-label': `Contents of ${path}`});
  text.value = file.content;
  text.addEventListener('keydown', event => {
    if (event.key !== 'Tab' || event.shiftKey) return;
    event.preventDefault();
    text.setRangeText('  ', text.selectionStart, text.selectionEnd, 'end');
  });
  const message = h('input', {type: 'text', value: `Edit ${path.split('/').at(-1)}`, autocomplete: 'off'});
  const after = h('div', {class: 'tabs'});
  const save = h('button', {type: 'button', text: 'Save'});
  save.onclick = async () => {
    save.disabled = true;
    try {
      const result = await api(`/projects/${id}/commits`, {method: 'POST', body: JSON.stringify({
        branch: ref, files: {[path]: text.value}, message: message.value.trim() || `Edit ${path}`, expected_revision: filesAt.revision})});
      moved(result.revision); rememberBranch(ref);
      say(`Saved to ${ref} @ ${result.revision.slice(0, 7)}.`);
      branchActions(after, ref, say);
    } catch (error) {
      say(error.code === 'stale_branch' ? `${ref} changed since you opened it. Copy your edits, then reload.` : error.message, true);
    } finally { save.disabled = false; }
  };
  const remove = h('button', {type: 'button', class: 'danger', text: 'Delete file'});
  remove.onclick = async () => {
    if (!confirm(`Delete ${path} on ${ref}?`)) return;
    try {
      await api(`/projects/${id}/commits`, {method: 'POST', body: JSON.stringify({
        branch: ref, files: {[path]: null}, message: `Delete ${path}`, expected_revision: filesAt.revision})});
      go({file: null}, true);
    } catch (error) { say(error.message, true); }
  };
  fill(pane, heading, text,
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Commit message'}), message),
    h('div', {class: 'tabs'}, save, remove), status, after);
}

// On main a writer starts a branch from the revision shown, then edits there.
function startBranch(path, revision, say) {
  const stem = path.split('/').at(-1).replace(/\.[^.]+$/, '');
  const name = h('input', {type: 'text', value: savedBranch() || `edit/${stem}`, spellcheck: 'false', autocomplete: 'off'});
  const button = h('button', {type: 'button', text: 'Edit on this branch'});
  button.onclick = async () => {
    const branch = name.value.trim();
    if (!branch || branch === 'main') return say('Choose a branch other than main. main changes only through a merge.', true);
    button.disabled = true;
    try {
      const existing = (await api(`/projects/${project.id}/branches`)).some(b => b.name === branch);
      if (!existing) await api(`/projects/${project.id}/branches`, {method: 'POST', body: JSON.stringify({name: branch, source: revision})});
      rememberBranch(branch);
      go({ref: branch});
    } catch (error) { say(error.message, true); button.disabled = false; }
  };
  return h('div', {class: 'edit'},
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'main is read-only · edit on a branch'}), name),
    h('div', {class: 'tabs'}, button));
}

// After a save: build the branch in development, and for administrators merge it.
function branchActions(box, ref, say) {
  const development = project.environments.find(e => e.environment === 'development');
  const build = async event => {
    event.currentTarget.disabled = true;
    try {
      const run = await api(`/projects/${project.id}/runs`, {method: 'POST', body: JSON.stringify({command: 'build', environment: 'development', ref})});
      go({view: 'runs', environment: 'development', run: run.id, file: null});
    } catch (error) { say(error.message, true); }
  };
  const merge = async event => {
    if (!confirm(`Merge ${ref} into main? Acceptance and production build main.`)) return;
    event.currentTarget.disabled = true;
    try {
      const result = await api(`/projects/${project.id}/merge`, {method: 'POST', body: JSON.stringify({branch: ref})});
      say(result.merged ? `Merged ${ref} into main @ ${result.revision.slice(0, 7)}.` : `main already contains ${ref}.`);
    } catch (error) {
      say(error.code === 'merge_conflict' ? `${error.message} Conflicting: ${(error.detail?.files || []).join(', ')}.` : error.message, true);
    }
  };
  fill(box,
    development?.enabled ? h('button', {class: 'quiet', type: 'button', onclick: build, text: 'Build branch in Development'}) : null,
    ADMINS.includes(project.role) ? h('button', {class: 'quiet', type: 'button', onclick: merge, text: 'Merge to main'}) : null);
}

function moved(revision) {
  filesAt.revision = revision;
  const head = $('#files-head');
  if (head) head.textContent = `${filesAt.files.length} files @ ${revision.slice(0, 7)}`;
}

function newFile() {
  const pane = $('#files-view .file-pane');
  if (route.ref === 'main') {
    fill(pane, h('h2', {text: 'New file'}), h('p', {class: 'small', text: 'main is read-only. Pick or start a branch first: open any file and choose "Edit on this branch", or choose a branch above.'}));
    return;
  }
  const path = h('input', {type: 'text', placeholder: 'models/marts/orders.sql', spellcheck: 'false', autocomplete: 'off'});
  const status = h('p', {class: 'small', role: 'status'});
  const create = h('button', {type: 'button', text: 'Create'});
  create.onclick = async () => {
    const value = path.value.trim();
    if (!NEW_FILE.test(value) || value.includes('..')) {
      status.textContent = 'Use a relative path ending in .sql, .yml, .csv or .md, such as models/marts/orders.sql.';
      status.className = 'small error'; return;
    }
    if (filesAt.files.some(f => f.path === value)) { go({file: value}, true); return; }
    create.disabled = true;
    try {
      const extension = value.split('.').at(-1).replace('yaml', 'yml');
      await api(`/projects/${project.id}/commits`, {method: 'POST', body: JSON.stringify({
        branch: route.ref, files: {[value]: TEMPLATES[extension]}, message: `Add ${value}`, expected_revision: filesAt.revision})});
      go({file: value}, true);
    } catch (error) { status.textContent = error.message; status.className = 'small error'; create.disabled = false; }
  };
  fill(pane, h('h2', {text: `New file on ${route.ref}`}),
    h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Path'}), path),
    h('p', {class: 'small', text: 'Models go under models/, seeds (CSV) under seeds/, tests under tests/. A YAML file next to a model describes it and its columns.'}),
    h('div', {class: 'tabs'}, create), status);
  path.focus();
}
