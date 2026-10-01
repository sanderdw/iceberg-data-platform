"""A user-facing semantic-model error: a status and message like every portal error, plus a stable code."""

from server.models import ServiceError

CODES = {400: "invalid_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict",
         413: "too_large", 422: "invalid_request", 429: "busy", 502: "platform_error", 503: "unavailable",
         504: "timeout"}


class SemanticError(ServiceError):
    def __init__(self, status, message, code=None, detail=None):
        # Tools pass only the message on, so it names the valid choices (metrics, fields, datasets).
        choices = [f"{key}: {', '.join(map(str, value))}" for key, value in (detail or {}).items()
                   if isinstance(value, list) and value and key in {"metrics", "fields", "datasets", "via"}]
        super().__init__(status, message + (f" Available {'; '.join(choices)}." if choices else ""))
        self.code = code or CODES.get(status, "error")
        self.detail = detail
