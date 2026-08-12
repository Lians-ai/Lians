# Contributing to Lians

Thank you for your interest in contributing. Lians is time-aware, verifiable
memory for AI agents — contributions that improve temporal correctness,
developer experience, reproducible evidence, or integration breadth are
especially welcome.

New to the project? Start with the [community guide](community.md), the
[public roadmap](../ROADMAP.md), or an issue labeled
[`good first issue`](https://github.com/Lians-ai/Lians/labels/good%20first%20issue).

## Before you start

Open an issue before opening a PR for anything beyond a small bug fix. This lets us align on approach before you invest time writing code.

Search existing issues first to avoid duplicates.

## Repository layout

```
server/          Core FastAPI server (agentmem/)
sdk/python/      Thin HTTP client (lians package on PyPI)
agentmem/sdk/    Full SDK — Python + TypeScript
integrations/    Per-framework integration packages
docs/            Documentation
benchmarks/      Reproducible benchmark suite
```

## Development setup

### Server & full SDK (Python)

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians/agentmem
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Run all tests (local embeddings, no API keys required)
pytest -v

# Run only fast unit tests
pytest -v -m "not pgvector"
```

### Thin client SDK (Python)

```bash
cd sdk/python
pip install -e ".[dev]"
pytest -v
```

### TypeScript SDK

```bash
cd agentmem/sdk/typescript
npm install
npm test
```

## Adding a framework integration

1. Create `integrations/<framework>/python/` (or `/typescript/`)
2. Add a `README.md`, `pyproject.toml`, and tests
3. Keep the integration dependency as an optional extra — never add it to core
4. Add a row to the framework table in the root `README.md`

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add Pydantic AI integration
fix: correct valid_to boundary in point-in-time recall
docs: add GDPR erasure example to README
test: cover conflict detection edge case
```

## Pull request checklist

- [ ] Tests pass locally (`pytest -v`)
- [ ] New behaviour is covered by a test
- [ ] Documentation updated for any user-facing change
- [ ] No secrets, credentials, or real API keys in the diff
- [ ] PR description links the relevant issue (`Closes #123`)

## Reporting a security issue

Do **not** open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md).

## Code style

- Python: standard `ruff` defaults, 100-character line length
- TypeScript: `tsc --strict`, no `any` without justification
- No new dependencies on core paths without discussion
