"""One error type: a status, a readable message and a stable machine code. Never carries a token."""

CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found", 409: "conflict", 422: "invalid_request",
         502: "platform_error", 503: "unavailable"}


class SyncError(Exception):
    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status = status
        self.code = code or CODES.get(status, "error")
