"""One error type for REST, MCP and the agent: a status, a readable message and a stable machine code."""

CODES = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
         413: "too_large", 422: "invalid_request", 429: "busy", 502: "platform_error", 503: "unavailable",
         504: "timeout"}


class CbiError(Exception):
    def __init__(self, status, message, code=None, detail=None):
        super().__init__(message)
        self.status = status
        self.code = code or CODES.get(status, "error")
        self.detail = detail

    def body(self):
        return {"error": str(self), "code": self.code, **({"detail": self.detail} if self.detail else {})}
