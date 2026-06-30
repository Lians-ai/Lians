# Lians Vertical Pitch Guide

Lians sells best when each institution sees its own risk model in the product.
The core engine stays the same; the adapter, demo, controls narrative, and
connectors change by vertical.

## Financial Institutions

**Buyer pain:** AI agents can ingest revised market data, MNPI, research notes,
ratings actions, or late filings and then use the wrong temporal version in a
trade, recommendation, model validation, or client interaction.

**Pitch:** Lians is the memory control plane that keeps AI agents point-in-time
correct and reviewable.

**Proof points:**

- Bitemporal memory: `event_time`, `ingestion_time`, `valid_from`, `valid_to`.
- Backtest contamination checks for lookahead bias.
- Desk-level information barriers enforced in Postgres.
- SEC/FINRA-style audit export and hash-chain verification.
- Related-party and beneficial-ownership graph paths.
- Finance adapter for ticker, ISIN, CUSIP, and metric normalization.

**Demo:** revise NVDA guidance, show stale facts excluded, reconstruct what the
agent knew before the revision, then run a backtest contamination report.

**Connector priorities:** Bloomberg, FactSet, Refinitiv/LSEG, Snowflake,
Databricks, S3 data lakes, OMS/EMS exports, trade surveillance tools.

## Healthcare Organizations

**Buyer pain:** AI agents may retain PHI, mix care-team context, cite outdated
medications or diagnoses, or fail to prove what patient information was available
at the time of a clinical or administrative decision.

**Pitch:** Lians gives healthcare agents patient-scoped, encrypted,
reconstructable memory with care-team barriers and audit-preserving erasure.

**Proof points:**

- Per-subject AES-256-GCM encryption keyed by patient or member identifier.
- Crypto-shred certificates for patient-level deletion workflows.
- Care-team or department barriers.
- Point-in-time recall for clinical timelines.
- HIPAA technical safeguard mapping.
- Healthcare adapter for patient, encounter, provider, condition, medication,
  ICD-10, NPI, CPT, and HCPCS-style metadata.

**Demo:** update a patient's medication dose across encounters, show only the
current dose in present recall, reconstruct the chart context at admission time,
then crypto-shred the patient subject.

**Connector priorities:** FHIR, HL7, Epic/Cerner export paths, claims systems,
provider directories, clinical document stores, identity and access gateways.

## Legal Institutions

**Buyer pain:** Legal AI agents must respect matter walls, privilege cutoffs,
client confidentiality, chain-of-custody requirements, and conflict-of-interest
rules while working across huge document collections.

**Pitch:** Lians gives legal agents matter-scoped memory with privilege-date
reconstruction, conflict graph queries, and custody-preserving erasure.

**Proof points:**

- Matter-level information barriers.
- `recall_at` for privilege cutoff and production-date reconstruction.
- Tamper-evident audit trail for chain of custody.
- Conflict-of-interest reachability through the relationship graph.
- Matter destruction through crypto-shred keyed by matter or client.
- Legal adapter for matter, jurisdiction, claim type, party, privilege date, and
  document type normalization.

**Demo:** ingest matter facts before and after a privilege cutoff, show
point-in-time reconstruction excludes later-produced documents, then run a graph
path from attorney to adverse party.

**Connector priorities:** iManage, NetDocuments, Relativity, Clio, SharePoint,
Box, Microsoft Purview, document management systems, eDiscovery exports.

## Cross-Vertical Positioning

Use this line consistently:

> Lians is not just memory for AI agents. It is the control plane for regulated
> agent memory: temporal correctness, auditability, erasure, and access barriers
> in one deployable system.

Avoid positioning Lians as only a better vector store. The stronger claim is that
vector recall, graph context, and personalization are not enough for regulated
deployment unless the memory layer can be audited, segregated, reconstructed, and
erased.
