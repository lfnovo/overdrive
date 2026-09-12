# Contributing to Overdrive

Thanks for helping make CRM work better for humans and agents.

For bugs, open an issue with the version, reproduction steps, expected behavior and actual result. Use fictional examples and remove tokens, activation links, contact details and other private data. Report vulnerabilities through the private process in [SECURITY.md](SECURITY.md).

For a substantial feature or architecture change, start with an issue describing the problem and intended behavior. Small fixes can go straight to a pull request.

Follow the [development guide](docs/development.md). Keep code, documentation and UI copy in English. Prefer focused changes that preserve the shared UI/MCP permissions model. Add meaningful tests for changed behavior, and update relevant documentation. Run `uv run ruff check overdrive tests scripts` and `uv run pytest -q` against a disposable SurrealDB 3 instance before submitting.

Describe the concrete problem, resulting behavior and validation in the pull request. Include screenshots for visible interface changes, using fictional data. Do not include `.env`, database dumps, uploads or private company records.

By contributing, you agree that your contribution is available under the project's [MIT license](LICENSE).
