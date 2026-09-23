// Keycloak account management, enabled by the authenticated session metadata.
let keycloakUsers = false;

function identityLabel(user) {
  return ({linked: 'Keycloak account', unlinked: 'Not linked to Keycloak', pending: 'Setup incomplete',
    revoking: 'Revocation incomplete'})[user.identity?.status] || 'Service account';
}

function identityActions(user) {
  if (!keycloakUsers) return '';
  const id = esc(user.id), status = user.identity?.status;
  if (status === 'unlinked') return `<button class="text-button" data-link-identity="${id}">Link Keycloak</button>`;
  if (status === 'pending') return `<button class="text-button" data-retry-identity="${id}">Retry setup</button>`;
  if (status === 'linked' && user.identity.managed) return `<button class="text-button" data-reset-identity="${id}">Reset password</button>`;
  return '';
}

function bindIdentityActions() {
  for (const b of document.querySelectorAll('[data-link-identity]')) b.onclick = () => identityModal(b.dataset.linkIdentity);
  for (const b of document.querySelectorAll('[data-retry-identity]')) b.onclick = () => identityOperation(b.dataset.retryIdentity, 'retry');
  for (const b of document.querySelectorAll('[data-reset-identity]')) b.onclick = () => identityOperation(b.dataset.resetIdentity, 'password');
}

async function identityReady(result) {
  await load();
  const identity = result.identity, password = identity.temporaryPassword;
  openModal(password ? 'Keycloak account is ready' : 'Keycloak account linked',
    password ? 'Share this temporary password securely. The user must change it when signing in.' : 'The user can sign in with their existing Keycloak account.',
    `<div class="credential"><span>Username</span><code>${esc(identity.username || result.user.name)}</code></div>${password ? `<div class="credential"><span>Temporary password · shown once</span><code id="temporary-password">${esc(password)}</code></div>` : ''}<p class="subtle">Team and database permissions are managed here. Sign in through Keycloak in the user portal.</p><div class="modal-actions">${password ? '<button class="button" id="copy-identity">Copy sign-in details</button>' : ''}<button class="button primary" id="identity-done">Done</button></div>`);
  if ($('#copy-identity')) $('#copy-identity').onclick = () => copy(JSON.stringify({username: identity.username, temporaryPassword: password}, null, 2));
  $('#identity-done').onclick = closeModal;
}

function identityModal(existingId = null, database = null, mode = 'new') {
  if (existingId) mode = 'link';
  const user = state.data.users.find(u => u.id === existingId);
  const selected = state.data.databases.find(d => d.id === database)?.team;
  const chooser = existingId ? '' : `<div class="modal-actions identity-mode"><button class="button ${mode === 'new' ? 'primary' : ''}" id="identity-new">Create Keycloak account</button><button class="button ${mode === 'link' ? 'primary' : ''}" id="identity-link">Link existing account</button></div>`;
  const accountFields = mode === 'new'
    ? '<div class="form-row"><label>First name<input name="first_name" required maxlength="100" pattern=".*\\S.*" title="Enter a first name, not just spaces." autocomplete="off"></label><label>Last name<input name="last_name" required maxlength="100" pattern=".*\\S.*" title="Enter a last name, not just spaces." autocomplete="off"></label></div><label>Email<input name="email" type="email" required maxlength="254" pattern="[^\\s@]+@[^\\s@]+\\.[^\\s@]+" title="Enter an email address such as name@example.com." autocomplete="off"></label>'
    : '<label>Existing Keycloak username<input id="identity-search" maxlength="254" autocomplete="off"></label><button class="button" id="find-identity" type="button">Find account</button><label>Account to link<select name="subject" id="identity-account" required><option value="">Search and select an account</option></select></label><p class="subtle">Select the exact account. Its password and access to other applications stay unchanged.</p>';
  openModal(existingId ? `Link ${esc(user.name)} to Keycloak` : 'A new platform user',
    'Keycloak manages sign-in. This portal manages team and database access.',
    `${chooser}<form id="modal-form">${existingId ? '' : nameInput(mode === 'new' ? 'Username' : 'Platform username', 'e.g. analyst')}${accountFields}${existingId ? '' : membershipRows(selected ? { [selected]: 'reader' } : {})}<p class="form-error" role="alert"></p><div class="modal-actions"><button class="button" type="button" id="identity-refresh">Refresh Users</button><button class="button primary" type="submit">${mode === 'new' ? 'Create user' : 'Link account'}</button></div></form>`);
  if (!existingId) {
    $('#identity-new').onclick = () => identityModal(null, database, 'new');
    $('#identity-link').onclick = () => identityModal(null, database, 'link');
  }
  bindMembershipRows();
  $('#identity-refresh').onclick = async () => { closeModal(); await load(); };
  if (mode === 'link') {
    let searchVersion = 0;
    $('#identity-search').oninput = () => { searchVersion++; $('#identity-account').innerHTML = '<option value="">Search and select an account</option>'; };
    $('#find-identity').onclick = async () => {
      const version = ++searchVersion, button = $('#find-identity'), target = $('#identity-account');
      button.disabled = true;
      try {
        const username = $('#identity-search').value.trim();
        if (!username) throw new Error('Enter the Keycloak username.');
        const accounts = await api('/identity/accounts?' + new URLSearchParams({username}));
        if (version !== searchVersion || !target.isConnected) return;
        target.innerHTML = '<option value="">Select an account</option>' + accounts.map(a => `<option value="${esc(a.id)}" ${!a.enabled || a.linked ? 'disabled' : ''}>${esc(a.username)}${a.email ? ' · ' + esc(a.email) : ''}${a.linked ? ' · already linked' : !a.enabled ? ' · disabled' : ''}</option>`).join('');
        $('.form-error').textContent = accounts.length ? '' : 'No account found for that username.';
      } catch (error) { if (target.isConnected) $('.form-error').textContent = error.message; }
      finally { button.disabled = false; }
    };
  }
  formSubmit(async input => {
    if (mode === 'new') for (const field of ['first_name', 'last_name', 'email']) input[field] = input[field].trim();
    if (!existingId && !input.memberships.length) throw new Error('Select at least one team.');
    const path = existingId ? `/users/${encodeURIComponent(existingId)}/identity` : mode === 'new' ? '/identity/users' : '/identity/links';
    await identityReady(await api(path, 'POST', input));
  });
}

function identityOperation(id, operation) {
  const user = state.data.users.find(u => u.id === id);
  openModal(operation === 'retry' ? 'Retry account setup' : 'Reset Keycloak password',
    operation === 'retry' ? `Complete the existing setup for ${esc(user.name)}.` : `Issue a new temporary password for ${esc(user.name)}. They must change it at their next sign-in.`,
    '<form id="modal-form"><p class="form-error" role="alert"></p><div class="modal-actions"><button class="button primary" type="submit">Continue</button></div></form>');
  formSubmit(async () => identityReady(await api(`/users/${encodeURIComponent(id)}/identity/${operation}`, 'POST', {})));
}
