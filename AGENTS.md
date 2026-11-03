# Working Guidelines

- Read the relevant code before making changes. Follow existing patterns.
- If requirements are unclear, state your assumptions or ask before editing.
- Make the smallest change that solves the request. Avoid unrelated refactoring,
  new dependencies, and speculative features.
- Preserve existing user changes.

# Project Conventions

- Keep HTTP handling in `routes/`, application logic in `services/`,
  page templates in `templates/`, and browser code in `static/`.
- Preserve authentication, CSRF protection, and user ownership checks.
- Keep credentials in environment variables or existing configuration.
  Never commit secrets or log them.

# Pi Integration

- Keep each user's conversations and workspace separated.
- Preserve Pi's native `session.jsonl` and the web transcript `messages.jsonl`.
- Preserve conversation locking, cancellation, and subprocess cleanup.
- Do not change the RPC event flow without checking the frontend consumer.

# Verification

- For bug fixes, add a focused regression test when practical.
- Run relevant tests after changes. Report what was checked and any limitations.
- Keep documentation updates limited to behavior or configuration you changed.

# New Page
- everytime the user wants to generate a new page, consider the mobile phone too

# Language Support

- The website supports English (`en`, the default) and Simplified Chinese
  (`zh-CN`). Every new or changed user-facing label must support both languages,
  including navigation, forms, placeholders, accessibility labels, validation,
  empty states, flash messages, and JavaScript status/error messages.
- In Jinja, use `_("English message", name=value)` for UI text. In browser code,
  use `window.AppI18n.t("English message", {name: value})`. Add the matching
  Chinese translation to `translations/zh-CN.json`; English is the fallback.
- Use named placeholders for dynamic values. Do not concatenate translated
  fragments when a complete message can be translated. Keep translation
  placeholder names identical in both languages.
- Preserve the language selector and browser preference cookie implemented in
  `utils/i18n.py`. Do not require a database migration for interface language.
- Do not translate user posts, filenames, saved conversations, agent/tool output,
  model identifiers, API field names, or saved system prompts. Translate UI labels
  describing those values. Keep API contracts stable; localize known API errors
  where they are displayed in the web UI.
- Verify English and Chinese rendering, dynamic Pi UI strings, persistence,
  escaped values, and mobile layout when changing the interface. Keep the shared
  catalog packaged in deployments and add coverage for new translation keys.

# Local development

If `AGENTS.local.md` exists, read it for private machine-specific instructions.
Activate your own virtual environment before Python commands: `source .venv/bin/activate`.
Keep `.env`, credentials, certificates, databases, logs, and personal notes out of Git.
Deployment destinations must come from environment variables, never committed server details.
