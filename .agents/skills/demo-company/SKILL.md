---
name: demo-company
description: Set up a fictional demo company for a class or training on an Iceberg Data Platform installation through the administration MCP server. It asks how many participants there are and creates an Energy, Webshop or Retail company with one account per participant, spread over teams with their own databases. It ends with a sign-in summary of every username and one-time password. Use when the user wants demo data, a training or workshop environment, a sample tenant, test users or a quick way to explore the platform.
---

# Demo company for Iceberg Data Platform

This skill fills an installation with a believable fictional company, sized for a class or training:
- one account per participant (1 to 48)
- up to 6 teams of about 4 people, each person in exactly one team
- 2 databases per team (one production, one development)
- in each team: one team admin, everyone else a writer (no read-only accounts, so every participant can create tables and run notebooks)

Everything goes through the administration portal's MCP server, the same API the portal uses, so the result behaves exactly like data an administrator entered by hand.

## Rules

- **One account per participant.** Create exactly as many demo accounts as the class has participants. The trainer uses the platform administrator account and does not count as a participant.
- **One team per person.** Every `create_user` call gets exactly one membership. Never add a demo user to a second team, and never call `update_user_access` to add teams.
- **Roles are `admin` or `writer` only.** Never `reader`, because trainees must be able to work hands-on. Never `bucket-admin`.
- Only create teams and databases from the chosen blueprint, and people from the people pool. Never rename, move or delete existing teams, databases or users unless the user explicitly asks for cleanup.
- These accounts are fictional demo accounts, and the user asked for their passwords. Collecting each `identity.temporaryPassword` into the summary below is the purpose of this skill. Show the passwords in that summary and in the credentials file; do not copy them anywhere else.

## 1. Check the MCP connection

You need the tools of the administration MCP server: `get_overview`, `create_team`, `create_database`, `create_user`, `find_accounts`, `list_users` and `retry_user_setup`. Your agent may show them with a prefix, usually the server name (`iceberg-admin`). Call `get_overview` once. It must succeed and report the provider online.

If the tools are missing, stop and tell the user how to connect. Pick the recipe for the agent you are running in, and fill in the administration address. `<URL>` is `PORTAL_ORIGIN` from `.env` plus `/mcp`, by default `http://localhost:3000/mcp`; after the lan-access skill, the HTTPS address.

1. Sign in once at the administration portal as the platform administrator from `.env` (`PLATFORM_ADMIN_USERNAME` / `PLATFORM_ADMIN_PASSWORD`) and set a new password.
2. Register the MCP server:

   | Agent | Register | Sign in |
   |---|---|---|
   | Codex (CLI and IDE extension) | In `~/.codex/config.toml`: `[mcp_servers.iceberg-admin]` with `url = "<URL>"` and `scopes = ["openid", "profile", "offline_access"]`, plus `[mcp_servers.iceberg-admin.oauth]` with `client_id = "iceberg-mcp"` | `codex mcp login iceberg-admin` |
   | GitHub Copilot in VS Code | In `.vscode/mcp.json`: `{"servers": {"iceberg-admin": {"type": "http", "url": "<URL>", "oauth": {"clientId": "iceberg-mcp"}}}}` | Start the server from `mcp.json`; VS Code opens the sign-in |
   | GitHub Copilot CLI | In `~/.copilot/mcp-config.json` under `mcpServers`: `"iceberg-admin": {"type": "http", "url": "<URL>", "tools": ["*"], "oauthClientId": "iceberg-mcp", "oauthPublicClient": true}` | On first use; if asked for a client ID, enter `iceberg-mcp` |
   | Claude Code | `claude mcp add --transport http --client-id iceberg-mcp --callback-port 3010 iceberg-admin <URL>` | On the first tool call |
   | Any other agent | A streamable HTTP MCP server at `<URL>` with OAuth: public client ID `iceberg-mcp`, no client secret, no dynamic registration | Callback on `localhost` or `127.0.0.1`, any port |

3. Start a new agent session so the tools load, then sign in as the platform administrator when the browser opens.

Codex needs the `scopes` line: without it, Codex asks Keycloak for scopes the client doesn't have and the sign-in fails with `invalid_scope`. If GitHub Copilot reports that the server is "blocked by policy", the user's organization has turned off third-party MCP servers for Copilot; a GitHub organization owner must allow them.

If a tool answers "Platform administrator access is required", the signed-in account lacks the `platform-admin` role. Sign in with the platform administrator instead.

## 2. Ask for the class size and the domain

Ask the user both questions together. If your agent has a tool for asking questions (for example `ask_user`, `vscode/askQuestions` or `AskUserQuestion`), use it. Otherwise ask in chat and stop until the user replies. Never guess the class size or the domain.

**How many participants are in the class?** A whole number from 1 to 48. For a larger group, suggest splitting it over several installations.

**Which company should you create?**
- **Energy**: Voltara Energy, a grid operator and energy supplier ([references/energy.md](references/energy.md))
- **Webshop**: Kiosko Online, an online home and lifestyle shop ([references/webshop.md](references/webshop.md))
- **Retail**: Linden Market, a chain of neighbourhood grocery stores ([references/retail.md](references/retail.md))

Read the chosen reference file and [references/people.md](references/people.md). The YAML blocks hold:
- the company's 6 teams in order of use
- the pool of 48 people

Each person's email is `<username>@<email_domain>` of the chosen company.

## 3. Plan the company

With `N` participants:
1. **Teams:** use the first `T = ceil(N / 4)` teams of the blueprint (at most 6). Each of these teams gets both of its databases. Unused teams are not created.
2. **Seats:** give each participant a seat in turn. The seat goes to the team with the fewest members, counting people already in it; on a tie, the earlier team in the blueprint wins. Team sizes then differ by at most one.
3. **Role:** a seat is `admin` when its team has no admin yet, so every team can manage its databases and data shares. All other seats are `writer`. There are no readers.
4. **People:** fill the seats in order with people from the pool, in list order. Skip anyone whose username already exists as a platform user or Keycloak account.

Examples:
- N = 1: one team with an admin.
- N = 10: 3 teams of 4, 3 and 3 people.
- N = 30: 6 teams of 5 people, each with an admin and 4 writers.

Show the plan in one short table (team, members, roles) and continue without waiting for confirmation.

### Running it again

Before planning, compare with `get_overview` (teams, databases, users). Running the skill again with a bigger class must only add what is missing:
- **Team** with a blueprint name exists: reuse its id.
- **Database** with the same name, team and environment exists: skip it.
- **Members:** platform users who already belong to one of the blueprint's teams count as participants and as members of that team. Only create `N` minus that number of new accounts. They are listed in the summary as "existing, password unchanged". If they already number `N` or more, create nothing, and say so.
- **Usernames:** a pool username that exists as a platform user outside these teams is skipped. Before each `create_user`, call `find_accounts(username)` and skip the person if an account with that name exists. Never link an existing account.

## 4. Create

Work team by team through the `T` planned teams, in this order:
1. `create_team(name, description)`. Keep the returned team id (`team-…`).
2. `create_database(name, team=<team id>, environment, description)` for each database of the team.
3. `create_user(name=<username>, memberships=[{"team": <team id>, "role": <role>}], email, first_name, last_name)` for each planned seat of the team. Keep `identity.username` and `identity.temporaryPassword` from the result.
   - If it fails because the username already exists, give the seat to the next unused person in the pool.

If `create_user` reports an incomplete setup:
1. Find the user with `list_users`.
2. Call `retry_user_setup(user=<portal-… id>)` once. It may return the one-time password.
3. If the password is still missing, list the user as "setup incomplete: finish with Retry setup in Users" and continue.

Report any other error and continue with the next object. Never retry a failed create in a loop. At the end, the number of demo accounts in the blueprint's teams must equal `N`. If it doesn't, say how many are missing and why.

## 5. Summary

Print the summary in your reply, then write the same content to `demo-company-<domain>-credentials.md` in the installation directory (the folder with `.env`). On macOS and Linux, run `chmod 600` on that file.

Use this format:

```markdown
# <Company> demo accounts: <N> participants

Administration portal: http://localhost:3000   (platform administrators only)
User portal:           http://localhost:3002   (everyone below)

Passwords are one-time: each person sets a new password at first sign-in.
This is the only copy; the platform does not store them.

## <team-name>: <team description>
Databases: <name> (production), <name> (development)

| # | Username | Name | Role | Temporary password |
|---|---|---|---|---|
| 1 | noor-bakker | Noor Bakker | admin | <password> |
| … | … | … | … | … |
```

- Use the actual portal addresses. If `PORTAL_ORIGIN` / `USER_ORIGIN` in `.env` are HTTPS LAN addresses, use those.
- Number the rows across all teams from 1 to N, so the trainer can hand out one line per participant.
- End with counts: participants, teams, databases and users created or reused, plus any problems.
- Tell the user the file holds live passwords and should be deleted once the accounts have been handed out.

## 6. Next steps to suggest

- Each participant signs in to the user portal and sees their own team's databases. Writers can create tables and run notebooks; the team admin can also create databases and data shares.
- The new databases are empty. Tables come from the example notebooks in the user portal: on Notebooks, open a database and pick an example in the notebook picker, or from your own tools via `iceberg_connect.py`. The admin MCP server cannot create tables.
- Data shares between teams are created in the user portal by a team admin.

## Cleanup (only when the user asks)

Deleting is irreversible. Confirm with the user first and list exactly what will go. Then work from the blueprint:
1. `delete_user` for each user whose only team is a blueprint team. Their Keycloak accounts stay and can be removed in the Keycloak console.
2. `delete_database(database, confirm_name=<name>)` for each blueprint database. This destroys all its tables and shares.
3. `delete_team` for each blueprint team.
