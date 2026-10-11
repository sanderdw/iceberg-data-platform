'use strict';
// Schedules and Settings (environments, branches). Uses the helpers of app.js, which loads after this file.

const COMMANDS = ['build', 'run', 'test', 'seed'];
// Development schedules are for writers; acceptance and production for team administrators, on main.
const mayManage = environment => (environment === 'development' ? WRITERS : ADMINS).includes(project.role);

async function renderSchedules() {
  const schedules = await api(`/projects/${project.id}/schedules`);
  const status = h('p', {class: 'small', role: 'status'});
  const say = (words, error = false) => { status.textContent = words; status.classList.toggle('error', error); };
  const change = async (schedule, body, method = 'PUT') => {
    try {
      await api(method === 'DELETE' ? `/schedules/${schedule.id}` : `/projects/${project.id}/schedules/${schedule.id}`,
        {method, body: JSON.stringify(body || {})});
      await renderSchedules();
    } catch (error) { say(error.message, true); }
  };
  const fields = s => ({environment: s.environment, cron: s.cron, command: s.command, select: s.selector || '', ref: s.ref, enabled: Boolean(s.enabled)});
  const table = schedules.length ? h('table', {},
    h('thead', {}, h('tr', {}, ...['Environment', 'Cron (UTC)', 'Command', 'Branch', 'Next run', 'State', ''].map(t => h('th', {text: t})))),
    h('tbody', {}, ...schedules.map(s => h('tr', {},
      h('td', {text: LABELS[s.environment]}), h('td', {class: 'nowrap', text: s.cron}),
      h('td', {text: `${s.command} ${s.selector || ''}`.trim()}), h('td', {text: s.ref}),
      h('td', {class: 'nowrap', text: s.enabled ? new Date(s.next_run_at * 1000).toLocaleString() : '—'}),
      h('td', {}, h('span', {class: `chip ${s.enabled ? 'success' : ''}`, text: s.enabled ? 'enabled' : 'paused'})),
      h('td', {class: 'nowrap'}, mayManage(s.environment) ? h('div', {class: 'tabs'},
        h('button', {type: 'button', class: 'quiet', onclick: () => change(s, {...fields(s), enabled: !s.enabled}), text: s.enabled ? 'Pause' : 'Resume'}),
        h('button', {type: 'button', class: 'danger', onclick: () => confirm(`Delete the ${s.cron} schedule in ${LABELS[s.environment]}?`) && change(s, null, 'DELETE'), text: 'Delete'})) : null)))))
    : h('p', {text: 'No schedules yet.'});
  fill($('#schedule-table'), table, status, scheduleForm(say));
}

function scheduleForm(say) {
  const environments = project.environments.filter(e => e.enabled && mayManage(e.environment)).map(e => e.environment);
  if (!environments.length) return h('p', {class: 'small', text: 'You can schedule where dbt is enabled: writers in Development, team administrators in Acceptance and Production.'});
  const environment = h('select', {}, ...environments.map(e => h('option', {value: e, text: LABELS[e]})));
  const cron = h('input', {type: 'text', value: '0 6 * * *', spellcheck: 'false', autocomplete: 'off'});
  const command = h('select', {}, ...COMMANDS.map(c => h('option', {value: c, text: c})));
  const select = h('input', {type: 'text', placeholder: 'everything, or for example tag:daily or orders+', spellcheck: 'false', autocomplete: 'off'});
  const ref = h('input', {type: 'text', value: 'main', spellcheck: 'false', autocomplete: 'off'});
  const refField = h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Branch'}), ref);
  environment.onchange = () => { refField.hidden = environment.value !== 'development'; if (refField.hidden) ref.value = 'main'; };
  environment.onchange();
  const create = h('button', {type: 'submit', text: 'Add schedule'});
  const form = h('form', {class: 'card schedule-form'},
    h('h2', {text: 'New schedule'}),
    h('p', {class: 'small', text: 'Five cron fields in UTC: minute, hour, day of month, month, day of week. "0 6 * * *" runs every day at 06:00 UTC.'}),
    h('div', {class: 'form-grid'},
      h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Environment'}), environment),
      h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Cron (UTC)'}), cron),
      h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Command'}), command),
      h('label', {class: 'field'}, h('span', {class: 'eyebrow', text: 'Select'}), select),
      refField),
    h('div', {class: 'tabs'}, create));
  form.onsubmit = async event => {
    event.preventDefault();
    create.disabled = true;
    try {
      await api(`/projects/${project.id}/schedules`, {method: 'POST', body: JSON.stringify({
        environment: environment.value, cron: cron.value.trim(), command: command.value, select: select.value.trim(),
        ref: ref.value.trim() || 'main', enabled: true})});
      await renderSchedules();
    } catch (error) { say(error.message, true); create.disabled = false; }
  };
  return form;
}

async function renderSettings() {
  const branches = await api(`/projects/${project.id}/branches`);
  const status = h('p', {class: 'small', role: 'status'});
  const say = (words, error = false) => { status.textContent = words; status.classList.toggle('error', error); };
  const admin = ADMINS.includes(project.role), writer = WRITERS.includes(project.role);
  const reload = async () => { project = await api(`/projects/${project.id}`); renderProjectBar(); await renderSettings(); };

  const toggle = async (env, on) => {
    if (!on && !confirm(`Disable dbt in ${LABELS[env.environment]}? Its runs and schedules stop and the team's service account for it is revoked. Tables stay.`)) return;
    try {
      await api(`/teams/${project.team}/environments/${env.environment}`, {method: on ? 'POST' : 'DELETE'});
      await reload();
    } catch (error) { say(error.message, true); }
  };
  const environments = h('section', {class: 'card'},
    h('h2', {text: 'Environments'}),
    h('p', {class: 'small', text: 'dbt runs in an environment once a team administrator enables it there. It then writes to the team\'s databases of that environment as the team\'s service account.'}),
    h('table', {}, h('thead', {}, h('tr', {}, ...['Environment', 'State', 'Databases', 'Enabled by', ''].map(t => h('th', {text: t})))),
      h('tbody', {}, ...project.environments.map(env => h('tr', {},
        h('td', {text: LABELS[env.environment]}),
        h('td', {}, h('span', {class: `chip ${env.enabled ? 'success' : ''}`, text: env.enabled ? 'enabled' : 'off'})),
        h('td', {text: (env.databases || []).join(', ') || '—'}), h('td', {text: env.enabledBy || '—'}),
        h('td', {class: 'nowrap'}, admin ? h('button', {type: 'button', class: env.enabled ? 'danger' : 'quiet',
          onclick: () => toggle(env, !env.enabled), text: env.enabled ? 'Disable' : 'Enable'}) : null))))));

  const remove = async branch => {
    if (!confirm(`Delete the branch ${branch.name}? Its commits that are not on main are lost.`)) return;
    try {
      await api(`/projects/${project.id}/branches/${encodeURIComponent(branch.name)}`, {method: 'DELETE'});
      await renderSettings();
    } catch (error) { say(error.message, true); }
  };
  const branchTable = h('section', {class: 'card'},
    h('h2', {text: 'Branches'}),
    h('p', {class: 'small', text: 'main is protected: it changes only through a merge by a team administrator. Delete branches that are merged or no longer needed.'}),
    h('table', {}, h('thead', {}, h('tr', {}, ...['Branch', 'Last commit', 'When', ''].map(t => h('th', {text: t})))),
      h('tbody', {}, ...branches.map(branch => h('tr', {},
        h('td', {text: branch.name}), h('td', {text: `${branch.subject} · ${branch.revision.slice(0, 7)}`}),
        h('td', {class: 'nowrap', text: when(branch.committedAt)}),
        h('td', {class: 'nowrap'}, h('div', {class: 'tabs'},
          h('button', {type: 'button', class: 'quiet', onclick: () => go({view: 'pipeline', environment: 'development', ref: branch.name}), text: 'Pipeline'}),
          h('button', {type: 'button', class: 'quiet', onclick: () => go({view: 'files', ref: branch.name, file: null}), text: 'Files'}),
          writer && branch.name !== 'main' ? h('button', {type: 'button', class: 'danger', onclick: () => remove(branch), text: 'Delete'}) : null)))))));
  fill($('#settings-view'), environments, branchTable, status);
}
