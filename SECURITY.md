# Security policy

Lians is decision-evidence and AI control infrastructure. Security reports that
could affect tenant isolation, evidence integrity, identity, cryptographic key
handling, runtime policy enforcement, or recovery are treated as high priority.

This is the canonical repository security policy. The implementation and
operator assumptions are described in the [production threat
model](docs/threat-model.md).

## Supported versions

Lians does not currently publish a long-term-support matrix.

| Track | Security support |
|---|---|
| Latest published release | Receives security fixes and advisories |
| Current default branch | Accepts security reports; may contain unreleased changes and is not a production support promise |
| Older releases and unmaintained forks | Not guaranteed to receive fixes; upgrade to the latest patched release |

When an advisory affects more than one release, its notice will identify the
first fixed version and any required key rotation, migration, or deployment
action. A version remains unsafe until both the patched software and those
operator actions are complete.

## Report a vulnerability privately

Do **not** open a public issue, discussion, pull request, or support thread with
vulnerability details.

Use GitHub's private vulnerability-reporting flow:

<https://github.com/Lians-ai/Lians/security/advisories/new>

If GitHub does not show the private reporting form, do not publish exploit
details as a workaround. Use GitHub only to ask the repository maintainers to
open a private channel, without including sensitive technical information in
that public request. This repository does not publish a verified security email
address, so this policy intentionally does not invent one.

For an actively compromised deployment, first invoke that deployment's incident
response process, isolate affected credentials and egress, and preserve evidence.
Then submit the private project report. Repository maintainers cannot operate or
contain a self-hosted environment on the reporter's behalf.

## What to include

Provide enough information to reproduce and assess the issue without including
real customer data or live secrets:

- affected release, commit, component, endpoint, SDK, or deployment artifact;
- deployment assumptions, including database and authentication mode;
- prerequisite privileges and whether the issue crosses a namespace or
  information barrier;
- minimal reproduction steps or a proof of concept using synthetic data;
- observed and expected behavior;
- confidentiality, integrity, availability, and safety impact;
- relevant request IDs, hashes, logs, or screenshots after removing secrets and
  personal data;
- any known workarounds or suggested remediation; and
- whether the issue is already public or subject to a disclosure deadline.

Never send API keys, bearer tokens, `X-Admin-Secret`, receipt-signing private
keys, master-key material, database credentials, raw production evidence, or a
customer backup. If a secret is exposed while preparing a report, revoke it and
say that redacted evidence is available.

## Response and coordinated disclosure

The maintainers aim to:

1. acknowledge a complete private report within three business days;
2. reproduce it, identify affected supported versions, and assign severity;
3. agree on a communication cadence and disclosure window with the reporter;
4. prepare a reviewed fix, regression coverage, release notes, and operator
   actions where applicable;
5. publish a GitHub Security Advisory and request a CVE when appropriate; and
6. credit the reporter unless anonymity is requested.

These are response targets, not a warranty or contractual service level. Complex
multi-party issues can take longer. The project may ask for a short embargo while
a fix is distributed, but will not ask a reporter to conceal an unfixed issue
indefinitely. Please avoid public disclosure until a fix or mutually agreed date.

## In scope

Examples include:

- authentication or authorization bypass in API-key, OIDC, SCIM, workload
  credential, metrics, or break-glass administration paths;
- namespace, subject, or information-barrier isolation failures;
- SQL injection, SSRF, unsafe deserialization, request smuggling, path traversal,
  remote code execution, or secret disclosure;
- a practical bypass of Recorder redaction/capture policy, Gate policy,
  immutable approval/review/closure semantics, or receipt trust verification;
- forging, deleting, reordering, or silently corrupting Decision Receipts,
  evidence links, audit history, integration outbox evidence, or WORM handoff
  evidence without detection expected by the documented model;
- weaknesses in encryption, master-key rotation, crypto-shredding, signing-key
  handling, backup verification, or recovery isolation;
- supply-chain compromise of a published Lians artifact or its release workflow;
  and
- denial-of-service issues with a concrete, disproportionate amplification or
  an authentication, quota, or isolation bypass.

The API server, bundled SDKs, specifications, container images, release
workflows, operator scripts, and maintained deployment templates are in scope.
An issue in a third-party service is in scope when Lians uses it unsafely or the
issue breaks a Lians security guarantee.

## Usually out of scope

- reports that only repeat a dependency advisory without showing reachability or
  Lians-specific impact;
- missing best-practice headers with no meaningful exploit path;
- scanner output without a reproducible finding;
- social engineering, physical attacks, or attacks against other users;
- load testing, volumetric denial of service, or destructive testing without
  prior written authorization;
- attacks that require a malicious database superuser, cluster administrator,
  KMS administrator, or CI administrator when the report does not also bypass a
  documented control intended to withstand that actor; and
- claims that a hash chain, checksum, signature, readiness response, or
  `WORM_MODE` flag provides guarantees expressly excluded by the threat model.

Out-of-scope classification does not mean the underlying risk is unimportant.
Operator-boundary risks and hardening gaps are tracked in the threat model and
may still be accepted as private defense-in-depth reports.

## Researcher conduct and safe harbor

Use only accounts, tenants, namespaces, and data you own or are authorized to
test. Minimize access, stop after demonstrating impact, do not establish
persistence, do not pivot to third parties, and delete locally retained test
data when the report is resolved. Automated testing must remain low volume and
must not degrade the service.

When research follows this policy in good faith, the project will treat it as
authorized security research and will not intentionally pursue legal action for
the testing itself. This statement cannot bind third parties, employers,
customers, cloud providers, or law-enforcement authorities and is not legal
advice.
