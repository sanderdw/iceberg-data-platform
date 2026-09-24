/* Getting started: the same team data through the portal, your own tools or an AI agent. */

/* Snippets for tools on the user's computer; the Catalog's connect panel uses them too. */
function connectSnippets(db) {
  return [
    ['Python · PyIceberg and DuckDB', `from iceberg_connect import catalog, duckdb_connection\n\nlake = catalog("${db}")\nprint(lake.list_namespaces())\n\ncon = duckdb_connection("${db}")\ncon.sql("SHOW ALL TABLES").show()`,
      'Keep iceberg_connect.py next to your script. The environment needs httpx, pyiceberg[pyarrow] and duckdb.'],
    ['DuckDB CLI', `uv run iceberg_connect.py shell ${db}`,
      'Opens the DuckDB CLI with the database attached as lakehouse. The token lasts an hour; run the command again to renew it.'],
    ['DBeaver', `uv run iceberg_connect.py duckdb ${db}`,
      'In a DuckDB connection, add each printed line under Connection settings › Initialization › Bootstrap queries. After an hour, run it again and replace the CREATE SECRET line.'],
  ];
}

/* One way to register an MCP server per coding agent; the Keycloak client accepts any loopback callback. */
function mcpRecipes(name, url) {
  return [
    ['CODEX · ~/.codex/config.toml', `[mcp_servers.${name}]\nurl = "${url}"\nscopes = ["openid", "profile", "offline_access"]\n\n[mcp_servers.${name}.oauth]\nclient_id = "iceberg-mcp"`],
    ['COPILOT · .vscode/mcp.json', JSON.stringify({ servers: { [name]: { type: 'http', url, oauth: { clientId: 'iceberg-mcp' } } } }, null, 2)],
    ['CLAUDE CODE', `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 ${name} ${url}`],
  ];
}

function guideText(...parts) {
  // Strings are text; one-element arrays are inline code.
  const p = element('p');
  p.append(...parts.map(part => Array.isArray(part) ? element('code', part[0]) : part));
  return p;
}

function guideCode(label, code) {
  const block = element('div', undefined, 'guide-code'), bar = element('div', undefined, 'guide-code-bar');
  bar.append(element('span', label), copyButton('Copy', code, `${label} snippet copied.`));
  block.append(bar, element('pre', code, 'view-sql'));
  return block;
}

function guideTrack(id, number, label, title, text, steps) {
  const track = element('section', undefined, 'guide-track'); track.id = `guide-${id}`;
  track.append(element('span', `${number} / ${label}`, 'eyebrow'), element('h3', title), guideText(...text));
  steps.forEach(([heading, body, ...extra], i) => {
    const step = element('div', undefined, 'guide-step'), content = element('div');
    content.append(element('h3', heading), guideText(...body), ...extra);
    step.append(element('span', `0${i + 1}`), content); track.append(step);
  });
  return track;
}

/* Install commands per operating system; the helper needs uv, the shell command the DuckDB CLI. */
const installs = [
  ['macOS', 'BASH', 'brew install uv duckdb'],
  ['Linux', 'BASH', 'curl -LsSf https://astral.sh/uv/install.sh | sh\ncurl https://install.duckdb.org | sh'],
  ['Windows', 'POWERSHELL', 'winget install --id=astral-sh.uv -e\nwinget install DuckDB.cli'],
];

let guideOs = null; // The chosen tab survives the guide's periodic re-render.

function guideInstall() {
  const host = element('div'), tabs = element('div', undefined, 'catalog-tabs guide-os');
  tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Operating system');
  const platform = (navigator.userAgentData?.platform || navigator.userAgent).toLowerCase();
  const detected = platform.includes('win') ? 2 : platform.includes('mac') ? 0 : platform.includes('linux') ? 1 : 0;
  const buttons = installs.map(([name, shell, code], i) => {
    const button = element('button', name, 'quiet'); button.type = 'button'; button.setAttribute('role', 'tab');
    button.addEventListener('click', () => {
      guideOs = i;
      buttons.forEach(b => b.setAttribute('aria-selected', String(b === button)));
      host.lastChild.replaceWith(guideCode(shell, code));
    });
    return button;
  });
  tabs.append(...buttons); host.append(tabs, element('span'));
  buttons[guideOs ?? detected].click();
  return host;
}

/* The helper uses the catalog address set by the administrator. A localhost address only works on the platform's own machine. */
function catalogReachable() {
  const local = host => ['localhost', '127.0.0.1', '[::1]'].includes(host);
  try { return !local(new URL(state.catalogUri).hostname) || local(location.hostname); } catch { return true; }
}

function guideLink(label, page) {
  const button = element('button', label, 'quiet guide-start'); button.type = 'button';
  button.addEventListener('click', () => showPage(page));
  return button;
}

function renderGuide() {
  const db = state.databases.find(d => !d.shared) || state.databases[0];
  const manages = ['admin', 'bucket-admin'].includes(state.activeRole);
  const keycloak = [['Enable Keycloak sign-in', ['Available once this workspace signs in through Keycloak. Your own tools and AI agents use your Keycloak account.']]];

  const portal = [
    ['Pick your team and environment', ['Active team and Environment at the top decide which databases you see. Your role in the team decides what you can change.']],
    ['Browse the catalog', ['Open a database to see its namespaces, tables and views: schema, snapshots and a preview of up to 100 rows.'], guideLink('Open the catalog', 'catalog')],
    ['Open a notebook', ['Start marimo on a database with your own permissions. The example notebooks 01–07 show PyIceberg and DuckDB. Files are shared by team and environment.'], guideLink('Go to notebooks', 'notebooks')],
    ['Share data', ['Team administrators share tables and views with another team or an external party. Data shared with your team appears in your catalog, read-only.'], guideLink('View data shares', 'shares')],
  ];

  const download = element('a', 'Download iceberg_connect.py', 'button quiet guide-start'); download.href = '/iceberg_connect.py'; download.download = 'iceberg_connect.py';
  const attach = `uv run iceberg_connect.py shell ${db?.id || '<database>'}`;
  const unreachable = element('p', 'Your own tools cannot reach this platform from other computers yet: its catalog address is only valid on the machine that runs it. Use the portal or an AI agent, or ask your administrator to share the platform.', 'guide-warning');
  const computer = loginUrl ? [
    ['Install uv and DuckDB', ['uv runs the helper; the DuckDB CLI is where you query. Open a new terminal after installing.'],
      ...(catalogReachable() ? [] : [unreachable]), guideInstall()],
    ['Download the helper', ['One Python file for ', ['uv'], '. The portal fills in where to sign in and which catalog to use. It holds no secret.'], download],
    ['Sign in once', ['Approve the code in your browser. The sign-in stays in ', ['~/.config/iceberg-platform'], ' for 30 days of inactivity. ', ['uv run iceberg_connect.py logout'], ' revokes it.'],
      guideCode('BASH', 'uv run iceberg_connect.py login')],
    ['Open DuckDB', db
      ? [`Commands for ${db.name}. `, 'Catalog › Connect from your computer has them for every database. DuckDB attaches read-only; add ', ['--write'], ' to write.']
      : ['Replace ', ['<database>'], ' with a catalog name from Catalog › Connection information. DuckDB attaches read-only; add ', ['--write'], ' to write.'],
      guideCode('DUCKDB · READ', attach), guideCode('DUCKDB · WRITE', `${attach} --write`),
      element('p', 'Attaches the database as lakehouse, so refer to tables as lakehouse.<namespace>.<table>. The token lasts an hour; run the command again to renew it. Your team role decides what you can write, and shared databases stay read-only.', 'hint')],
  ] : keycloak;

  const prompts = [
    `Which databases can I use in ${envNames[state.activeEnvironment].toLowerCase()}, and what is in them?`,
    db ? `Describe the tables in ${db.name} and preview five rows of the largest one.` : 'Describe the tables in one of my databases and preview five rows.',
    ...(manages ? ['Create a development database called campaigns for my team.'] : []),
  ];
  const list = element('ul', undefined, 'guide-prompts'); list.append(...prompts.map(prompt => element('li', prompt)));
  const agent = loginUrl ? [
    ['Register the server', ['Add this workspace to your coding agent. Other MCP clients need the URL ', [`${location.origin}/mcp`], ' and the client ID ', ['iceberg-mcp'], '; any local callback port works.'],
      ...mcpRecipes('iceberg-user', `${location.origin}/mcp`).map(([label, code]) => guideCode(label, code))],
    ['Sign in', ['The agent opens Keycloak in your browser on first use, or when you run ', ['codex mcp login iceberg-user'], '. Sign in with your own account. Claude Code listens on port 3010 unless your administrator changed the platform’s MCP callback port.']],
    ['Ask', [manages
      ? 'Describe what you want to know. The agent lists, describes and previews with your permissions, and as a team administrator it can also create, rename and delete databases.'
      : 'Describe what you want to know. The agent lists, describes and previews your data with your permissions.'], list],
  ] : keycloak;

  const ways = [
    ['portal', '01', 'PORTAL', 'Explore in the browser', 'Catalog, notebooks and data shares.'],
    ['computer', '02', 'YOUR COMPUTER', 'Use your own tools', 'The DuckDB CLI, as yourself.'],
    ['agent', '03', 'AGENT', 'Ask an AI agent', 'Explore through MCP with your permissions.'],
  ];
  const index = element('nav', undefined, 'guide-index'); index.setAttribute('aria-label', 'Ways to get started');
  index.append(...ways.map(([id, number, label, title, text]) => {
    const button = element('button'); button.type = 'button';
    button.append(element('span', `${number} / ${label}`), element('strong', title), element('small', text));
    button.addEventListener('click', () => $(`#guide-${id}`).scrollIntoView({behavior: 'smooth', block: 'start'}));
    return button;
  }));

  const docs = element('a', '/docs'); docs.href = '/docs'; docs.target = '_blank'; docs.rel = 'noopener';
  const note = guideText('Every route uses your own account. Your team role decides what you can read and write, and data shared with your team stays read-only. Prefer HTTP? The workspace API is documented at ', docs, '.');
  note.className = 'guide-note';
  const guide = element('section', undefined, 'guide');
  guide.append(element('span', 'GUIDE / THREE WAYS IN', 'eyebrow'), element('h2', 'Your team data. Work with it your way.'),
    element('p', 'Browse and analyze in this portal, connect the tools you already use, or let an AI agent do the legwork. Each route signs in as you.', 'guide-lead'), index,
    guideTrack('portal', '01', 'PORTAL', 'Explore in the browser.', ['The pages of this workspace, one step at a time.'], portal),
    guideTrack('computer', '02', 'YOUR COMPUTER', 'Connect your own tools as yourself.', ['The DuckDB CLI reads and writes Iceberg directly, with your account and your team role. No notebook and no shared secret.'], computer),
    guideTrack('agent', '03', 'AGENT', 'Ask an AI agent.', ['This workspace serves a Model Context Protocol endpoint at ', ['/mcp'], '. An agent such as Claude Code gets your catalog as tools and signs in as you.'], agent),
    note);
  $('#guide').replaceChildren(guide);
}
