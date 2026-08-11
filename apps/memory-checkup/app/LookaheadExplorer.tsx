"use client";

import { useMemo, useState } from "react";
import {
  canonicalLookaheadDecision,
  LOOKAHEAD_TRUSTED_PUBLIC_KEY,
  lookaheadDecisions,
  lookaheadSummary,
  type LookaheadDecision,
} from "./incidents/lookahead-bias";
import lookaheadReceipt from "./incidents/lookahead-receipt.json";

type DecisionFilter = "all" | "contaminated" | "no-future";
type ReceiptVerification =
  | "ready"
  | "checking"
  | "verified"
  | "failed"
  | "unavailable";

const decisionFilters: Array<{ id: DecisionFilter; label: string }> = [
  { id: "all", label: "All 744" },
  { id: "contaminated", label: "Future evidence" },
  { id: "no-future", label: "No future-use row" },
];

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
    Object.entries(lookaheadReceipt).filter(([key]) => key !== "integrity"),
  );
}

function formatDay(value: string) {
  const [year, month, day] = value.split("-");
  return `${month}/${day}/${year}`;
}

function formatTimestamp(value: string) {
  return value.replace("T", " ").replace("Z", " UTC");
}

function formatPosition(value: LookaheadDecision["position"]) {
  if (value === -1) return "SHORT";
  if (value === 1) return "LONG";
  if (value === 0) return "FLAT";
  return "NOT IN FUTURE-USE LEDGER";
}

function formatReturn(value: number) {
  const percentage = value * 100;
  return `${percentage >= 0 ? "+" : ""}${percentage.toFixed(3)}%`;
}

export default function LookaheadExplorer() {
  const canonicalDecision =
    lookaheadDecisions.find(
      (decision) => decision.id === canonicalLookaheadDecision.id,
    ) ?? lookaheadDecisions[0];
  const [imported, setImported] = useState(false);
  const [selectedId, setSelectedId] = useState(canonicalDecision.id);
  const [decisionFilter, setDecisionFilter] =
    useState<DecisionFilter>("all");
  const [query, setQuery] = useState("");
  const [reconstructed, setReconstructed] = useState(false);
  const [verification, setVerification] =
    useState<ReceiptVerification>("ready");

  const selectedDecision =
    lookaheadDecisions.find((decision) => decision.id === selectedId) ??
    canonicalDecision;
  const isCanonicalDecision =
    selectedDecision.id === canonicalLookaheadDecision.id;

  const visibleDecisions = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    return lookaheadDecisions.filter((decision) => {
      const matchesFilter =
        decisionFilter === "all" ||
        (decisionFilter === "contaminated" && decision.futureCount > 0) ||
        (decisionFilter === "no-future" && decision.futureCount === 0);
      const matchesQuery =
        !normalizedQuery ||
        decision.id.toLowerCase().includes(normalizedQuery) ||
        decision.ticker.toLowerCase().includes(normalizedQuery) ||
        decision.marketDate.includes(normalizedQuery) ||
        decision.decisionAt.includes(normalizedQuery);
      return matchesFilter && matchesQuery;
    });
  }, [decisionFilter, query]);

  const steps = [
    { number: "01", label: "Import", complete: imported },
    { number: "02", label: "List all decisions", complete: imported },
    { number: "03", label: "Select contamination", complete: selectedDecision.futureCount > 0 },
    { number: "04", label: "Disclose future evidence", complete: selectedDecision.evidence.length > 0 },
    { number: "05", label: "Reconstruct cutoff", complete: reconstructed },
    { number: "06", label: "Compare outcomes", complete: reconstructed },
    { number: "07", label: "Verify receipt", complete: verification === "verified" },
  ];

  function importRun() {
    setImported(true);
    setSelectedId(canonicalDecision.id);
    setDecisionFilter("all");
    setQuery("");
    setReconstructed(false);
    setVerification("ready");
  }

  function selectDecision(decision: LookaheadDecision) {
    setSelectedId(decision.id);
    setReconstructed(false);
  }

  function openCanonicalDecision() {
    setSelectedId(canonicalDecision.id);
    setReconstructed(false);
  }

  function downloadReceipt() {
    const blob = new Blob([JSON.stringify(lookaheadReceipt, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download =
      "lians-lookahead-decision-receipt-v0.1-hlio-20260204.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  async function verifyReceipt() {
    setVerification("checking");
    try {
      const payloadBytes = new TextEncoder().encode(
        canonicalJson(receiptProtectedPayload()),
      );
      const digest = await window.crypto.subtle.digest("SHA-256", payloadBytes);
      const digestHex = bytesToHex(digest);
      const signature = lookaheadReceipt.integrity.signature;
      const pinnedKeyMatches =
        signature.public_key === LOOKAHEAD_TRUSTED_PUBLIC_KEY;
      const keyIdentityMatches =
        signature.key_id === lookaheadReceipt.issuer.key_id;

      if (
        digestHex !== lookaheadReceipt.integrity.receipt_hash ||
        !pinnedKeyMatches ||
        !keyIdentityMatches
      ) {
        setVerification("failed");
        return;
      }

      const publicKey = await window.crypto.subtle.importKey(
        "raw",
        base64ToBytes(LOOKAHEAD_TRUSTED_PUBLIC_KEY),
        "Ed25519",
        false,
        ["verify"],
      );
      const signatureValid = await window.crypto.subtle.verify(
        "Ed25519",
        publicKey,
        base64ToBytes(signature.value),
        digest,
      );
      setVerification(signatureValid ? "verified" : "failed");
    } catch {
      setVerification("unavailable");
    }
  }

  return (
    <section
      className="backtest-section"
      id="backtest"
      aria-labelledby="backtest-title"
    >
      <div className="backtest-heading">
        <div>
          <p className="eyebrow">
            <span aria-hidden="true">05</span> TRACK E0 / DECISION EXPLORER
          </p>
          <h2 id="backtest-title">See exactly how a backtest cheated.</h2>
          <p>
            Import one deterministic run, inspect every ticker decision, expose
            the future evidence that entered context, then rebuild the same
            decision at its actual historical cutoff.
          </p>
        </div>
        <div className="backtest-proof-mark" aria-label="Deterministic run identity">
          <span>REPRODUCIBLE FIXTURE</span>
          <strong>SEED 42 / 2026 H1</strong>
          <small>No API key, model call, or live market action</small>
        </div>
      </div>

      <div className="import-console">
        <div className="import-copy">
          <p className="panel-kicker">01 / DETERMINISTIC IMPORT</p>
          <h3>{imported ? "Run manifest imported" : "Load the committed evidence run"}</h3>
          <p>
            The manifest binds 75 timestamped notes, six synthetic tickers, 125
            market days, 744 ticker decisions, and the generated result files.
            Importing changes only this browser session.
          </p>
        </div>
        <dl className="import-manifest">
          <div><dt>Dataset</dt><dd>{lookaheadSummary.datasetId}</dd></div>
          <div><dt>Decision rows</dt><dd>{lookaheadSummary.decisions}</dd></div>
          <div><dt>Detector checkpoint</dt><dd>{formatTimestamp(lookaheadSummary.checkpoint)}</dd></div>
          <div><dt>Capture status</dt><dd>Complete with known exclusions</dd></div>
        </dl>
        <button className="primary-button" type="button" onClick={importRun}>
          {imported ? "Re-import deterministic run" : "Import deterministic run"}
        </button>
      </div>

      <div className="capture-disclosure">
        <strong>Declared boundary</strong>
        <p>
          Directly observed: committed seed-42 notes, prices, strategy settings,
          retrieval rows, and result files. Excluded: hidden model cognition,
          live brokerage execution, and claims about future performance.
        </p>
        <span>Instrumentation: lookahead-seed42-v1</span>
      </div>

      <ol className="e0-progress" aria-label="Track E0 workflow progress">
        {steps.map((step) => (
          <li className={step.complete ? "complete" : "pending"} key={step.number}>
            <span>{step.complete ? "OK" : step.number}</span>
            <strong>{step.label}</strong>
          </li>
        ))}
      </ol>

      {!imported ? (
        <div className="backtest-empty-state">
          <span aria-hidden="true">42</span>
          <div>
            <h3>The evidence directory is ready to import.</h3>
            <p>
              The run is deterministic and local. Import it to render all 744
              decisions and the complete 918-row future-evidence ledger.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="backtest-kpis" aria-label="Imported run summary">
            <div><span>Imported notes</span><strong>75</strong><small>timestamped evidence events</small></div>
            <div><span>Decision directory</span><strong>744</strong><small>124 days x 6 tickers</small></div>
            <div><span>Contaminated decisions</span><strong>499</strong><small>future evidence was used</small></div>
            <div><span>Future retrievals</span><strong>918</strong><small>exact evidence-use rows</small></div>
          </div>

          <div className="backtest-explorer-grid">
            <aside className="backtest-directory" aria-labelledby="decision-directory-title">
              <div className="directory-heading">
                <div>
                  <p className="panel-kicker">02 / FULL DECISION LIST</p>
                  <h3 id="decision-directory-title">Every ticker decision</h3>
                </div>
                <span>{visibleDecisions.length} / {lookaheadSummary.decisions}</span>
              </div>
              <label className="decision-search">
                <span>Search ID, ticker, or date</span>
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="HLIO or 2026-02-04"
                />
              </label>
              <div className="decision-filter" role="group" aria-label="Filter backtest decisions">
                {decisionFilters.map((filter) => (
                  <button
                    type="button"
                    key={filter.id}
                    className={decisionFilter === filter.id ? "active" : ""}
                    aria-pressed={decisionFilter === filter.id}
                    onClick={() => setDecisionFilter(filter.id)}
                  >
                    {filter.label}
                  </button>
                ))}
              </div>
              <ul className="decision-list" aria-label="Imported backtest decisions">
                {visibleDecisions.map((decision) => (
                  <li key={decision.id}>
                    <button
                      type="button"
                      className={`backtest-decision-row ${selectedDecision.id === decision.id ? "active" : ""}`}
                      aria-pressed={selectedDecision.id === decision.id}
                      onClick={() => selectDecision(decision)}
                    >
                      <span className="decision-row-date">
                        <strong>{decision.ticker}</strong>
                        <small>{formatDay(decision.decisionAt.slice(0, 10))} / 21:00 UTC</small>
                      </span>
                      <span className="decision-row-position">{formatPosition(decision.position)}</span>
                      <span className={decision.futureCount > 0 ? "future-count contaminated" : "future-count bounded"}>
                        {decision.futureCount > 0 ? `${decision.futureCount} future` : "0 future"}
                      </span>
                    </button>
                  </li>
                ))}
                {visibleDecisions.length === 0 && (
                  <li className="decision-list-empty">No decision matches this filter.</li>
                )}
              </ul>
              <p className="directory-truth-note">
                No future-use row is not labeled clean: the checkpoint detector
                also distinguishes late revisions.
              </p>
            </aside>

            <article className="backtest-decision-detail" aria-live="polite">
              <div className="selected-decision-heading">
                <div>
                  <p className="panel-kicker">03 / SELECTED DECISION</p>
                  <h3>{selectedDecision.ticker} at {formatTimestamp(selectedDecision.decisionAt)}</h3>
                  <code>{selectedDecision.id}</code>
                </div>
                <span className={selectedDecision.futureCount > 0 ? "decision-verdict contaminated" : "decision-verdict bounded"}>
                  {selectedDecision.futureCount > 0 ? "FUTURE EVIDENCE USED" : "NO FUTURE-USE ROW"}
                </span>
              </div>

              <dl className="selected-decision-facts">
                <div><dt>Historical cutoff</dt><dd>{formatTimestamp(selectedDecision.decisionAt)}</dd></div>
                <div><dt>Market date</dt><dd>{formatDay(selectedDecision.marketDate)}</dd></div>
                <div><dt>Recorded position</dt><dd>{formatPosition(selectedDecision.position)}</dd></div>
                <div><dt>Underlying next-day move</dt><dd>{formatReturn(selectedDecision.nextDayReturn)}</dd></div>
              </dl>

              <div className="evidence-use-states" aria-label="Evidence-use states">
                <div><span>AVAILABLE</span><strong>Present-time full history</strong><small>surrounding store</small></div>
                <div><span>RETRIEVED</span><strong>{selectedDecision.futureCount} future items</strong><small>exact receipt rows</small></div>
                <div><span>INCLUDED</span><strong>{selectedDecision.futureCount > 0 ? "Scored by rule" : "Not recorded"}</strong><small>do not infer use from availability</small></div>
                <div><span>CONFIRMED</span><strong>Deterministic path</strong><small>keyword score and cutoff</small></div>
              </div>

              <div className="future-evidence-panel">
                <div className="future-evidence-heading">
                  <div>
                    <p className="panel-kicker">04 / FUTURE EVIDENCE DISCLOSURE</p>
                    <h4>Information that did not exist at decision time</h4>
                  </div>
                  <span>{selectedDecision.futureCount} rows</span>
                </div>
                {selectedDecision.evidence.length > 0 ? (
                  <div className="future-evidence-list">
                    {selectedDecision.evidence.map((item, index) => (
                      <article key={`${item.eventTime}-${index}`}>
                        <div>
                          <span>FUTURE +{item.daysInFuture.toFixed(1)} DAYS</span>
                          <strong>{formatTimestamp(item.eventTime)}</strong>
                        </div>
                        <p>{item.note}</p>
                        <div className="future-evidence-meta">
                          <span>Provenance: imported research-desk fixture</span>
                          <span>Use: retrieved + included in deterministic score</span>
                        </div>
                      </article>
                    ))}
                  </div>
                ) : (
                  <div className="no-future-evidence">
                    <strong>No future-used evidence is recorded for this ticker decision.</strong>
                    <p>This does not prove the full replay was clean; inspect capture status and late-revision flags.</p>
                  </div>
                )}
              </div>

              <div className={`cutoff-reconstruction ${reconstructed ? "reconstructed" : "recorded"}`}>
                <div>
                  <p className="panel-kicker">05 / HISTORICAL CUTOFF</p>
                  <h4>{reconstructed ? "Point-in-time context reconstructed" : "Present-time context recorded"}</h4>
                  <p>
                    {reconstructed
                      ? `recall_at(as_of=${selectedDecision.decisionAt}) excludes every item whose event time is later than the cutoff.`
                      : "The contaminated branch used ordinary recall(), so information from later dates was reachable and scored."}
                  </p>
                </div>
                <button
                  className={reconstructed ? "secondary-button" : "primary-button"}
                  type="button"
                  onClick={() => setReconstructed((value) => !value)}
                >
                  {reconstructed ? "Show recorded contaminated context" : "Reconstruct at decision cutoff"}
                </button>
                {reconstructed && (
                  <div className="reconstruction-result">
                    <span>EXCLUDED BY CUTOFF</span>
                    <strong>{selectedDecision.futureCount} future evidence rows</strong>
                    {isCanonicalDecision ? (
                      <>
                        <p>{canonicalLookaheadDecision.availableAtCutoff.note}</p>
                        <dl>
                          <div><dt>Available evidence</dt><dd>1 neutral preview</dd></div>
                          <div><dt>Evidence use</dt><dd>{canonicalLookaheadDecision.availableAtCutoff.useState}</dd></div>
                          <div><dt>Historical position</dt><dd>FLAT</dd></div>
                          <div><dt>Strategy contribution</dt><dd>{canonicalLookaheadDecision.historical.strategyContribution}</dd></div>
                        </dl>
                      </>
                    ) : (
                      <p>
                        This directory proves the cutoff and exclusions for every
                        row. The portable deep comparison is intentionally bound
                        to the canonical HLIO decision rather than inventing an
                        unrecorded historical position.
                      </p>
                    )}
                  </div>
                )}
              </div>
            </article>
          </div>

          <section className="backtest-comparison" aria-labelledby="comparison-title">
            <div className="comparison-heading">
              <div>
                <p className="panel-kicker">06 / DETERMINISTIC COMPARISON</p>
                <h3 id="comparison-title">One parameter changed the result.</h3>
              </div>
              <code>recall() -&gt; recall_at(as_of=decision_time)</code>
            </div>
            <div className="backtest-result-grid">
              <article className="result-card contaminated">
                <span>CONTAMINATED / RECORDED RUN</span>
                <strong>{lookaheadSummary.contaminated.totalReturn}</strong>
                <p>Sharpe {lookaheadSummary.contaminated.sharpe}</p>
                <dl><div><dt>Retrieval</dt><dd>Present-time full history</dd></div><div><dt>Max drawdown</dt><dd>-4.3%</dd></div></dl>
              </article>
              <article className="result-card historical">
                <span>HISTORICAL / CUTOFF-CORRECT RUN</span>
                <strong>{lookaheadSummary.honest.totalReturn}</strong>
                <p>Sharpe {lookaheadSummary.honest.sharpe}</p>
                <dl><div><dt>Retrieval</dt><dd>Point-in-time recall</dd></div><div><dt>Max drawdown</dt><dd>-12.8%</dd></div></dl>
              </article>
              <article className="result-card benchmark">
                <span>REFERENCE / BUY AND HOLD</span>
                <strong>{lookaheadSummary.benchmark.totalReturn}</strong>
                <p>Sharpe {lookaheadSummary.benchmark.sharpe}</p>
                <dl><div><dt>Purpose</dt><dd>Comparison baseline</dd></div><div><dt>Max drawdown</dt><dd>-5.4%</dd></div></dl>
              </article>
            </div>
            <div className="comparison-honesty">
              <strong>What this proves</strong>
              <p>
                For this deterministic fixture, the contaminated run returned
                +44.0% with a 4.6 Sharpe while the cutoff-correct run returned
                -4.2% with a -0.6 Sharpe. It does not prove future performance or
                reproduce hidden model reasoning; no model simulation occurs.
              </p>
            </div>
          </section>

          <section className="lookahead-receipt" aria-labelledby="lookahead-receipt-title">
            <div className="lookahead-receipt-copy">
              <p className="panel-kicker">07 / OPEN RECEIPT</p>
              <h3 id="lookahead-receipt-title">Verify the proof without trusting this page.</h3>
              <p>
                The portable Decision Receipt v0.1 is bound to the canonical
                HLIO decision. Browser verification recomputes canonical JSON,
                checks the SHA-256 hash, pins the expected key independently of
                the receipt, and verifies Ed25519 over the raw digest.
              </p>
              {!isCanonicalDecision && (
                <button className="quiet-button receipt-jump" type="button" onClick={openCanonicalDecision}>
                  Open receipt-bound canonical decision
                </button>
              )}
              <div className="lookahead-receipt-actions">
                <button className="primary-button light" type="button" onClick={downloadReceipt}>
                  Download Decision Receipt v0.1
                </button>
                <button
                  className="outline-button"
                  type="button"
                  onClick={verifyReceipt}
                  disabled={verification === "checking"}
                >
                  {verification === "checking" ? "Verifying..." : "Independently verify receipt"}
                </button>
              </div>
            </div>
            <div className={`lookahead-verifier ${verification}`} aria-live="polite">
              <div className="verifier-status">
                <span aria-hidden="true">{verification === "verified" ? "OK" : verification === "failed" ? "!" : "#"}</span>
                <div>
                  <strong>
                    {verification === "ready" && "Ready for independent verification"}
                    {verification === "checking" && "Recomputing protected bytes..."}
                    {verification === "verified" && "Hash, pinned key, and signature verified"}
                    {verification === "failed" && "Receipt verification failed"}
                    {verification === "unavailable" && "Ed25519 unavailable in this browser"}
                  </strong>
                  <small>Synthetic key; pinned separately in the Explorer bundle</small>
                </div>
              </div>
              <dl>
                <div><dt>Receipt decision</dt><dd>{lookaheadReceipt.decision.id}</dd></div>
                <div><dt>Receipt hash</dt><dd><code>{lookaheadReceipt.integrity.receipt_hash}</code></dd></div>
                <div><dt>Pinned key</dt><dd><code>{LOOKAHEAD_TRUSTED_PUBLIC_KEY}</code></dd></div>
                <div><dt>Capture</dt><dd>Complete with declared fixture exclusions</dd></div>
              </dl>
            </div>
          </section>
        </>
      )}
    </section>
  );
}
