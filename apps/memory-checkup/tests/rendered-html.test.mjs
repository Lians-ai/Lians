import assert from "node:assert/strict";
import { createHash, webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render(pathname = "/") {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}-${Math.random()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request(new URL(pathname, "http://localhost/"), {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the live Lians Studio memory workspace", async () => {
  const response = await render("/studio");
  assert.equal(response.status, 200);
  const html = await response.text();

  assert.match(html, /See and manage what your AI remembers\./i);
  assert.match(html, /Lians address/i);
  assert.match(html, /See saved memories/i);
  assert.match(html, /Search and correct/i);
  assert.match(html, /Access keys are not saved/i);
  assert.match(html, /logo-blue\.png/i);
});

test("shares the public website brand, typography, and interaction system", async () => {
  const [page, studio, styles, studioStyles] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/studio/StudioClient.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/studio/studio.css", import.meta.url), "utf8"),
  ]);

  assert.match(page, /logo-blue\.png/);
  assert.match(studio, /logo-blue\.png/);
  assert.match(styles, /font-family: "Sora"/);
  assert.doesNotMatch(styles, /DM Mono/);
  assert.match(styles, /h1,\s*\n+h2,\s*\n+h3,\s*\n+h4/);
  assert.match(styles, /lians-interactive-graphic/);
  assert.match(styles, /outline: 2px solid #7188ff/);
  assert.match(studioStyles, /box-shadow: 4px 4px 0 #7188ff/);
});

test("server-renders the Lians Investigator incident", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(
    html,
    /Make every AI decision answerable\./i,
  );
  assert.match(html, /What did it know\?/i);
  assert.match(html, /Who approved it\?/i);
  assert.match(html, /fictional lending data/i);
  assert.match(html, /Application 8127/i);
  assert.match(html, /Declined/i);
  assert.match(html, /Debt-to-income ratio was too high/i);
  assert.match(html, /income was corrected to \$96,000/i);
  assert.match(html, /Policy 4\.2 was retired/i);
});

test("renders an honest recorded boundary and differentiated impact queue", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /Recorded boundary frozen/i);
  assert.match(html, /Verified income/i);
  assert.match(html, /credit-risk-v3\.2/i);
  assert.match(html, /underwriting-agent-prod/i);
  assert.match(html, /income\.verify/i);
  assert.match(html, /Review state at decision time/i);
  assert.match(html, /Direct reference/i);
  assert.match(html, /Reachable/i);
  assert.match(html, /Estimated/i);
  assert.match(html, /Complete within declared boundary/i);
  assert.match(html, /hidden AI reasoning/i);
  assert.match(html, /Affected-decision queue/i);
});

test("renders receipt verification and local review controls", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /Hash matched\. Signature verified\./i);
  assert.match(html, /Verify hash \+ signature/i);
  assert.match(html, /Download Receipt v0\.1/i);
  assert.match(html, /Mark review complete/i);
  assert.match(html, /Close incident/i);
  assert.match(html, /local session state only/i);
});

test("server-renders the deterministic Track E0 entry point", async () => {
  const response = await render();
  const html = await response.text();

  assert.match(html, /Catch tests that used tomorrow/i);
  assert.match(html, /Load the example test/i);
  assert.match(html, /all 744 decisions/i);
  assert.match(html, /918 places where facts/i);
  assert.match(html, /Nothing leaves your browser/i);
  assert.match(html, /hidden AI reasoning/i);
});

test("lookahead directory preserves exact deterministic benchmark counts", async () => {
  const source = await readFile(
    new URL("../app/incidents/lookahead-bias.ts", import.meta.url),
    "utf8",
  );
  const marker = "export const lookaheadDecisions";
  const declaration = source.indexOf(marker);
  assert.ok(declaration >= 0, "decision directory declaration is present");
  const assignment = source.indexOf("= [", declaration);
  assert.ok(assignment >= 0, "decision array assignment is present");
  const arrayStart = assignment + 2;
  const arrayEnd = source.lastIndexOf("];");
  assert.ok(arrayStart >= 0 && arrayEnd > arrayStart, "decision array is bounded");
  const decisions = JSON.parse(source.slice(arrayStart, arrayEnd + 1));

  assert.equal(decisions.length, 744);
  assert.equal(decisions.filter((row) => row.futureCount > 0).length, 499);
  assert.equal(
    decisions.reduce((total, row) => total + row.futureCount, 0),
    918,
  );
  assert.equal(
    decisions.filter((row) => row.futureCount > 0 && row.position === 0).length,
    110,
  );
  assert.ok(decisions.every((row) => row.evidence.length === row.futureCount));
  assert.ok(
    decisions
      .flatMap((row) => row.evidence)
      .every((item) => item.daysInFuture > 0),
  );
});

test("keeps download, cryptographic verification, and metadata contracts in source", async () => {
  const [page, fixture, layout] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/incidents/lending-8127.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(page, /lians-decision-receipt-v0\.1-app-8127\.json/);
  assert.match(page, /crypto\.subtle\.digest\("SHA-256"/);
  assert.match(page, /crypto\.subtle\.verify/);
  assert.match(page, /canonicalJson\(receiptProtectedPayload\(\)\)/);
  assert.match(page, /importKey\(\s*"raw"/);
  assert.doesNotMatch(page, /pack_hash|publicKeySpki|receiptPayload/);
  assert.match(fixture, /decisionReceipt/);
  assert.match(fixture, /decision-receipt\/v0\.1\/schema\.json/);
  assert.match(fixture, /receipt_hash/);
  assert.match(fixture, /json-sort-keys-utf8-v1/);
  assert.match(fixture, /public_key/);
  assert.doesNotMatch(fixture, /evidence-pack\/v1|packHash|publicKeySpki/);
  assert.match(layout, /Lians \| Make Every AI Decision Answerable/);
  assert.doesNotMatch(layout, /Memory Checkup|Starter Project|codex-preview/);
});

test("Decision Receipt v0.1 independently verifies with backend-compatible bytes", async () => {
  const fixtureSource = await readFile(
    new URL("../app/incidents/lending-8127.ts", import.meta.url),
    "utf8",
  );
  const fixtureStart = fixtureSource.indexOf("const completenessChecks =");
  assert.ok(fixtureStart >= 0, "receipt fixture declaration is present");
  const executableFixture = fixtureSource
    .slice(fixtureStart)
    .replace("export const decisionReceipt =", "const decisionReceipt =")
    .replaceAll(" as const;", ";");
  const receipt = Function(`${executableFixture}\nreturn decisionReceipt;`)();

  const sortKeys = (value) => {
    if (Array.isArray(value)) return value.map(sortKeys);
    if (value && typeof value === "object") {
      return Object.keys(value)
        .sort()
        .reduce((sorted, key) => {
          sorted[key] = sortKeys(value[key]);
          return sorted;
        }, {});
    }
    return value;
  };
  const protectedReceipt = Object.fromEntries(
    Object.entries(receipt).filter(([key]) => key !== "integrity"),
  );
  const digest = createHash("sha256")
    .update(JSON.stringify(sortKeys(protectedReceipt)), "utf8")
    .digest();

  assert.equal(
    digest.toString("hex"),
    receipt.integrity.receipt_hash,
    "receipt_hash covers canonical sorted-key JSON",
  );
  assert.equal(receipt.integrity.signature.public_key.length, 44);
  const publicKey = await webcrypto.subtle.importKey(
    "raw",
    Buffer.from(receipt.integrity.signature.public_key, "base64"),
    "Ed25519",
    false,
    ["verify"],
  );
  const signatureValid = await webcrypto.subtle.verify(
    "Ed25519",
    publicKey,
    Buffer.from(receipt.integrity.signature.value, "base64"),
    digest,
  );
  assert.equal(signatureValid, true, "signature covers the raw digest bytes");
});

test("lookahead Decision Receipt independently verifies with pinned bytes", async () => {
  const receipt = JSON.parse(
    await readFile(
      new URL("../app/incidents/lookahead-receipt.json", import.meta.url),
      "utf8",
    ),
  );
  const sortKeys = (value) => {
    if (Array.isArray(value)) return value.map(sortKeys);
    if (value && typeof value === "object") {
      return Object.keys(value)
        .sort()
        .reduce((sorted, key) => {
          sorted[key] = sortKeys(value[key]);
          return sorted;
        }, {});
    }
    return value;
  };
  const protectedReceipt = Object.fromEntries(
    Object.entries(receipt).filter(([key]) => key !== "integrity"),
  );
  const digest = createHash("sha256")
    .update(JSON.stringify(sortKeys(protectedReceipt)), "utf8")
    .digest();

  assert.equal(digest.toString("hex"), receipt.integrity.receipt_hash);
  assert.equal(receipt.decision.id, "LB42-20260204-HLIO");
  assert.equal(receipt.policy.evaluation.future_evidence_count, 2);
  assert.equal(
    receipt.policy.evaluation.historical_reconstruction.future_evidence_count,
    0,
  );
  const publicKey = await webcrypto.subtle.importKey(
    "raw",
    Buffer.from(receipt.integrity.signature.public_key, "base64"),
    "Ed25519",
    false,
    ["verify"],
  );
  const signatureValid = await webcrypto.subtle.verify(
    "Ed25519",
    publicKey,
    Buffer.from(receipt.integrity.signature.value, "base64"),
    digest,
  );
  assert.equal(signatureValid, true, "lookahead signature covers raw digest bytes");
});
