# Contributing to Lians

Thank you for helping make memory easier to add to any AI agent. Contributions
that simplify installation, improve recall quality, strengthen tests, or add a
real agent integration are especially welcome.

## Find a task

- Start with an open [`good first issue`](https://github.com/Lians-ai/Lians/issues?q=is%3Aissue%20state%3Aopen%20label%3A%22good%20first%20issue%22).
- Search existing issues before proposing new work.
- Comment on the issue before starting so another contributor does not duplicate
  the same work.
- Open an issue first for a new feature or any change that affects public
  behavior, storage, security, or compatibility.

## Set up the repository

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m venv .venv
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

The full [development guide](docs/CONTRIBUTING.md) explains the repository
layout, focused Python and TypeScript test commands, integration conventions,
and commit style.

## Submit a pull request

Keep each pull request focused on one problem. Before submitting it:

- run the most relevant tests locally;
- add or update tests when behavior changes;
- update user-facing documentation when setup or behavior changes;
- update `product-manifest.json` when capabilities, positioning, workflow, or
  availability changes; and
- confirm that no secrets, credentials, personal data, or real API keys are in
  the diff.

Describe what changed, why it matters to a user or developer, and the exact
validation you ran. Link the issue when one exists.

## Report a security issue

Do not open a public issue for a vulnerability. Follow the private reporting
instructions in the [security policy](docs/SECURITY.md).
