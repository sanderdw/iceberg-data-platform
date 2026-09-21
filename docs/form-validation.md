# Portal form validation audit

The audit covers forms maintained in `public/` and `user_portal/public/`, including
the legacy sign-in forms. Keycloak, marimo, pgAdmin and RustFS maintain their own
forms and are outside this source audit. Browser checks use isolated API fixtures;
they do not modify a running installation.

| Fields and forms | Rules and findings |
| --- | --- |
| Team name (create/edit), database name, service username, new or linked platform username | 3–48 lowercase letters, digits, hyphens or underscores; first character must be a letter. The existing admin regex was valid. Help now states the first-character rule and is associated with the field; API errors explain the rule. |
| Share name | Same naming rules. Fixed the invalid browser regex and added visible help. The name stays disabled when editing an existing share. |
| Team/database/share description | Optional, up to 280 characters. Existing browser limits were correct; API errors now identify the limit. |
| Share recipient | Optional free text, up to 120 characters; it is not restricted to an email address. Existing browser limit was correct; API errors now identify the limit. |
| First and last name | Required, up to 100 characters. Whitespace alone is rejected before provisioning. Surrounding spaces are trimmed; case, accents and internal spaces are preserved. |
| New account email | Required, up to 254 characters. Added the domain-dot requirement already enforced by the API, along with an example in the validation hint. |
| Existing Keycloak username and account selector | Lookup trims surrounding spaces and requires a nonempty username, up to 254 characters. Selecting an account is required; changing the search clears the selection. Linked or disabled accounts cannot be selected. Existing controls were correct. |
| Team memberships and access roles (create/edit) | At least one team and at most 100; no duplicate teams. Unselected teams have disabled role controls. Added a browser check for the upper limit and ensured an empty team list produces the selection error. |
| Database team (create/move) and environment | Team is required; environment comes from Development, Acceptance or Production. Existing select controls were correct; invalid API selections now receive useful messages. |
| Share tables and views | 1–50 objects, including at least one table. Existing empty-selection and view-only checks were correct. Added the advertised object limit to the form and block oversized selections before submission. |
| Share expiry (create/edit) | Optional; expiry is at the end of the selected UTC day. The picker now allows today, matching the API, and blocks past dates. Malformed API dates receive an actionable error. |
| Legacy admin password | Required, maximum 1024 characters. Added the missing browser maximum and API minimum. Passwords are not trimmed. |
| Legacy user username/client secret | Username is 3–48 characters; secret is required and at most 1024 characters. Added the missing browser username minimum. Secrets are not trimmed. |
| Search/filter, workspace team/environment, notebook and preview-snapshot selectors | Free-text search filters lists; selectors use available values. These controls do not impose the resource-name rules. Existing catalog and navigation checks cover them. |
| Revoke/delete/reset confirmations | No editable data fields. Existing explicit confirmation behavior is retained. |

Both portals use one validation-error formatter. It names known fields and their
constraints without echoing submitted values, credentials or unknown field names.

Run the browser audits with `npm run test:forms-ui`, `npm run test:shares-ui`,
`npm run test:catalog` and `npm run test:navigation-ui`. API regressions are covered
by `test/test_form_validation.py` and the existing portal, identity and share tests.
