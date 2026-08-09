# Contributing to Lians

Thank you for your interest in contributing. Lians is an open-source decision evidence and reconstruction layer for production AI agents. Contributions that improve correctness, governed memory, evidence quality, safety boundaries, or integration breadth are especially welcome.

## Before you start

Use [GitHub Discussions](https://github.com/Lians-ai/Lians/discussions) for architecture questions, workflow design, and early proposals. Open an issue before a PR for anything beyond a small bug fix so we can align on scope before you invest time writing code.

Search existing issues and discussions first. Use the integration request for a framework, SDK, MCP, or deployment integration; the methodology correction form to challenge a benchmark or claim; and the feature request for a bounded product change.

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
npm ci
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
- [ ] Benchmark, compatibility, compliance, and performance claims link to dated evidence and state their limits
- [ ] Synthetic examples are clearly distinguished from customer deployments
- [ ] Capture gaps, schema compatibility, and migration impact are called out where relevant
- [ ] PR description links the relevant issue (`Closes #123`)

## Reporting a security issue

Do **not** open a public issue for vulnerabilities. See [SECURITY.md](SECURITY.md).

## Code style

- Python: standard `ruff` defaults, 100-character line length
- TypeScript: `tsc --strict`, no `any` without justification
- No new dependencies on core paths without discussion

## Evidence and claim discipline

Lians is used around consequential systems, so public claims should remain narrower than the evidence.

- Link benchmark and performance claims to a dated method, configuration, and result artifact.
- Distinguish a synthetic proof from a customer deployment.
- Do not claim compliance, certification, endorsement, universal performance, or deterministic replay without direct evidence.
- State capture gaps and failure modes explicitly.
- Remove secrets, personal data, protected health information, customer data, and confidential records from issues, tests, fixtures, and logs.
