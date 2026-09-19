# Server-rendered interface

Jinja templates provide full pages and fragments for the same FastAPI application.

## Files and integration

- `base.html`: navigation, command palette, shared dialogs, assets and CSRF metadata.
- `deals.html`: pipeline/list, drag and drop, card actions and task hover details.
- `deal.html` and `deal_*.html`: cockpit, living summary, activity, materials, tasks and relationships.
- `form.html`, `inline.html`, `editor_shell.html`: full-page, inline and drawer editing.
- `tasks.html`, `directory.html`, `record.html`, `analytics.html`: other commercial views.
- `settings.html`, login/password/activation templates and `consent.html`: account and agent authorization flows.
- `material_preview.html`, `delete_material.html`, `transfer.html`, `error.html`: scoped supporting flows.

Routes and context come from `../app.py`; interactions live in `../static/app.js`, with custom styling in `../static/pitlane.css`. The design is called Pitlane, with a cockpit deal detail and the editable “Deal so far” summary. Keep compact cards, keyboard operation, contextual editing and clear action priorities.

## Editing constraints

- Keep all product copy in English. `../voice.py` rotates decorative copy; labels, errors and business facts stay stable.
- Preserve regular links/forms as well as HTMX/drawer behavior. Avoid putting authorization logic in templates.
- Mutating forms carry CSRF, record version and idempotency key where required by their routes. Refresh versions after successful edits.
- Use the existing Markdown filter and preview routes; do not render untrusted HTML or expose storage paths.
- Keep URLs relative to the public origin. Absolute backend HTTP URLs break HTTPS session cookies behind a proxy.
- UI tests are mapped in [tests guidance](../../tests/AGENTS.md); asset build commands are in [development](../../docs/development.md).
