"use client";

import { useMemo, useState } from "react";
import Image from "next/image";
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
    name: "Connect your AI",
    description:
      "Add Lians to one workflow without replacing the model or tools you already use.",
  },
  {
    number: "02",
    name: "Let it work",
    description:
      "Lians quietly records the facts, rules, tools, and approvals behind each decision.",
  },
  {
    number: "03",
    name: "Open any decision",
    description:
      "See what the AI knew, why it acted, and who allowed it in one clear view.",
  },
  {
    number: "04",
    name: "Fix what changed",
    description:
      "When a fact or rule changes, find every affected decision and send it to the right person.",
  },
] as const;

const answerabilityQuestions = [
  {
    question: "What did it know?",
    answer: "The facts available at that exact moment.",
  },
  {
    question: "Why did it act?",
    answer: "The evidence and rules behind the result.",
  },
  {
    question: "Who approved it?",
    answer: "The people and policies that allowed it.",
  },
  {
    question: "What needs attention now?",
    answer: "Every affected decision and its owner.",
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
    <main id="top" className="lians-interactive-graphic">
      <header className="topbar">
        <a className="brand" href="#top" aria-label="Lians home">
          <Image className="brand-logo" src="/logo-blue.png" alt="Lians" width={88} height={32} priority />
          <span className="brand-product">Decision System</span>
        </a>
        <nav className="topnav" aria-label="Page sections">
          <a href="#control">How it works</a>
          <a href="#incident">See an example</a>
          <a href="#impact">Find changes</a>
          <a href="#receipt">Download proof</a>
          <Link href="/studio">Saved memory</Link>
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
          <h1>Make every AI decision answerable.</h1>
          <p className="hero-lede">
            Connect Lians to your AI. Open any decision to see what happened,
            why it happened, who approved it, and what needs attention now.
          </p>
          <div className="hero-actions">
            <a className="primary-button" href="#incident">
              Show me what happened <span aria-hidden="true">↓</span>
            </a>
            <Link className="secondary-button" href="/studio">See saved memory</Link>
          </div>
          <div className="truth-note">
            <span className="truth-mark" aria-hidden="true">i</span>
            <p>
              <strong>This example uses fictional lending data.</strong> It shows
              how Lians works without exposing real customer information.
            </p>
          </div>
        </div>

        <aside className="receipt-preview" aria-label="Decision Receipt v0.1 preview">
          <div className="receipt-preview-top">
            <span>AI decision</span>
            <span className="verified-chip">
              <span aria-hidden="true">✓</span> Verified
            </span>
          </div>
          <div className="receipt-number">#8127</div>
          <div className="receipt-outcome">
            <span>Result</span>
            <strong>Declined</strong>
            <small>Debt-to-income ratio was too high</small>
          </div>
          <dl className="receipt-facts">
            <div>
              <dt>Decision time</dt>
              <dd>Jul 12 · 14:32 ET</dd>
            </div>
            <div>
              <dt>Evidence grade</dt>
              <dd>12 of 12 facts</dd>
            </div>
            <div>
              <dt>Policy</dt>
              <dd>LND-4.2</dd>
            </div>
            <div>
              <dt>Model</dt>
              <dd>Credit review AI</dd>
            </div>
          </dl>
          <div className="receipt-integrity">
            <div>
              <span>Proof</span>
              <code>Decision record attached</code>
            </div>
            <span className="integrity-seal" aria-label="Hash and signature verified">
              A
            </span>
          </div>
          <p className="receipt-boundary">
            Every fact shown is from the moment the decision happened.
          </p>
        </aside>
      </section>

      <section className="answerability-section" aria-label="The four questions every AI decision must answer">
        <h2 className="answerability-heading">Open a decision. Get four clear answers.</h2>
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
            <h2 id="control-title">Connect it once. Understand every decision.</h2>
            <p>
              Lians sits beside the AI tools you already use. It records the
              decision, explains it clearly, and shows you what to do next.
            </p>
          </div>
          <div className="control-standard" aria-label="Simple integration promise">
            <strong>Keep your current AI.</strong>
            <small>Lians works beside your models, agents, and tools.</small>
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
            </article>
          ))}
        </div>

        <div className="gate-simulation">
          <div className="gate-context">
            <h3>Some actions need human approval.</h3>
            <p>
              This example cannot send a lending notice until both risk and
              compliance approve the exact action.
            </p>
            <dl className="gate-facts">
              <div><dt>AI system</dt><dd>Credit review AI</dd></div>
              <div><dt>Workflow</dt><dd>Consumer lending</dd></div>
              <div><dt>Rule</dt><dd>Two people must approve</dd></div>
              <div><dt>Decision proof</dt><dd><span className="fact-ok">Verified</span></dd></div>
            </dl>
          </div>

          <div className={`gate-console ${gateState}`} aria-live="polite">
            <div className="gate-console-top">
              <span>Approval status</span>
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
                <p>Every approval is recorded. The original decision stays unchanged.</p>
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
                {gateApprovals >= 2 ? "Both approvals added" : "Add review approval"}
              </button>
              <button
                className="close-incident-button"
                type="button"
                onClick={releaseSyntheticAction}
                disabled={gateApprovals < 2 || actionReleased}
              >
                {actionReleased ? "Action allowed" : "Allow action"}
              </button>
            </div>
            <small>Demo controls update local session state only; no production action is taken.</small>
          </div>
        </div>
      </section>

      <section className="incident-shell" id="incident" aria-labelledby="incident-title">
        <div className="incident-heading">
          <div>
            <h2 id="incident-title">See exactly what happened.</h2>
            <p>
              This application was declined. Choose any moment below to see
              the facts and rules available at that time.
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
            <span>Original result</span>
            <strong>{incident.outcome}</strong>
          </div>
          <div>
            <span>Income used</span>
            <strong>{incident.originalIncome}</strong>
          </div>
          <div>
            <span>Debt ratio used</span>
            <strong>{incident.originalDti}</strong>
          </div>
          <div>
            <span>Rule used</span>
            <strong>Version 4.2</strong>
          </div>
          <div className="grade-cell">
            <span>Proof quality</span>
            <strong>Grade {incident.completeness.grade}</strong>
          </div>
        </div>

        <div className="investigation-grid">
          <div className="timeline-panel">
            <div className="panel-label">
              <span>Timeline</span>
              <small>Choose a recorded event</small>
            </div>
            <div className="timeline" role="group" aria-label="Incident events">
              {incident.timeline.map((event) => (
                <button
                  className={`timeline-event ${event.tone} ${activeEvent === event.id ? "active" : ""}`}
                  type="button"
                  key={event.id}
                  data-graphic-item
                  aria-pressed={activeEvent === event.id}
                  onClick={() => setActiveEvent(event.id)}
                >
                  <span className="timeline-step">{event.step}</span>
                  <span className="timeline-date">
                    <strong>{event.date}</strong>
                    <small>{event.time}</small>
                  </span>
                  <span className="timeline-copy">
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
            <h2 id="impact-title">When one fact changes, find everything affected.</h2>
            <p>
              Lians shows which decisions definitely used the old fact, which
              ones may have seen it, and who should review them next.
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
                  data-graphic-item
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
            <h2 id="receipt-title">Download proof anyone can check.</h2>
            <p>
              Share one file containing the decision, the facts behind it, later
              changes, and proof that the record has not been altered.
            </p>
          </div>
          <button className="primary-button light" type="button" onClick={downloadReceipt}>
            Download Receipt v0.1 JSON
          </button>
        </div>

        <div className="assurance-grid">
          <article className="completeness-card">
            <span className="grade-badge">Grade A</span>
            <div className="score-line"><strong>{incident.completeness.score}</strong><span>required receipt fields recorded</span></div>
            <div className="coverage-bar" aria-label="12 of 12 completeness checks present"><span /></div>
            <h3>{incident.completeness.status}</h3>
            <p>{incident.completeness.exclusion}</p>
            <a href="#incident">Inspect recorded boundary ↑</a>
          </article>

          <article className="verification-card">
            <span className={`verification-dot ${verification}`} aria-label={`Verification ${verification}`} />
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
            <span className={`review-chip ${reviewState}`}>{reviewState === "needs-review" ? "Needs review" : reviewState === "reviewed" ? "Reviewed" : "Closed"}</span>
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
          <Image className="brand-logo" src="/logo-blue.png" alt="Lians" width={88} height={32} />
        </a>
        <p>Make every AI decision answerable.</p>
        <span>Memory · Authority · Evidence · Action</span>
      </footer>

      {notice && <div className="toast" role="status" aria-live="polite"><span aria-hidden="true">✓</span>{notice}</div>}
    </main>
  );
}
