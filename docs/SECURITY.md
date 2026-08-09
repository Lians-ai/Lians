# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.5.x   | Yes |
| < 0.5   | No |

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via **GitHub Security Advisories**:
1. Go to https://github.com/Lians-ai/Lians/security/advisories
2. Click "Report a vulnerability"
3. Fill in the details

Alternatively email **security@lians.ai** with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix

We will acknowledge receipt within **48 hours** and provide a remediation timeline within **7 days**.

## Scope

In scope:
- SQL injection or data isolation bypass in the API layer
- Authentication/authorisation bypass (OAuth bearer tokens, `X-API-Key`, `X-Admin-Secret`)
- Information barrier (RLS) bypass allowing cross-namespace data access
- Cryptographic weaknesses in the AES-256-GCM per-subject encryption
- Audit chain (SHA-256 hash chain) tampering or bypass
- Remote code execution via any input path

Out of scope:
- Issues in dependencies that are already publicly disclosed upstream
- Theoretical attacks with no practical exploit path
- Denial-of-service via resource exhaustion without auth bypass

## Security model

Lians enforces isolation at multiple layers:

- **OAuth resource-server authentication** — the public MCP endpoint validates JWT signature, issuer, audience, expiry, and per-tool scopes on every request
- **API key authentication** — REST data-plane routes require `X-API-Key`; keys are stored as SHA-256 hashes, never plaintext
- **Admin secret** — a separate credential (`X-Admin-Secret`) gates all `/v1/admin/*` routes; it is not derivable from agent keys
- **PostgreSQL RLS** — information barriers are enforced at the database layer with `FORCE ROW LEVEL SECURITY`, not the application layer
- **AES-256-GCM encryption** — each subject's memories are encrypted under a unique data-encryption key (DEK); destroying the DEK makes the data cryptographically unrecoverable (GDPR crypto-shred)
- **Append-only audit chain** — every write is recorded in a SHA-256 hash chain; audit rows are never updated or deleted

## Disclosure policy

We follow coordinated disclosure. Once a fix is ready we will:
1. Release a patched version
2. Publish a GitHub Security Advisory
3. Credit the reporter (unless they prefer anonymity)
