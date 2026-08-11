"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  decisionReceipt,
  incident,
  type ImpactLabel,
  type IncidentEventId,
} from "./incidents/lending-8127";
import LookaheadExplorer from "./LookaheadExplorer";

type ImpactFilter = "All" | ImpactLabel;
type ReviewState = "needs-review" | "reviewed" | "closed";
type VerificationState = "verified" | "checking" | "failed" | "unavailable";

const controlStages = [
  {
    number: "01",
    name: "Record what mattered",
    description:
      "The Universal Recorder captures memory, prompts, tools, policy, identity, and review across native, OTLP, MCP, and A2A events.",
    proof: "UNIVERSAL RECORDER / OPEN INPUTS",
  },
  {
    number: "02",
    name: "Prove the boundary",
    description:
      "A Decision Receipt freezes the declared evidence boundary, completeness disclosure, acting identity, and integrity material.",
    proof: "DECISION RECEIPT / SHA-256 / ED25519",
  },
  {
    number: "03",
    name: "Govern the action",
    description:
      "The Authority Gate binds the real principal, scopes, information barrier, policy, and approval quorum before release.",
    proof: "AUTHORITY GATE / IDENTITY-BOUND",
  },
  {
    number: "04",
    name: "Learn from change",
    description:
      "Impact Intelligence finds exposed decisions, separates proof from reachability, assigns remediation, and attests closure.",
    proof: "IMPACT INTELLIGENCE / CLOSURE",
  },
] as const;

const answerabilityQuestions = [
  {
    question: "What did it know?",
    answer: "Point-in-time memory and provenance",
  },
  {
    question: "Why did it act?",
    answer: "A complete decision evidence graph",
  },
  {
    question: "Who authorized it?",
    answer: "Identity-bound policy and approval",
  },
  {
    question: "What changed next?",
    answer: "Blast-radius detection and remediation",
  },
] as const;

const impactFilters: ImpactFilter[] = [
  "All",
  "Direct reference",
  "Reachable",
  "Estimated",
];

function labelClass(label: ImpactLabel) {
  return label.toLowerCase().replace(" ", "-");
}

function bytesToHex(bytes: ArrayBuffer) {
  return Array.from(new Uint8Array(bytes))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function base64ToBytes(value: string) {
  return Uint8Array.from(window.atob(value), (character) =>
    character.charCodeAt(0),
  );
}

function sortJsonKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJsonKeys);
  if (value && typeof value === "object") {
    return Object.keys(value)
      .sort()
      .reduce<Record<string, unknown>>((sorted, key) => {
        sorted[key] = sortJsonKeys((value as Record<string, unknown>)[key]);
        return sorted;
      }, {});
  }
  return value;
}

function canonicalJson(value: unknown) {
  return JSON.stringify(sortJsonKeys(value));
}

function receiptProtectedPayload() {
  return Object.fromEntries(
    Object.entries(decisionReceipt).filter(([key]) => key !== "integrity"),
  );
}

export default function Home() {
  const [activeEvent, setActiveEvent] =
    useState<IncidentEventId>("decision");
  const [impactFilter, setImpactFilter] = useState<ImpactFilter>("All");
  const [reviewState, setReviewState] =
    useState<ReviewState>("needs-review");
  const [verification, setVerification] =
    useState<VerificationState>("verified");
  const [gateApprovals, setGateApprovals] = useState(0);
  const [actionReleased, setActionReleased] = useState(false);
  const [notice, setNotice] = useState("");

  const gateState = actionReleased
    ? "released"
    : gateApprovals >= 2
      ? "approved"
      : "held";

  const selectedEvent =
    incident.timeline.find((event) => event.id === activeEvent) ??
    incident.timeline[0];

  const visibleImpacts = useMemo(
    () =>
      impactFilter === "All"
        ? incident.impacts
        : incident.impacts.filter((item) => item.label === impactFilter),
    [impactFilter],
  );

  function notify(message: string) {
    setNotice(message);
    window.setTimeout(() => setNotice(""), 2800);
  }

  function downloadReceipt() {
    const blob = new Blob([JSON.stringify(decisionReceipt, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "lians-decision-receipt-v0.1-app-8127.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
    notify("Decision Receipt downloaded.");
  }

  async function verifyReceipt() {
    setVerification("checking");
    try {
      const payloadBytes = new TextEncoder().encode(
        canonicalJson(receiptProtectedPayload()),
      );
      const digest = await window.crypto.subtle.digest("SHA-256", payloadBytes);
      const hash = bytesToHex(digest);
      if (hash !== decisionReceipt.integrity.receipt_hash) {
        setVerification("failed");
        return;
      }

      const receiptSignature = decisionReceipt.integrity.signature;
      const publicKey = await window.crypto.subtle.importKey(
        "raw",
        base64ToBytes(receiptSignature.public_key),
        "Ed25519",
        false,
        ["verify"],
      );
      const signatureValid = await window.crypto.subtle.verify(
        "Ed25519",
        publicKey,
        base64ToBytes(receiptSignature.value),
        digest,
      );
      setVerification(signatureValid ? "verified" : "failed");
    } catch {
      setVerification("unavailable");
    }
  }

  function markReviewComplete() {
    setReviewState("reviewed");
    notify("Review marked complete in this demo session.");
  }

  function closeIncident() {
    if (reviewState !== "reviewed") return;
    setReviewState("closed");
    notify("Incident closed. The original receipt remains unchanged.");
  }

  function addSyntheticApproval() {
    if (gateApprovals >= 2 || actionReleased) return;
    const nextCount = gateApprovals + 1;
    setGateApprovals(nextCount);
    notify(
      nextCount === 2
        ? "Approval quorum satisfied. The action is eligible for release."
        : "First identity-bound approval added to the demo chain.",
    );
  }

  function releaseSyntheticAction() {
    if (gateApprovals < 2 || actionReleased) return;
    setActionReleased(true);
    notify("Gate released the synthetic action with its approval chain attached.");
  }

  function resetDemo() {
    setActiveEvent("decision");
    setImpactFilter("All");
    setReviewState("needs-review");
    setVerification("verified");
    setGateApprovals(0);
    setActionReleased(false);
    notify("Synthetic incident restored.");
  }

  return (
    <main id="top">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Lians home">
          <span className="brand-mark" aria-hidden="true">
            L
          </span>
          <span className="brand-name">Lians</span>
          <span className="brand-product">Decision System</span>
        </a>
        <nav className="topnav" aria-label="Page sections">
          <Link href="/studio">Memory Studio</Link>
          <a href="#control">Proof loop</a>
          <a href="#incident">Live case</a>
          <a href="#impact">Blast radius</a>
          <a href="#backtest">Lookahead test</a>
          <a href="#receipt">Verify</a>
        </nav>
        <div className="topbar-actions">
          <span className="demo-pill">
            <span aria-hidden="true" /> Synthetic demo
          </span>
          <button className="quiet-button" type="button" onClick={resetDemo}>
            Reset
          </button>
        </div>
      </header>

      <section className="hero">
        <div className="hero-copy">
          <p className="eyebrow">
            THE SYSTEM OF RECORD FOR AI DECISIONS
          </p>
          <h1>Make every AI decision answerable.</h1>
          <p className="hero-lede">
            Lians gives teams the memory, authority, evidence, and controls to
            understand what AI knew, why it acted, who authorized it, and what
            changed next&mdash;across every model, agent, and provider.
          </p>
          <div className="hero-actions">
            <a className="primary-button" href="#incident">
              See a decision investigated <span aria-hidden="true">↓</span>
            </a>
            <Link className="secondary-button" href="/studio">Open Memory Studio</Link>
            <button
              className="secondary-button"
              type="button"
              onClick={downloadReceipt}
            >
              Verify a Decision Receipt
            </button>
          </div>
          <div className="truth-note">
            <span className="truth-mark" aria-hidden="true">i</span>
            <p>
              <strong>This is a synthetic canonical incident.</strong> Lians
              reconstructs recorded context within a declared capture boundary;
              it does not claim access to hidden model cognition or universal
              deterministic replay.
            </p>
          </div>
        </div>

        <aside className="receipt-preview" aria-label="Decision Receipt v0.1 preview">
          <div className="receipt-preview-top">
            <span>DECISION RECEIPT · v0.1</span>
            <span className="verified-chip">
              <span aria-hidden="true">✓</span> Verified
            </span>
          </div>
          <div className="receipt-number">#8127</div>
          <div className="receipt-outcome">
            <span>OUTCOME</span>
            <strong>Declined</strong>
            <small>Reason code · DTI_HIGH</small>
          </div>
          <dl className="receipt-facts">
            <div>
              <dt>Decision time</dt>
              <dd>Jul 12 · 14:32 ET</dd>
            </div>
            <div>
              <dt>Evidence grade</dt>
              <dd>A · 12 / 12</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>LND-4.2</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>credit-risk-v3.2</dd>
            </div>
          </dl>
          <div className="receipt-integrity">
            <div>
              <span>RECEIPT HASH</span>
              <code>{decisionReceipt.integrity.receipt_hash.slice(0, 20)}…</code>
            </div>
            <span className="integrity-seal" aria-label="Hash and signature verified">
              A
            </span>
          </div>
          <p className="receipt-boundary">
            Canonical JSON · Ed25519 · synthetic demo key
          </p>
        </aside>
      </section>

      <section className="answerability-section" aria-label="The four questions every AI decision must answer">
        <div className="answerability-intro">
          <span>THE ANSWERABILITY STANDARD</span>
          <strong>The record AI cannot rewrite after the fact.</strong>
        </div>
        <div className="answerability-grid">
          {answerabilityQuestions.map((item) => (
            <article key={item.question}>
              <h2>{item.question}</h2>
              <p>{item.answer}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="control-section" id="control" aria-labelledby="control-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              <span aria-hidden="true">01</span> THE LIANS PROOF LOOP
            </p>
            <h2 id="control-title">One system closes the accountability loop.</h2>
            <p>
              Record the decision boundary before the outcome is known. Bind it
              to authority before action. Then find every exposed decision when
              memory, policy, permissions, or models change.
            </p>
          </div>
          <div className="control-standard" aria-label="Supported protocol surfaces">
            <span>OPEN PROTOCOL SURFACES</span>
            <strong>OTLP · MCP · A2A</strong>
            <small>plus the native Recorder envelope</small>
          </div>
        </div>

        <div className="control-stage-grid">
          {controlStages.map((stage) => (
            <article className="control-stage" key={stage.name}>
              <div className="control-stage-top">
                <span>{stage.number}</span>
                <i aria-hidden="true" />
              </div>
              <h3>{stage.name}</h3>
              <p>{stage.description}</p>
              <code>{stage.proof}</code>
            </article>
          ))}
        </div>

        <div className="gate-simulation">
          <div className="gate-context">
            <p className="panel-kicker">RUNTIME CONTROL · SYNTHETIC ACTION</p>
            <h3>Issue adverse-action notice for Application 8127</h3>
            <p>
              The receipt is valid, but this action crosses the consumer-lending
              barrier. Policy requires independent risk and compliance approvals
              bound to this exact action, receipt, principal, and evaluation.
            </p>
            <dl className="gate-facts">
              <div><dt>Acting principal</dt><dd>underwriting-agent-prod</dd></div>
              <div><dt>Barrier</dt><dd>consumer-lending</dd></div>
              <div><dt>Policy</dt><dd>lending.high-impact.v4</dd></div>
              <div><dt>Trusted receipt</dt><dd><span className="fact-ok">Verified</span></dd></div>
            </dl>
          </div>

          <div className={`gate-console ${gateState}`} aria-live="polite">
            <div className="gate-console-top">
              <span>GATE EVALUATION · GATE-EVAL-8127</span>
              <strong>
                {gateState === "held" && "HELD"}
                {gateState === "approved" && "QUORUM MET"}
                {gateState === "released" && "RELEASED"}
              </strong>
            </div>
            <div className="gate-disposition">
              <span>{gateApprovals} / 2</span>
              <div>
                <strong>
                  {gateState === "held" && "Approval required"}
                  {gateState === "approved" && "Eligible for release"}
                  {gateState === "released" && "Action authorized"}
                </strong>
                <p>Every approval appends a context-bound attestation; the original receipt is never rewritten.</p>
              </div>
            </div>
            <div className="approval-chain" aria-label="Approval chain">
              <div className={gateApprovals >= 1 ? "complete" : "pending"}>
                <span>01</span>
                <p><strong>Risk reviewer</strong>{gateApprovals >= 1 ? "risk-reviewer@demo.lians" : "Awaiting independent identity"}</p>
              </div>
              <div className={gateApprovals >= 2 ? "complete" : "pending"}>
                <span>02</span>
                <p><strong>Compliance reviewer</strong>{gateApprovals >= 2 ? "compliance-reviewer@demo.lians" : "Awaiting independent identity"}</p>
              </div>
            </div>
            <div className="gate-actions">
              <button
                className="outline-button"
                type="button"
                onClick={addSyntheticApproval}
                disabled={gateApprovals >= 2 || actionReleased}
              >
                {gateApprovals >= 2 ? "Quorum complete" : "Add synthetic approval"}
              </button>
              <button
                className="close-incident-button"
                type="button"
                onClick={releaseSyntheticAction}
                disabled={gateApprovals < 2 || actionReleased}
              >
                {actionReleased ? "Action released" : "Release authorized action"}
              </button>
            </div>
            <small>Demo controls update local session state only; no production action is taken.</small>
          </div>
        </div>
      </section>

      <section className="incident-shell" id="incident" aria-labelledby="incident-title">
        <div className="incident-heading">
          <div>
            <p className="eyebrow">
              <span aria-hidden="true">02</span> ANSWERABLE DECISION CASE
            </p>
            <h2 id="incident-title">A decision becomes a case, not a mystery.</h2>
            <p>
              Ask why Application 8127 was declined, inspect the immutable
              boundary, and move forward through later changes without rewriting
              what the system knew on July 12.
            </p>
          </div>
          <div className="incident-statuses" aria-label="Incident status">
            <span className={`status-pill ${reviewState === "closed" ? "closed" : "open"}`}>
              <span aria-hidden="true" />
              {reviewState === "closed" ? "Incident closed" : "Incident open"}
            </span>
            <span className="reference-id">{incident.id}</span>
          </div>
        </div>

        <div className="decision-strip" aria-label="Original decision summary">
          <div>
            <span>ORIGINAL OUTCOME</span>
            <strong>{incident.outcome}</strong>
          </div>
          <div>
            <span>RECORDED INCOME</span>
            <strong>{incident.originalIncome}</strong>
          </div>
          <div>
            <span>RECORDED DTI</span>
            <strong>{incident.originalDti}</strong>
          </div>
          <div>
            <span>POLICY AT TIME</span>
            <strong>Version 4.2</strong>
          </div>
          <div className="grade-cell">
            <span>COMPLETENESS</span>
            <strong>Grade {incident.completeness.grade}</strong>
          </div>
        </div>

        <div className="investigation-grid">
          <div className="timeline-panel">
            <div className="panel-label">
              <span>INCIDENT TIMELINE</span>
              <small>Choose a recorded event</small>
            </div>
            <div className="timeline" role="group" aria-label="Incident events">
              {incident.timeline.map((event) => (
                <button
                  className={`timeline-event ${event.tone} ${activeEvent === event.id ? "active" : ""}`}
                  type="button"
                  key={event.id}
                  aria-pressed={activeEvent === event.id}
                  onClick={() => setActiveEvent(event.id)}
                >
                  <span className="timeline-step">{event.step}</span>
                  <span className="timeline-date">
                    <strong>{event.date}</strong>
                    <small>{event.time}</small>
                  </span>
                  <span className="timeline-copy">
                    <small>{event.eyebrow}</small>
                    <strong>{event.title}</strong>
                    <span>{event.summary}</span>
                  </span>
                  <span className="timeline-arrow" aria-hidden="true">→</span>
                </button>
              ))}
            </div>
          </div>

          <div className="evidence-panel" aria-live="polite">
            <div className="evidence-panel-heading">
              <div>
                <p>{selectedEvent.eyebrow}</p>
                <h3>{selectedEvent.title}</h3>
              </div>
              <span className={`event-tone ${selectedEvent.tone}`}>{selectedEvent.step}</span>
            </div>

            {activeEvent === "decision" && (
              <>
                <div className="frozen-boundary">
                  <span className="lock-mark" aria-hidden="true">⌁</span>
                  <div>
                    <strong>Recorded boundary frozen at {incident.decidedAtLabel}</strong>
                    <p>Later corrections and policy changes are intentionally excluded from this view.</p>
                  </div>
                </div>
                <div className="boundary-grid">
                  {incident.boundary.map((item) => (
                    <article className="boundary-card" key={item.kind}>
                      <div className="boundary-card-top">
                        <span>{item.kind}</span>
                        <span className={`use-chip ${item.evidenceUse === "Direct reference" ? "direct-reference" : "recorded-context"}`}>
                          {item.evidenceUse}
                        </span>
                      </div>
                      <h4>{item.title}</h4>
                      <strong>{item.value}</strong>
                      <p>{item.detail}</p>
                      <code>{item.reference}</code>
                    </article>
                  ))}
                </div>
              </>
            )}

            {activeEvent === "income-correction" && (
              <div className="change-detail">
                <p className="change-lede">
                  The source provider issued a superseding record six days after
                  the decision. Lians preserves both versions and their valid-time
                  boundary.
                </p>
                <div className="comparison-card">
                  <div className="comparison-side before">
                    <span>AVAILABLE JUL 12</span>
                    <strong>{incident.originalIncome}</strong>
                    <p>DTI calculated as {incident.originalDti}</p>
                    <code>inc_8843 · v1</code>
                  </div>
                  <div className="comparison-arrow" aria-hidden="true">→</div>
                  <div className="comparison-side after">
                    <span>RECORDED JUL 18</span>
                    <strong>{incident.correctedIncome}</strong>
                    <p>Recomputed DTI would be {incident.correctedDti}</p>
                    <code>inc_8843 · v2</code>
                  </div>
                </div>
                <div className="honesty-callout">
                  <strong>What Lians can say</strong>
                  <p>
                    Application 8127 directly referenced v1. A sandboxed comparison
                    may estimate a different result with v2, but that estimate is
                    not proof that the original agent would have produced it.
                  </p>
                </div>
              </div>
            )}

            {activeEvent === "policy-retirement" && (
              <div className="change-detail">
                <p className="change-lede">
                  Policy 4.2 was retired after the source correction. Its historical
                  references remain verifiable; active workflows can be checked for
                  continued reachability.
                </p>
                <div className="policy-comparison">
                  <div>
                    <span>RETIRED VERSION</span>
                    <strong>LND-4.2</strong>
                    <p>Applied to the July 12 decision</p>
                  </div>
                  <span className="policy-state">RETIRED · JUL 20</span>
                  <div>
                    <span>CURRENT VERSION</span>
                    <strong>LND-4.3</strong>
                    <p>Required for new evaluations</p>
                  </div>
                </div>
                <ul className="change-checks">
                  <li><span aria-hidden="true">✓</span> Original policy hash remains attached to the receipt</li>
                  <li><span aria-hidden="true">✓</span> Two historical records directly reference version 4.2</li>
                  <li><span aria-hidden="true">!</span> Six pending applications are reachable by a workflow still configured for 4.2</li>
                </ul>
              </div>
            )}
          </div>
        </div>
      </section>

      <section className="impact-section" id="impact" aria-labelledby="impact-title">
        <div className="section-heading">
          <div>
            <p className="eyebrow">
              <span aria-hidden="true">03</span> IMPACT INTELLIGENCE
            </p>
            <h2 id="impact-title">When truth changes, find every decision at risk.</h2>
            <p>
              Lians separates recorded use from possible access and sandboxed
              estimates, turning change into a prioritized remediation queue
              without overstating causality.
            </p>
          </div>
          <div className="impact-summary" aria-label="Impact summary">
            <div><strong>2</strong><span>direct records</span></div>
            <div><strong>6</strong><span>reachable cases</span></div>
            <div><strong>1</strong><span>estimated scenario</span></div>
          </div>
        </div>

        <div className="impact-legend" aria-label="Impact label definitions">
          <div>
            <span className="legend-mark direct-reference" />
            <p><strong>Direct reference</strong> The receipt records actual use.</p>
          </div>
          <div>
            <span className="legend-mark reachable" />
            <p><strong>Reachable</strong> A workflow can retrieve it; use is not proven.</p>
          </div>
          <div>
            <span className="legend-mark estimated" />
            <p><strong>Estimated</strong> A sandbox predicts possible change.</p>
          </div>
        </div>

        <div className="queue-panel">
          <div className="queue-toolbar">
            <div>
              <h3>Affected-decision queue</h3>
              <span>{visibleImpacts.length} of {incident.impacts.length} entries shown</span>
            </div>
            <div className="filter-group" role="group" aria-label="Filter impact queue">
              {impactFilters.map((filter) => (
                <button
                  type="button"
                  key={filter}
                  className={impactFilter === filter ? "active" : ""}
                  aria-pressed={impactFilter === filter}
                  onClick={() => setImpactFilter(filter)}
                >
                  {filter}
                </button>
              ))}
            </div>
          </div>
          <div className="table-scroll">
            <table className="impact-table">
              <thead>
                <tr>
                  <th scope="col">Record</th>
                  <th scope="col">Outcome</th>
                  <th scope="col">Why it appears</th>
                  <th scope="col">Impact basis</th>
                  <th scope="col">Queue</th>
                  <th scope="col">Risk</th>
                </tr>
              </thead>
              <tbody>
                {visibleImpacts.map((item) => (
                  <tr key={item.id}>
                    <td><strong>{item.subject}</strong><code>{item.id}</code></td>
                    <td>{item.outcome}</td>
                    <td>{item.reason}</td>
                    <td><span className={`impact-label ${labelClass(item.label)}`}>{item.label}</span></td>
                    <td><span className="queue-status">{item.queueStatus}</span></td>
                    <td><span className={`risk-chip ${item.risk.toLowerCase()}`}>{item.risk}</span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <LookaheadExplorer />

      <section className="receipt-section" id="receipt" aria-labelledby="receipt-title">
        <div className="section-heading receipt-heading">
          <div>
            <p className="eyebrow">
              <span aria-hidden="true">05</span> PORTABLE PROOF
            </p>
            <h2 id="receipt-title">Proof that travels across teams, models, and regulators.</h2>
            <p>
              The downloadable JSON freezes the recorded boundary, completeness
              disclosure, later change links, and integrity material in a format
              another system can independently inspect.
            </p>
          </div>
          <button className="primary-button light" type="button" onClick={downloadReceipt}>
            Download Receipt v0.1 JSON
          </button>
        </div>

        <div className="assurance-grid">
          <article className="completeness-card">
            <div className="card-kicker"><span>CAPTURE COMPLETENESS</span><span className="grade-badge">A</span></div>
            <div className="score-line"><strong>{incident.completeness.score}</strong><span>required receipt fields recorded</span></div>
            <div className="coverage-bar" aria-label="12 of 12 completeness checks present"><span /></div>
            <h3>{incident.completeness.status}</h3>
            <p>{incident.completeness.exclusion}</p>
            <a href="#incident">Inspect recorded boundary ↑</a>
          </article>

          <article className="verification-card">
            <div className="card-kicker"><span>INTEGRITY VERIFICATION</span><span className={`verification-dot ${verification}`} /></div>
            <h3>
              {verification === "verified" && "Hash matched. Signature verified."}
              {verification === "checking" && "Checking receipt integrity…"}
              {verification === "failed" && "Receipt verification failed."}
              {verification === "unavailable" && "Verification unavailable in this browser."}
            </h3>
            <p>
              SHA-256 over sorted-key canonical JSON, then Ed25519 verification
              over the raw 32-byte digest using the embedded synthetic demo key.
            </p>
            <dl className="verification-facts">
              <div><dt>Receipt hash</dt><dd><code>{decisionReceipt.integrity.receipt_hash}</code></dd></div>
              <div><dt>Signing key</dt><dd>{decisionReceipt.integrity.signature.key_id}</dd></div>
              <div><dt>Canonical form</dt><dd>json-sort-keys-utf8-v1</dd></div>
            </dl>
            <button className="outline-button" type="button" onClick={verifyReceipt} disabled={verification === "checking"}>
              {verification === "checking" ? "Verifying…" : "Verify hash + signature"}
            </button>
            <small className="key-trust-note">
              Signature validity does not by itself establish external trust in
              the embedded demo key.
            </small>
          </article>

          <article className="review-card">
            <div className="card-kicker"><span>HUMAN REVIEW</span><span className={`review-chip ${reviewState}`}>{reviewState === "needs-review" ? "NEEDS REVIEW" : reviewState === "reviewed" ? "REVIEWED" : "CLOSED"}</span></div>
            <h3>{reviewState === "closed" ? "Incident review closed" : "Resolve the review queue"}</h3>
            <p>
              {reviewState === "needs-review" && "Confirm that direct references were reviewed and reachable workflows were routed to their owners."}
              {reviewState === "reviewed" && "Review is complete. You can close the incident without changing the original signed receipt."}
              {reviewState === "closed" && "Closure was recorded in this browser session. The original receipt remains immutable."}
            </p>
            <div className="review-steps">
              <div className="done"><span>1</span><p><strong>Reconstruct</strong>Original boundary inspected</p></div>
              <div className={reviewState !== "needs-review" ? "done" : ""}><span>2</span><p><strong>Review</strong>Impact queue acknowledged</p></div>
              <div className={reviewState === "closed" ? "done" : ""}><span>3</span><p><strong>Close</strong>Human-attested closure</p></div>
            </div>
            <div className="review-actions">
              <button className="outline-button" type="button" onClick={markReviewComplete} disabled={reviewState !== "needs-review"}>
                {reviewState === "needs-review" ? "Mark review complete" : "Review complete"}
              </button>
              <button className="close-incident-button" type="button" onClick={closeIncident} disabled={reviewState !== "reviewed"}>
                {reviewState === "closed" ? "Incident closed" : "Close incident"}
              </button>
            </div>
            <small>Demo controls update local session state only; no external action is taken.</small>
          </article>
        </div>
      </section>

      <footer>
        <a className="brand footer-brand" href="#top">
          <span className="brand-mark" aria-hidden="true">L</span>
          <span className="brand-name">Lians</span>
        </a>
        <p>Make every AI decision answerable.</p>
        <span>Memory · Authority · Evidence · Action</span>
      </footer>

      {notice && <div className="toast" role="status" aria-live="polite"><span aria-hidden="true">✓</span>{notice}</div>}
    </main>
  );
}
