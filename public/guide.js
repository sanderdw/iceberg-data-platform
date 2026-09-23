/* Getting started: the same workspace set up through the portal, an MCP agent or the API. */
function guideCode(label, text) {
  return `<div class="guide-code"><div class="guide-code-bar"><span>${label}</span><button class="text-button" type="button" data-copy>Copy</button></div><pre>${esc(text)}</pre></div>`;
}

// One way to register an MCP server per coding agent; the Keycloak client accepts any loopback callback.
function mcpRecipes(name, url) {
  return [
    ['CODEX · ~/.codex/config.toml', `[mcp_servers.${name}]\nurl = "${url}"\nscopes = ["openid", "profile", "offline_access"]\n\n[mcp_servers.${name}.oauth]\nclient_id = "iceberg-mcp"`],
    ['COPILOT · .vscode/mcp.json', JSON.stringify({ servers: { [name]: { type: 'http', url, oauth: { clientId: 'iceberg-mcp' } } } }, null, 2)],
    ['CLAUDE CODE', `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 ${name} ${url}`],
  ];
}

function guideSteps(steps) {
  return steps.map(([title, text, extra = ''], i) => `<div class="guide-step"><span>0${i + 1}</span><div><h3>${title}</h3><p>${text}</p>${extra}</div></div>`).join('');
}

function guideTrack(id, number, label, title, text, steps) {
  return `<div class="guide-track" id="guide-${id}"><span class="eyebrow">${number} / ${label}</span><h3>${title}</h3><p>${text}</p>${guideSteps(steps)}</div>`;
}

function renderGuide() {
  const origin = location.origin;
  const portal = keycloakUsers ? [
    ['Create a team', 'On Teams, choose Create team. Teams own databases and give their members access. You can rename a team later; its ID stays the same.'],
    ['Create a database', 'On Databases, pick a name, a team and an environment. Each database gets its own Iceberg catalog, bucket and permissions. Team administrators can also create databases in the user portal.'],
    ['Create or link a user', 'On Users, create a Keycloak account or link an existing one, then choose teams and a role per team. The temporary password is shown once. Share it through a secure channel.'],
    ['Sign in and open a notebook', 'Users sign in to the user portal with Keycloak, change their temporary password and open a notebook with their own permissions. Use Edit access, Revoke or Retry setup on Users to manage them later.'],
  ] : [
    ['Create a team', 'On Teams, choose Create team. You can edit its name and description later.'],
    ['Create a database', 'Choose a name, an existing team and an environment. Your database gets its own bucket and permissions.'],
    ['Create a user', 'Create a service account, select at least one team and choose a role per team. Each role applies to the databases of that team. Save the secret when it appears.'],
    ['Connect your application', 'Open the connection details for your database. Use the Iceberg REST configuration in Spark or PyIceberg, for example.'],
  ];
  portal[0][2] = '<button class="button guide-start" type="button" id="guide-teams">Start with a team</button>';

  const prompts = [
    'Give me an overview of the workspace: teams, their databases per environment and who has access.',
    keycloakUsers
      ? 'Create team marketing with a development and a production database called campaigns. Add Jane Doe (jane@example.com, username jane_doe) as writer.'
      : 'Create team marketing with a development and a production database called campaigns.',
    'Move the development database campaigns to team sales and tell me who gains and who loses access.',
    'Which data shares expire in the next 30 days, and who created them?',
  ];
  const agent = loginUrl ? [
    ['Register the server', 'Add the administration endpoint to your coding agent. Other MCP clients need the URL <code>' + esc(origin) + '/mcp</code> and the client ID <code>iceberg-mcp</code>; any local callback port works.',
      mcpRecipes('iceberg-admin', `${origin}/mcp`).map(([label, code]) => guideCode(label, code)).join('')],
    ['Sign in', 'The agent opens Keycloak in your browser on first use, or when you run <code>codex mcp login iceberg-admin</code>. Sign in with your platform administrator account. Claude Code listens on port 3010, which must match <code>MCP_CALLBACK_PORT</code> in <code>.env</code>.'],
    ['Ask', 'Describe the outcome. The agent reads the workspace first and then calls the same operations as this portal, with the same rules.',
      `<ul class="guide-prompts">${prompts.map(prompt => `<li>${esc(prompt)}</li>`).join('')}</ul>`],
  ] : [
    ['Enable Keycloak sign-in', 'The MCP endpoint authenticates every call with a Keycloak token. It is available once the portal signs in through Keycloak.'],
  ];

  const signIn = loginUrl
    ? `# Copy the portal_session cookie from your browser's developer tools.
portal.cookies.set('portal_session', '<cookie>')`
    : `portal.post(ORIGIN + '/api/session', json={'password': '<PORTAL_PASSWORD>'}).raise_for_status()`;
  const createUser = keycloakUsers
    ? `# The response contains the temporary password, once.
print(post('/api/identity/users', {
    'name': 'jane_doe', 'first_name': 'Jane', 'last_name': 'Doe', 'email': 'jane@example.com',
    'memberships': [{'team': team['id'], 'role': 'writer'}],
}))`
    : `# The response contains the secret, once.
print(post('/api/users', {'name': 'campaign_loader', 'memberships': [{'team': team['id'], 'role': 'writer'}]}))`;
  const script = `import requests

ORIGIN = '${origin}'
portal = requests.Session()
portal.headers['X-Portal-Request'] = '1'
${signIn}

def post(path, body):
    response = portal.post(ORIGIN + path, json=body)
    result = response.json()
    if not response.ok:
        raise RuntimeError(result['error'])
    return result

team = post('/api/teams', {'name': 'marketing', 'description': 'Campaign analytics'})
for environment in ('development', 'production'):
    post('/api/databases', {'name': 'campaigns', 'team': team['id'], 'environment': environment})
${createUser}`;
  const headers = `-H 'Content-Type: application/json' -H 'X-Portal-Request: 1'`;
  const curl = loginUrl
    ? `# Copy the portal_session cookie from your browser's developer tools.
curl -b 'portal_session=<cookie>' ${headers} \\
  -d '{"name": "marketing"}' ${origin}/api/teams`
    : `curl -c cookies.txt ${headers} \\
  -d '{"password": "<PORTAL_PASSWORD>"}' ${origin}/api/session
curl -b cookies.txt ${headers} \\
  -d '{"name": "marketing"}' ${origin}/api/teams`;
  const scripted = [
    ['Explore the endpoints', 'Every endpoint with its schema. Try it out uses your current session and adds the write headers.',
      '<a class="button guide-start" href="/docs" target="_blank" rel="noopener">Open API docs</a>'],
    ['Script it in Python', 'Run this with Python and <code>requests</code>. It signs in as you, then creates a team, a development and a production database, and a user.',
      guideCode('PYTHON', script)],
    ['Automate with curl', 'Writes need <code>Content-Type: application/json</code> and <code>X-Portal-Request: 1</code>. Sessions last up to eight hours. Errors return <code>{"error": ...}</code> with status 409 for conflicts and 422 for invalid input.',
      guideCode('BASH', curl)],
  ];

  const ways = [
    ['portal', '01', 'PORTAL', 'Point and click', 'First steps and one-off changes.'],
    ['agent', '02', 'AGENT', 'Ask an AI agent', 'Describe the result, let MCP do the work.'],
    ['api', '03', 'API', 'Script it', 'Repeatable setups and automation.'],
  ];
  $('#page-content').innerHTML = `<section class="panel guide"><span class="eyebrow">GUIDE / THREE WAYS IN</span><h2>Teams, databases and users. Set them up your way.</h2><p class="guide-lead">Every route uses the same operations and the same rules. Click through it once, let an agent do the legwork, or script it so the next setup takes seconds.</p><nav class="guide-index" aria-label="Ways to get started">${ways.map(([id, number, label, title, text]) => `<button type="button" data-track="${id}"><span>${number} / ${label}</span><strong>${title}</strong><small>${text}</small></button>`).join('')}</nav>`
    + guideTrack('portal', '01', 'PORTAL', 'Point and click.', 'The pages of this portal, one step at a time.', portal)
    + guideTrack('agent', '02', 'AGENT', 'Ask an AI agent.', 'This portal serves a Model Context Protocol endpoint at <code>/mcp</code>. An agent such as Claude Code gets the administration operations as tools and signs in as you.', agent)
    + guideTrack('api', '03', 'API', 'Script it.', 'The portal itself runs on a JSON API. Use it directly for setups you want to repeat.', scripted)
    + `<div class="panel-note">${keycloakUsers
      ? 'Keycloak manages sign-in. This portal manages teams and data permissions. Destructive agent tools are marked as such, and deleting a database requires its exact name. <code>create_user</code> puts the temporary password in the agent\'s transcript, so treat that transcript as confidential.'
      : 'This local portal uses one administrator login. Users are service accounts and must belong to at least one team. Their permissions follow their teams\' databases, including after a move.'} A team can only be deleted when it has no databases and no user would lose their last team.</div></section>`;
  for (const button of document.querySelectorAll('[data-track]')) button.onclick = () => $(`#guide-${button.dataset.track}`).scrollIntoView({ behavior: 'smooth', block: 'start' });
  for (const button of document.querySelectorAll('[data-copy]')) button.onclick = () => copy(button.closest('.guide-code').querySelector('pre').textContent);
  $('#guide-teams').onclick = () => { state.page = 'teams'; render(); };
}
