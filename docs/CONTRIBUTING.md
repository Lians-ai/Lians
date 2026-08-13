# Contributing to Lians

Thank you for your interest in contributing. Lians is the memory tool for any
AI agent. Contributions that simplify installation, improve recall quality,
make memory easier to control, or add a real integration are especially welcome.

New to the project? Start with the [community guide](community.md) and the
[public roadmap](../ROADMAP.md).

## Before you start

Open an issue before opening a pull request for anything beyond a small bug fix.
Search existing issues first so work does not split across duplicate changes.

## Repository layout

```text
agentmem/src/    Core engine and FastAPI service
agentmem/sdk/    Python, TypeScript, Go, Java, and C SDKs
packages/        Lightweight user-facing packages, including Lians Easy
integrations/    Agent, framework, and client integrations
plugins/         Installable AI-client plugins
docs/            Documentation
demo/            Reproducible demos and examples
```

## Development setup

### Server and full SDK (Python)

```bash
git clone https://github.com/Lians-ai/Lians.git
cd Lians
python -m venv .venv
python -m pip install -e ".[dev]"
python scripts/test_all.py
```

### Lians Easy

```bash
python -m pip install pytest
PYTHONPATH=packages/lians-easy python -m pytest packages/lians-easy/tests -q
```

### TypeScript SDK

```bash
cd agentmem/sdk/typescript
npm install
npm test
```

## Adding an integration

1. Create `integrations/<framework>/python/` or a matching language directory.
2. Add a README, package metadata where needed, and tests.
3. Keep framework dependencies optional; do not add them to core.
4. Add the path to the root README or install guide.

## Commit style

Use clear conventional commit subjects:

```text
feat: add Pydantic AI integration
fix: exclude a superseded preference from recall
docs: add the Windows desktop setup
test: prove two MCP clients share one profile
```

## Pull request checklist

- [ ] Relevant tests pass locally.
- [ ] New behavior is covered by a test.
- [ ] User-facing changes update the documentation.
- [ ] No secrets, credentials, personal data, or real API keys are included.
- [ ] The pull request links a relevant issue when one exists.

## Reporting a security issue

Do not open a public issue for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## Code style

- Python: Ruff defaults with a 100-character line length.
- TypeScript: strict TypeScript; avoid `any` without justification.
- New core dependencies require discussion and a clear product need.
