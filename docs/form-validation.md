# Form validation rules

These rules apply to the forms in both portals. The browser and the API enforce the same rules. API errors name the field and its rule, without echoing the submitted value.

| Field | Rule |
| --- | --- |
| Team, database, share and platform usernames | 3–48 characters: lowercase letters, digits, `-` or `_`, starting with a letter |
| Description (team, database, share) | Optional, at most 280 characters |
| Share recipient | Optional free text, at most 120 characters |
| First and last name | Required, at most 100 characters, not only whitespace; surrounding spaces are trimmed |
| New account email | Required, at most 254 characters, domain with a dot |
| Existing Keycloak username | Required, at most 254 characters; linked or disabled accounts can't be selected |
| Team memberships | 1–100 teams, no duplicates, one role per selected team |
| Database team and environment | Required; environment is Development, Acceptance or Production |
| Share objects | 1–50 objects, including at least one table |
| Share expiry | Optional; today or later, and ends at the close of that UTC day |

Browser checks: `npm run test:forms-ui`. API checks: `test/test_form_validation.py`.
