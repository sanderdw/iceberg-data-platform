"""The agent under evaluation: Conversational BI's evaluation API (its contracts/conversationalbi-api/v1).

Every call carries this extension's service-account token and names a team and an environment.
Conversational BI answers only where that team enabled both extensions, and then reads what every
member of the team can read.
"""

import httpx

from .errors import EvalError


class AgentClient:
    def __init__(self, settings, bridge, http=None):
        self.settings = settings
        self.bridge = bridge
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30, read=300), trust_env=False)

    async def close(self):
        await self.http.aclose()

    async def post(self, path, body):
        """The answer, or Conversational BI's refusal ({error, code}) as data; transport errors raise."""
        token = await self.bridge.service_token()
        try:
            response = await self.http.post(self.settings.agent_url + "/api/v1/evaluation" + path, json=body,
                                             headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise EvalError(503, "Conversational BI is unavailable. Is it installed and running?",
                            "agent_unavailable") from exc
        try:
            found = response.json()
        except ValueError:
            found = {}
        if response.status_code == 404 and not found.get("code"):
            raise EvalError(503, "This Conversational BI has no evaluation API: upgrade it.", "agent_unsupported")
        if response.is_error:
            return {"error": found.get("error", "Conversational BI refused the request."),
                    "code": found.get("code") or "agent_error", "status": response.status_code}
        return found

    async def checked(self, path, body):
        found = await self.post(path, body)
        if "error" in found:
            status = found["status"] if found["status"] in (403, 409, 422) else 502
            raise EvalError(status, found["error"], found["code"])
        return found

    @staticmethod
    def current(found, key):
        """An answer without `key` comes from a Conversational BI of before the shared API conventions. It would
        ignore the LLM a run asks for, so it is refused instead of read."""
        if key not in found:
            raise EvalError(503, "This Conversational BI has an older evaluation API: upgrade it.",
                            "agent_unsupported")
        return found

    async def models(self, team, environment):
        """The semantic models the agent answers from for the team, the LLMs a run may choose, and whether the
        control arm is allowed: {semanticModels, llmModels, schemaOnly}."""
        return self.current(await self.checked("/models", {"team": team, "environment": environment}),
                            "semanticModels")

    async def ask(self, team, environment, question, llm_model=None):
        """The agent's answer: {answer, llmModel, toolCalls, queries, usage, durationMs, traceId}. `llm_model` is
        one of the LLMs `models` lists; without it the agent uses its default."""
        body = {"team": team, "environment": environment, "question": question,
                **({"llmModel": llm_model} if llm_model else {})}
        return self.current(await self.checked("/ask", body), "llmModel")

    async def ask_control(self, team, environment, model, question, llm_model=None):
        """The control arm: the same LLM with only the table schemas of the semantic model `model`."""
        body = {"team": team, "environment": environment, "model": model, "question": question,
                **({"llmModel": llm_model} if llm_model else {})}
        return self.current(await self.checked("/ask-schema-only", body), "llmModel")

    async def truth(self, team, environment, model, spec):
        """The governed query's result, or its refusal: an expected refusal is a valid ground truth too."""
        return await self.post("/query", {"team": team, "environment": environment, "model": model, "spec": spec})

    async def recording(self, team, environment, enabled):
        """Conversational BI keeps the team's chat runs there from now on, or stops and deletes them."""
        return await self.checked("/recording", {"team": team, "environment": environment, "enabled": enabled})

    async def conversations(self, team, environment, since, limit=200):
        """Chat runs recorded while the team's live evaluation was on, oldest first, after `since` (an RFC 3339
        time, as each conversation's `at`). A conversation is {id, at, team, environment, model, question, answer,
        toolCalls, queries, llmModel, inputTokens, outputTokens, traceId}: never the person who asked."""
        return await self.checked("/conversations", {"team": team, "environment": environment, "since": since,
                                                     "limit": limit})

