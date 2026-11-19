#!/usr/bin/env bash
# Authenticates one MCP server from Claude Code's /mcp menu; the director opens the sign-in URL in tab "auth".
set -euo pipefail
cd "$(dirname "$0")/.."
name=$1
: > recordings/open-url.txt
python3 record/type.py --enter "/mcp"; sleep 2.5
for i in $(seq 1 12); do
  tmux -L demo capture-pane -p -t demo | grep -q "❯ . $name " && break
  python3 record/type.py --keys Down; sleep 0.25
done
python3 record/type.py --keys Enter; sleep 1.5
python3 record/type.py --keys Enter
for i in $(seq 1 20); do [ -s recordings/open-url.txt ] && break; sleep 0.5; done
record/step <<JS
const fs = await import('node:fs');
const url = fs.readFileSync('recordings/open-url.txt', 'utf8').trim().split('\n').pop();
const p = await ctx.page('auth', 'about:blank');
await ctx.rec.start('h3-auth-$name', 'auth'); ctx.mark('h3:auth-$name');
await ctx.sleep(600);
await p.goto(url);
await p.waitForLoadState('networkidle');
await ctx.sleep(2000);
await ctx.rec.stop('h3-auth-$name');
return await p.locator('body').innerText();
JS
sleep 2
tmux -L demo capture-pane -p -t demo | grep -E "Authentication|Connected" | tail -2
