---
name: agent-evaluations
description: Check how well the platform's AI agent (Conversational BI) answers a team's questions, with question sets of known answers in the Evaluations extension. Use when a user wants to test, evaluate or benchmark the agent or a semantic model, compare LLMs, find out why answers got worse, set up regression tests for a data product, or review real conversations that went wrong.
---

# Evaluations on the Iceberg Data Platform

The Evaluations extension's MCP server (`iceberg-evaluations`, at `http://localhost:3012/mcp`) keeps
question sets with known answers and scores Conversational BI's answers against them. Every tool
acts with the signed-in user's team role; runs ask the agent for the team. People follow the
scores at `http://localhost:3012`.

## Connect

```bash
claude mcp add --transport http --client-id ext-evaluations-mcp --callback-port 3010 iceberg-evaluations http://localhost:3012/mcp
```

On a Kubernetes installation the address is `https://eval.<domain>/mcp`; ask the user for the
domain. If the address does not answer, the extension is not installed: tell the user to run the
installer again with `ICEBERG_EXTENSIONS=conversationalbi,evaluations`, or on Kubernetes to register
it and install its chart (`kubernetes-install` skill).

## Before the first run

1. `whoami`: the user's teams, roles and where Evaluations is enabled.
2. A team administrator enables Evaluations (`enable_environment`) **and** Conversational BI in the
   same environment. Ask the user first: runs ask the agent for the whole team.
3. `list_agent_models` shows the semantic models the agent answers from, and the LLMs a run may use.

## Write a question set

A question set belongs to one team, environment and semantic model. Each case has a question and
exactly one expected outcome:

- `query`: the governed query that answers it, exactly as Conversational BI's `query` tool takes it
  (`metrics`, `dimensions` with `via` and `grain`, `filters`, `order_by`, `limit`). Use the model's
  real names from `describe_model` in Conversational BI or the user portal. Prefer this: the right
  answer is computed again at every run.
- `refusal`: an error the agent must run into and explain without figures, such as `fan_out`.
- `clarify: true`: a vague question the agent should ask back about (which airport: departure or arrival?).
- `decline: true`: a question the model can't answer (no such data, a forecast, a request to break rules).

Write questions the way the team asks them, including trends, comparisons, filters and two or three
adversarial ones. When the semantic-model skill made the model, `import_questions` takes its
`<name>.questions.sql` as a start. Write YAML in block style (`inputs:` on its own line with
`question:` under it): a `?` at the end of an inline `{...}` breaks YAML.

## Run and read

- `run_evaluation`, then poll `get_run` until `status` is `done`. Explain the failed cases with their
  `scores[...].reason`: a wrong row, a missing metric, an invented figure, a missed refusal.
- A run with `summary.reliable` false got no answer for more than 5% of its questions: it has no pass
  rate and says nothing about the agent. Say so, look at the case `error`s and run it again later.
- After a change (prompt, model, semantic model), run again and `compare_runs` with the previous
  run: report what was fixed and what broke.
- `compare_models` runs the set once per LLM; compare the runs' pass rates, tokens and seconds.
- `measure_semantic_model` shows what the semantic model adds: the same LLM once with it and once with only
  the table schemas. It costs two runs and needs `BI_EVAL_SCHEMA_ONLY=true` in Conversational BI. When both are
  done, `compare_runs(control, semantic)` has `uplift`. Tell the user the right answers are the model's own
  queries, so the number favours the model.
- `set_schedule` (team administrators) runs a set nightly; results also land in the target
  database as `evaluations.case_results`, with the semantic model `evaluation_results`.

## Live evaluation

`set_live` (team administrators) scores a sample of the team's real conversations. Tell the user that
Conversational BI then keeps the team's conversations for its retention period. `list_reviews` shows
the ones that failed a check; with the user, `review_conversation` dismisses one or
`promote_to_case` turns it into a regression case. Check the proposed expected query with the user
before promoting.
