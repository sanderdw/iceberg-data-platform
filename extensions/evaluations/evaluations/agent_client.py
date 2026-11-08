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

    async def models(self, team, environment):
        return await self.checked("/models", {"team": team, "environment": environment})

    async def ask(self, team, environment, question, model=None):
        return await self.checked("/ask", {"team": team, "environment": environment, "question": question,
                                           **({"model": model} if model else {})})

    async def ask_control(self, team, environment, model, question, llm=None):
        """The control arm: the same LLM with only the table schemas of the semantic model."""
        return await self.checked("/ask-schema-only", {"team": team, "environment": environment, "model": model,
                                                       "question": question, **({"llm": llm} if llm else {})})

    async def truth(self, team, environment, model, spec):
        """The governed query's result, or its refusal: an expected refusal is a valid ground truth too."""
        return await self.post("/query", {"team": team, "environment": environment, "model": model, "spec": spec})

    async def conversations(self, team, environment, since, limit=200):
        """Recorded chat runs of a team that enabled Evaluations, oldest first, after `since`."""
        return await self.checked("/conversations", {"team": team, "environment": environment, "since": since,
                                                     "limit": limit})

