"""Actionable form errors without echoing submitted values or unknown field names."""

LABELS = {
    "name": "Name", "username": "Username", "password": "Password", "secret": "Client secret",
    "first_name": "First name", "last_name": "Last name", "email": "Email",
    "description": "Description", "recipient": "Recipient", "team": "Team", "database": "Database",
    "environment": "Environment", "memberships": "Teams and access", "role": "Access role",
    "subject": "Keycloak account", "objects": "Tables and views", "expiresAt": "Expiry",
    "namespace": "Namespace", "table": "Table", "snapshot_id": "Snapshot", "limit": "Preview row limit",
}


def validation_message(errors, path):
    messages = []
    for error in errors:
        loc, kind, ctx = error["loc"], error["type"], error.get("ctx", {})
        field = next((part for part in reversed(loc) if part in LABELS), None)
        label = LABELS.get(field)
        message = "Check the input."
        if kind == "value_error" and "error" in ctx:
            # These messages come from our own model validators, never provider responses.
            message = str(ctx["error"])
        elif kind == "extra_forbidden":
            message = "The request contains an unsupported field. Refresh the page and try again."
        elif label:
            if loc == ("body", "name"):
                label = next((title for resource, title in (
                    ("/api/shares", "Share name"), ("/api/teams", "Team name"),
                    ("/api/databases", "Database name"), ("/api/users", "Username"),
                    ("/api/identity/", "Username"),
                ) if path.startswith(resource)), label)
            if kind == "missing":
                message = f"{label} is required."
            elif kind == "string_pattern_mismatch" and loc == ("body", "name"):
                message = (f"{label} must use 3–48 lowercase letters, digits, hyphens or underscores, "
                           "starting with a letter.")
            elif kind == "string_too_long":
                message = f"{label} must be at most {ctx['max_length']} characters."
            elif kind == "string_too_short":
                message = (f"{label} must be at least {ctx['min_length']} characters."
                           if ctx["min_length"] > 1 else f"{label} cannot be empty.")
            elif field == "email":
                message = "Enter an email address such as name@example.com."
            elif field == "memberships":
                message = "Select between 1 and 100 teams, without duplicates."
            elif field == "objects":
                message = "Select between 1 and 50 tables and views, including at least one table."
            elif field in ("team", "database", "subject"):
                message = f"Select an existing {label.lower()}."
            elif field == "environment":
                message = "Select Development, Acceptance or Production."
            elif field == "role":
                message = "Select an available access role for each team."
            elif field == "expiresAt":
                message = "Choose a valid future expiry with a time zone, or leave it empty."
            elif field == "limit":
                message = "Choose a preview row limit from 1 to 100."
            else:
                message = f"Check {label.lower()}."
        if message not in messages:
            messages.append(message)
    return " ".join(messages[:5]) or "Check the input."
