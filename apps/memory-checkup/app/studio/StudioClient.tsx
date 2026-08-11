"use client";

import Link from "next/link";
import { FormEvent, useCallback, useMemo, useState } from "react";

type MemoryState =
  | "active"
  | "historical"
  | "superseded"
  | "retired"
  | "erased"
  | "all";
type ControlAction = "confirm" | "pin" | "demote" | "retire" | "replace";

type PriorityMetadata = {
  tier?: string;
  kind?: string;
  durable?: boolean;
  signals?: string[];
};

type StudioControl = {
  action?: string;
  actor?: string;
  note?: string | null;
  at?: string;
};

type Memory = {
  id: string;
  namespace: string;
  agent_id: string;
  content: string | null;
  subject_id: string | null;
  event_time: string;
  ingestion_time: string;
  valid_from: string;
  valid_to: string | null;
  superseded_by: string | null;
  supersession_confidence: number | null;
  barrier_group: string | null;
  importance: number;
  source: string | null;
  content_hash: string;
  erased_at: string | null;
  metadata: Record<string, unknown> & {
    _memory_priority?: PriorityMetadata;
    _studio_control?: StudioControl;
    _pinned?: boolean;
  };
  score?: number | null;
  score_breakdown?: Record<string, unknown> | null;
  scope?: string | null;
  enrichment_status?: "pending" | "complete";
};

type AgentPolicy = {
  profile: string;
  profile_version: string;
  revision: number;
  effective: {
    capture?: Record<string, unknown>;
    recall?: Record<string, unknown>;
    lifecycle?: Record<string, unknown>;
  };
};

type Connector = {
  id: string;
  kind: string;
  name: string;
  agent_id: string;
  scope: string | null;
  status: string;
  last_sync_at: string | null;
};

type ControlPlane = {
  posture: { production_ready?: boolean; audit_chain?: { status?: string } };
  memory: { active?: number };
  governance: { open_conflicts?: number; pending_admissions?: number };
  evidence: { decisions?: number; replayable_rate?: number };
  operations: { jobs?: { dead?: number; pending?: number } };
  attention: Array<{ severity?: string; code?: string; count?: number }>;
};

type MemoryList = {
  memories: Memory[];
  total: number;
  limit: number;
  offset: number;
  state: MemoryState;
};

type RecallReport = {
  memories: Memory[];
  total_candidates: number;
  retrieval_degraded: boolean;
  token_estimate: number;
  retrieval_confidence: number;
  latency_ms: number;
  mode: "fast" | "deep" | "reconstruct";
  latency_budget_ms: number;
  deadline_exceeded: boolean;
  provenance_coverage: number;
  receipt_sha256: string;
};

type Connection = {
  apiUrl: string;
  apiKey: string;
  agentId: string;
  scope: string;
};

const policyProfiles = [
  "balanced",
  "personal_assistant",
  "coding_agent",
  "support_agent",
  "regulated_analyst",
];

const PAGE_SIZE = 50;
const memoryStates: { value: MemoryState; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "historical", label: "History" },
  { value: "superseded", label: "Superseded" },
  { value: "retired", label: "Retired" },
  { value: "erased", label: "Erased" },
  { value: "all", label: "All records" },
];

function normalizeApiUrl(value: string) {
  const url = new URL(value);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("Use an http:// or https:// Lians API URL.");
  }
  return url.toString().replace(/\/$/, "");
}

function formatTime(value: string | null) {
  if (!value) return "Open";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function shortId(value: string | null) {
  return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : "—";
}

function priorityFor(memory: Memory) {
  return memory.metadata._memory_priority ?? {};
}

function memoryStatus(memory: Memory) {
  if (memory.erased_at) return "Erased";
  if (memory.superseded_by) return "Superseded";
  if (memory.valid_to) return "Retired";
  if (memory.metadata._pinned) return "Pinned";
  return "Active";
}

export default function StudioClient() {
  const [draftConnection, setDraftConnection] = useState<Connection>({
    apiUrl: process.env.NEXT_PUBLIC_LIANS_API_URL ?? "http://127.0.0.1:8000",
    apiKey: "",
    agentId: "",
    scope: "",
  });
  const [connection, setConnection] = useState<Connection | null>(null);
  const [inventory, setInventory] = useState<MemoryList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [state, setState] = useState<MemoryState>("active");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [actor, setActor] = useState("studio-user");
  const [note, setNote] = useState("");
  const [correction, setCorrection] = useState("");
  const [confirmAction, setConfirmAction] = useState<ControlAction | null>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const [recallQuery, setRecallQuery] = useState("");
  const [recallMode, setRecallMode] = useState<"fast" | "deep" | "reconstruct">("fast");
  const [recallReport, setRecallReport] = useState<RecallReport | null>(null);
  const [recallBusy, setRecallBusy] = useState(false);
  const [agentPolicy, setAgentPolicy] = useState<AgentPolicy | null>(null);
  const [policyDraft, setPolicyDraft] = useState("balanced");
  const [policyBusy, setPolicyBusy] = useState(false);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectorKind, setConnectorKind] = useState("direct");
  const [connectorName, setConnectorName] = useState("");
  const [connectorBusy, setConnectorBusy] = useState(false);
  const [controlPlane, setControlPlane] = useState<ControlPlane | null>(null);

  const selected = useMemo(
    () => inventory?.memories.find((memory) => memory.id === selectedId) ?? null,
    [inventory, selectedId],
  );

  const pageStats = useMemo(() => {
    const memories = inventory?.memories ?? [];
    return {
      durable: memories.filter((memory) => priorityFor(memory).durable).length,
      preferences: memories.filter((memory) => priorityFor(memory).kind === "preference")
        .length,
      pinned: memories.filter((memory) => memory.metadata._pinned).length,
      review: memories.filter((memory) => {
        const review = memory.metadata._learning_review as { status?: string } | undefined;
        return review?.status === "pending";
      }).length,
    };
  }, [inventory]);

  const fetchInventory = useCallback(
    async (activeConnection: Connection, activeState: MemoryState, activeOffset: number) => {
      setLoading(true);
      setError("");
      try {
        const params = new URLSearchParams({
          state: activeState,
          limit: String(PAGE_SIZE),
          offset: String(activeOffset),
        });
        if (activeConnection.agentId.trim()) {
          params.set("agent_id", activeConnection.agentId.trim());
        }
        if (activeConnection.scope.trim()) {
          params.set("scope", activeConnection.scope.trim());
        }
        const response = await fetch(`${activeConnection.apiUrl}/v1/memories?${params}`, {
          headers: { "X-API-Key": activeConnection.apiKey },
          cache: "no-store",
        });
        if (!response.ok) {
          const payload = (await response.json().catch(() => null)) as
            | { detail?: unknown }
            | null;
          throw new Error(
            typeof payload?.detail === "string"
              ? payload.detail
              : `Lians returned ${response.status}. Check the URL, key, and access scope.`,
          );
        }
        const payload = (await response.json()) as MemoryList;
        setInventory(payload);
        setSelectedId((current) =>
          payload.memories.some((memory) => memory.id === current)
            ? current
            : (payload.memories[0]?.id ?? null),
        );
      } catch (cause) {
        setInventory(null);
        setSelectedId(null);
        setError(cause instanceof Error ? cause.message : "Unable to connect to Lians.");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const fetchWorkspaceControls = useCallback(async (activeConnection: Connection) => {
    const headers = { "X-API-Key": activeConnection.apiKey };
    const connectorResponse = await fetch(`${activeConnection.apiUrl}/v1/connectors`, {
      headers,
      cache: "no-store",
    });
    if (connectorResponse.ok) setConnectors((await connectorResponse.json()) as Connector[]);
    const controlResponse = await fetch(
      `${activeConnection.apiUrl}/v1/control-plane/overview`,
      { headers, cache: "no-store" },
    );
    setControlPlane(
      controlResponse.ok ? ((await controlResponse.json()) as ControlPlane) : null,
    );
    if (!activeConnection.agentId) {
      setAgentPolicy(null);
      return;
    }
    const policyResponse = await fetch(
      `${activeConnection.apiUrl}/v1/agents/${encodeURIComponent(activeConnection.agentId)}/policy`,
      { headers, cache: "no-store" },
    );
    if (policyResponse.ok) {
      const policy = (await policyResponse.json()) as AgentPolicy;
      setAgentPolicy(policy);
      setPolicyDraft(policy.profile);
    }
  }, []);

  function connect(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    try {
      if (!draftConnection.apiKey.trim()) throw new Error("Enter a Lians API key.");
      setOffset(0);
      const nextConnection = {
        apiUrl: normalizeApiUrl(draftConnection.apiUrl.trim()),
        apiKey: draftConnection.apiKey.trim(),
        agentId: draftConnection.agentId.trim(),
        scope: draftConnection.scope.trim(),
      };
      setConnection(nextConnection);
      void fetchInventory(nextConnection, state, 0);
      void fetchWorkspaceControls(nextConnection);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Connection settings are invalid.");
    }
  }

  async function applyControl(action: ControlAction) {
    if (!connection || !selected) return;
    const highImpact = action === "retire" || action === "replace";
    if (highImpact && confirmAction !== action) {
      setConfirmAction(action);
      return;
    }
    if (action === "replace" && !correction.trim()) {
      setError("Enter corrected memory content before replacing this record.");
      return;
    }
    setControlBusy(true);
    setError("");
    try {
      const response = await fetch(
        `${connection.apiUrl}/v1/memories/${selected.id}/control`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-API-Key": connection.apiKey,
          },
          body: JSON.stringify({
            agent_id: selected.agent_id,
            action,
            actor: actor.trim() || "studio-user",
            note: note.trim() || null,
            correction: action === "replace" ? correction.trim() : null,
          }),
        },
      );
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: unknown }
          | null;
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : `The ${action} action failed with ${response.status}.`,
        );
      }
      setNotice(`${action[0].toUpperCase()}${action.slice(1)} recorded in the audit chain.`);
      setNote("");
      setCorrection("");
      setConfirmAction(null);
      await fetchInventory(connection, state, offset);
      window.setTimeout(() => setNotice(""), 3200);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "The memory control failed.");
    } finally {
      setControlBusy(false);
    }
  }

  async function runRecall(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!connection) {
      setError("Connect a Lians environment before running recall.");
      return;
    }
    const agentId = connection.agentId || selected?.agent_id;
    if (!agentId) {
      setError("Set an agent filter or select a memory before running recall.");
      return;
    }
    if (!recallQuery.trim()) return;
    setRecallBusy(true);
    setError("");
    try {
      const response = await fetch(`${connection.apiUrl}/v1/recall`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-API-Key": connection.apiKey,
        },
        body: JSON.stringify({
          agent_id: agentId,
          query: recallQuery.trim(),
          k: 8,
          mode: recallMode,
          strategy: recallMode === "fast" ? "standard" : "adaptive",
          scope: connection.scope || null,
          include_parent_scopes: true,
        }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as
          | { detail?: unknown }
          | null;
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : `Recall failed with ${response.status}.`,
        );
      }
      setRecallReport((await response.json()) as RecallReport);
    } catch (cause) {
      setRecallReport(null);
      setError(cause instanceof Error ? cause.message : "Recall could not be completed.");
    } finally {
      setRecallBusy(false);
    }
  }

  async function updatePolicy() {
    if (!connection?.agentId || !agentPolicy) return;
    setPolicyBusy(true);
    setError("");
    try {
      const response = await fetch(
        `${connection.apiUrl}/v1/agents/${encodeURIComponent(connection.agentId)}/policy`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json", "X-API-Key": connection.apiKey },
          body: JSON.stringify({
            profile: policyDraft,
            actor: actor.trim() || "studio-user",
            expected_revision: agentPolicy.revision,
            overrides: {},
          }),
        },
      );
      if (!response.ok) throw new Error(`Policy update failed with ${response.status}.`);
      const policy = (await response.json()) as AgentPolicy;
      setAgentPolicy(policy);
      setNotice(`Policy ${policy.profile} is now active at revision ${policy.revision}.`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Policy update failed.");
    } finally {
      setPolicyBusy(false);
    }
  }

  async function createConnector(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!connection?.agentId || !connectorName.trim()) return;
    setConnectorBusy(true);
    setError("");
    try {
      const response = await fetch(`${connection.apiUrl}/v1/connectors`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-API-Key": connection.apiKey },
        body: JSON.stringify({
          kind: connectorKind,
          name: connectorName.trim(),
          agent_id: connection.agentId,
          scope: connection.scope || null,
          config: {},
        }),
      });
      if (!response.ok) throw new Error(`Connector creation failed with ${response.status}.`);
      setConnectorName("");
      await fetchWorkspaceControls(connection);
      setNotice("Connector created. Its integration gateway can now push normalized events.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Connector creation failed.");
    } finally {
      setConnectorBusy(false);
    }
  }

  return (
    <main className="studio-app">
      <header className="studio-topbar">
        <Link className="studio-brand" href="/" aria-label="Lians home">
          <span aria-hidden="true">L</span>
          <strong>Lians</strong>
          <small>Studio</small>
        </Link>
        <div className="studio-environment">
          <i className={connection ? "connected" : ""} aria-hidden="true" />
          {connection ? connection.apiUrl : "Not connected"}
        </div>
        <nav aria-label="Studio navigation">
          <Link className="active" href="/studio">Memory</Link>
          <Link href="/#backtest">Falsifiable proof</Link>
          <Link href="/#control">Controls</Link>
          <Link href="/">Decision System</Link>
        </nav>
      </header>

      <section className="studio-intro">
        <div>
          <p className="studio-kicker">THE MEMORY BEHIND ANSWERABLE AI</p>
          <h1>Know what your AI remembers—and why it matters.</h1>
          <p>
            Inspect durable preferences, trace every source, and correct the
            working set without erasing the decision history it influenced.
          </p>
        </div>
        <form className="connection-card" onSubmit={connect}>
          <label>
            <span>API URL</span>
            <input
              type="url"
              value={draftConnection.apiUrl}
              onChange={(event) =>
                setDraftConnection((value) => ({ ...value, apiUrl: event.target.value }))
              }
              placeholder="http://127.0.0.1:8000"
              required
            />
          </label>
          <label>
            <span>API key</span>
            <input
              type="password"
              value={draftConnection.apiKey}
              onChange={(event) =>
                setDraftConnection((value) => ({ ...value, apiKey: event.target.value }))
              }
              placeholder="Stored in this tab only"
              autoComplete="off"
              required
            />
          </label>
          <label>
            <span>Agent filter <em>optional</em></span>
            <input
              value={draftConnection.agentId}
              onChange={(event) =>
                setDraftConnection((value) => ({ ...value, agentId: event.target.value }))
              }
              placeholder="support-agent"
            />
          </label>
          <label>
            <span>Memory scope <em>optional</em></span>
            <input
              value={draftConnection.scope}
              onChange={(event) =>
                setDraftConnection((value) => ({ ...value, scope: event.target.value }))
              }
              placeholder="org/acme/team/platform/project/api"
            />
          </label>
          <button type="submit" disabled={loading}>
            {loading ? "Connecting…" : connection ? "Reconnect" : "Connect securely"}
          </button>
        </form>
      </section>

      {error && <div className="studio-alert error" role="alert">{error}</div>}
      {notice && <div className="studio-alert success" role="status">{notice}</div>}

      <section className="studio-metrics" aria-label="Memory inventory summary">
        <article><span>Visible records</span><strong>{inventory?.total ?? "—"}</strong><small>{state} scope</small></article>
        <article><span>Durable</span><strong>{connection ? pageStats.durable : "—"}</strong><small>on this page</small></article>
        <article><span>Preferences</span><strong>{connection ? pageStats.preferences : "—"}</strong><small>classified explicitly</small></article>
        <article><span>Pinned</span><strong>{connection ? pageStats.pinned : "—"}</strong><small>protected ranking priority</small></article>
        <article><span>Needs review</span><strong>{connection ? pageStats.review : "—"}</strong><small>human decision required</small></article>
      </section>

      <section className="studio-platform-controls" aria-label="Policy and connector controls">
        <article>
          <div className="platform-control-heading">
            <div><p>AGENT POLICY</p><h2>Capture and recall behavior</h2></div>
            {agentPolicy && <span>revision {agentPolicy.revision}</span>}
          </div>
          {!connection?.agentId ? (
            <p className="platform-empty">Set an agent filter to inspect and assign its policy.</p>
          ) : !agentPolicy ? (
            <p className="platform-empty">Connect with read access to load this agent&apos;s policy.</p>
          ) : (
            <div className="policy-editor">
              <label>
                <span>Profile</span>
                <select value={policyDraft} onChange={(event) => setPolicyDraft(event.target.value)}>
                  {policyProfiles.map((profile) => (
                    <option value={profile} key={profile}>{profile.replaceAll("_", " ")}</option>
                  ))}
                </select>
              </label>
              <button type="button" onClick={() => void updatePolicy()} disabled={policyBusy || policyDraft === agentPolicy.profile}>
                {policyBusy ? "Applying…" : "Apply policy"}
              </button>
              <dl>
                <div><dt>Admission</dt><dd>{String(agentPolicy.effective.capture?.admission_mode ?? "monitor")}</dd></div>
                <div><dt>Recall</dt><dd>{String(agentPolicy.effective.recall?.default_mode ?? "fast")}</dd></div>
                <div><dt>Retention</dt><dd>{String(agentPolicy.effective.lifecycle?.retention_days ?? "durable")}</dd></div>
              </dl>
            </div>
          )}
        </article>

        <article>
          <div className="platform-control-heading">
            <div><p>CONNECTORS</p><h2>Governed memory sources</h2></div>
            <span>{connectors.length} configured</span>
          </div>
          <form className="connector-editor" onSubmit={createConnector}>
            <select value={connectorKind} onChange={(event) => setConnectorKind(event.target.value)} aria-label="Connector kind">
              <option value="direct">Direct SDK</option>
              <option value="github">GitHub</option>
              <option value="slack">Slack</option>
              <option value="notion">Notion</option>
              <option value="google_drive">Google Drive</option>
              <option value="webhook">Webhook</option>
            </select>
            <input value={connectorName} onChange={(event) => setConnectorName(event.target.value)} placeholder="Source name" aria-label="Connector name" />
            <button type="submit" disabled={!connection?.agentId || connectorBusy}>{connectorBusy ? "Adding…" : "Add source"}</button>
          </form>
          <div className="connector-list">
            {connectors.slice(0, 4).map((connector) => (
              <div key={connector.id}>
                <span>{connector.kind.replaceAll("_", " ")}</span>
                <strong>{connector.name}</strong>
                <small>{connector.status} · {connector.scope ?? "workspace"}</small>
              </div>
            ))}
            {connectors.length === 0 && <p className="platform-empty">No sources configured yet.</p>}
          </div>
        </article>
      </section>

      {controlPlane && (
        <section className="enterprise-overview" aria-label="Enterprise control plane">
          <div>
            <p>CONTROL PLANE</p>
            <h2>Operational and evidence posture</h2>
            <span>{controlPlane.attention.length} items need attention</span>
          </div>
          <dl>
            <div><dt>Active memory</dt><dd>{controlPlane.memory.active ?? 0}</dd></div>
            <div><dt>Open conflicts</dt><dd>{controlPlane.governance.open_conflicts ?? 0}</dd></div>
            <div><dt>Admissions</dt><dd>{controlPlane.governance.pending_admissions ?? 0}</dd></div>
            <div><dt>Decisions</dt><dd>{controlPlane.evidence.decisions ?? 0}</dd></div>
            <div><dt>Replayable</dt><dd>{Math.round((controlPlane.evidence.replayable_rate ?? 0) * 100)}%</dd></div>
            <div><dt>Dead jobs</dt><dd>{controlPlane.operations.jobs?.dead ?? 0}</dd></div>
          </dl>
          <div className="posture-flags">
            <span className={controlPlane.posture.audit_chain?.status === "ok" ? "passed" : "neutral"}>Audit {controlPlane.posture.audit_chain?.status ?? "unchecked"}</span>
            <span className={controlPlane.posture.production_ready ? "passed" : "neutral"}>{controlPlane.posture.production_ready ? "Production ready" : "Development posture"}</span>
          </div>
        </section>
      )}

      <section className="studio-workspace" aria-label="Memory workspace">
        <aside className="memory-directory">
          <div className="directory-title">
            <div><p>MEMORY DIRECTORY</p><h2>Recorded knowledge</h2></div>
            <button
              type="button"
              onClick={() => connection && fetchInventory(connection, state, offset)}
              disabled={!connection || loading}
              aria-label="Refresh memory inventory"
            >
              ↻
            </button>
          </div>
          <div className="state-tabs" role="tablist" aria-label="Memory state">
            {memoryStates.map((option) => (
              <button
                type="button"
                role="tab"
                aria-selected={state === option.value}
                className={state === option.value ? "active" : ""}
                key={option.value}
                onClick={() => {
                  setState(option.value);
                  setOffset(0);
                  if (connection) void fetchInventory(connection, option.value, 0);
                }}
                disabled={!connection}
              >
                {option.label}
              </button>
            ))}
          </div>
          <div className="memory-list" aria-busy={loading}>
            {!connection && (
              <div className="directory-empty">
                <span aria-hidden="true">01</span>
                <strong>Connect a Lians environment</strong>
                <p>Your key stays in memory for this browser tab and is sent only to the API URL above.</p>
              </div>
            )}
            {connection && !loading && inventory?.memories.length === 0 && (
              <div className="directory-empty"><strong>No {state} memories</strong><p>Try another state or remove the agent filter.</p></div>
            )}
            {inventory?.memories.map((memory) => {
              const priority = priorityFor(memory);
              return (
                <button
                  type="button"
                  className={`memory-row ${selectedId === memory.id ? "active" : ""}`}
                  aria-pressed={selectedId === memory.id}
                  onClick={() => { setSelectedId(memory.id); setConfirmAction(null); }}
                  key={memory.id}
                >
                  <span className="memory-row-top">
                    <span className={`status-dot ${memoryStatus(memory).toLowerCase()}`} />
                    <strong>{priority.kind ?? "memory"}</strong>
                    <small>{formatTime(memory.event_time)}</small>
                  </span>
                  <span className="memory-content">{memory.content ?? "Content unavailable after erasure"}</span>
                  <span className="memory-row-meta">
                    <span>{memory.agent_id}</span>
                    <span>importance {memory.importance.toFixed(2)}</span>
                    <span>{priority.tier ?? memoryStatus(memory)}</span>
                  </span>
                </button>
              );
            })}
          </div>
          {inventory && inventory.total > PAGE_SIZE && (
            <div className="pagination">
              <button
                type="button"
                onClick={() => {
                  const nextOffset = Math.max(0, offset - PAGE_SIZE);
                  setOffset(nextOffset);
                  if (connection) void fetchInventory(connection, state, nextOffset);
                }}
                disabled={offset === 0}
              >Previous</button>
              <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, inventory.total)} of {inventory.total}</span>
              <button
                type="button"
                onClick={() => {
                  const nextOffset = offset + PAGE_SIZE;
                  setOffset(nextOffset);
                  if (connection) void fetchInventory(connection, state, nextOffset);
                }}
                disabled={offset + PAGE_SIZE >= inventory.total}
              >Next</button>
            </div>
          )}
        </aside>

        <article className="memory-inspector">
          {!selected ? (
            <div className="inspector-empty">
              <span aria-hidden="true">L</span>
              <h2>Choose a memory to inspect</h2>
              <p>Its classification, lifecycle, provenance, and safe controls will appear here.</p>
            </div>
          ) : (
            <>
              <header className="inspector-header">
                <div>
                  <p>MEMORY / {shortId(selected.id)}</p>
                  <h2>{priorityFor(selected).kind ?? "Recorded memory"}</h2>
                </div>
                <span className={`memory-status ${memoryStatus(selected).toLowerCase()}`}>{memoryStatus(selected)}</span>
              </header>

              <div className="inspector-content">
                <span>RECORDED CONTENT</span>
                <p>{selected.content ?? "Content is cryptographically unavailable."}</p>
              </div>

              <div className="classification-grid">
                <div><span>Classification</span><strong>{priorityFor(selected).kind ?? "unclassified"}</strong></div>
                <div><span>Retention tier</span><strong>{priorityFor(selected).tier ?? "standard"}</strong></div>
                <div><span>Importance</span><strong>{selected.importance.toFixed(2)}</strong></div>
                <div><span>Durable</span><strong>{priorityFor(selected).durable ? "Yes" : "No"}</strong></div>
              </div>

              <section className="inspector-section">
                <div className="inspector-section-title"><span>WHY THIS WAS KEPT</span><small>Deterministic signals</small></div>
                <div className="signal-list">
                  {(priorityFor(selected).signals ?? ["No priority signals recorded"]).map((signal) => <span key={signal}>{signal}</span>)}
                </div>
              </section>

              <section className="inspector-section">
                <div className="inspector-section-title"><span>PROVENANCE & VALIDITY</span><small>Bitemporal record</small></div>
                <dl className="provenance-list">
                  <div><dt>Agent</dt><dd>{selected.agent_id}</dd></div>
                  <div><dt>Subject</dt><dd>{selected.subject_id ?? "Not assigned"}</dd></div>
                  <div><dt>Source</dt><dd>{selected.source ?? "Direct write"}</dd></div>
                  <div><dt>Event time</dt><dd>{formatTime(selected.event_time)}</dd></div>
                  <div><dt>Known from</dt><dd>{formatTime(selected.valid_from)}</dd></div>
                  <div><dt>Known until</dt><dd>{formatTime(selected.valid_to)}</dd></div>
                  <div><dt>Barrier</dt><dd>{selected.barrier_group ?? "Namespace-wide"}</dd></div>
                  <div><dt>Scope</dt><dd>{selected.scope ?? "Unscoped"}</dd></div>
                  <div><dt>Enrichment</dt><dd>{selected.enrichment_status ?? "complete"}</dd></div>
                  <div><dt>Content hash</dt><dd><code>{shortId(selected.content_hash)}</code></dd></div>
                </dl>
              </section>

              {selected.metadata._studio_control && (
                <section className="last-control">
                  <span>LAST HUMAN CONTROL</span>
                  <strong>{selected.metadata._studio_control.action}</strong>
                  <p>{selected.metadata._studio_control.actor} · {formatTime(selected.metadata._studio_control.at ?? null)}</p>
                </section>
              )}

              {!selected.erased_at && !selected.valid_to && (
                <section className="control-panel">
                  <div className="inspector-section-title"><span>MEMORY CONTROLS</span><small>Every action is audited</small></div>
                  <div className="control-fields">
                    <label><span>Actor</span><input value={actor} onChange={(event) => setActor(event.target.value)} /></label>
                    <label><span>Review note</span><input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Why are you changing this memory?" /></label>
                    <label className="correction-field"><span>Corrected content <em>for replace</em></span><textarea value={correction} onChange={(event) => setCorrection(event.target.value)} placeholder="Create a corrected version while retaining this one in history." /></label>
                  </div>
                  <div className="control-actions">
                    {(["confirm", "pin", "demote", "retire", "replace"] as ControlAction[]).map((action) => {
                      const needsConfirmation = action === "retire" || action === "replace";
                      const confirming = confirmAction === action;
                      return (
                        <button
                          type="button"
                          key={action}
                          className={needsConfirmation ? "high-impact" : ""}
                          onClick={() => void applyControl(action)}
                          disabled={controlBusy}
                        >
                          {confirming ? `Confirm ${action}` : action}
                        </button>
                      );
                    })}
                  </div>
                  {confirmAction && <p className="confirmation-note">Select “Confirm {confirmAction}” again. The original record remains in the audit history.</p>}
                </section>
              )}
            </>
          )}
        </article>
      </section>

      <section className="recall-lab" aria-labelledby="recall-lab-title">
        <div className="recall-lab-heading">
          <div>
            <p className="studio-kicker">RECALL LAB / MEASURED CONTEXT</p>
            <h2 id="recall-lab-title">Test retrieval quality and latency on the live working set.</h2>
          </div>
          <span>Results include a content-addressed receipt</span>
        </div>
        <form className="recall-form" onSubmit={runRecall}>
          <label>
            <span>Ask memory</span>
            <input
              value={recallQuery}
              onChange={(event) => setRecallQuery(event.target.value)}
              placeholder="What response style does this user prefer?"
              required
            />
          </label>
          <label>
            <span>Mode</span>
            <select
              value={recallMode}
              onChange={(event) =>
                setRecallMode(event.target.value as "fast" | "deep" | "reconstruct")
              }
            >
              <option value="fast">Fast / 100 ms budget</option>
              <option value="deep">Deep / 800 ms budget</option>
              <option value="reconstruct">Reconstruct / 2,500 ms budget</option>
            </select>
          </label>
          <button type="submit" disabled={!connection || recallBusy}>
            {recallBusy ? "Measuring…" : "Run measured recall"}
          </button>
        </form>

        {!recallReport ? (
          <div className="recall-empty">
            <strong>No measured run yet.</strong>
            <p>Connect an environment and query an agent to see exact latency, confidence, provenance, token use, and ranking evidence.</p>
          </div>
        ) : (
          <div className="recall-report">
            <div className="recall-kpis">
              <article className={recallReport.deadline_exceeded ? "failed" : "passed"}>
                <span>Latency</span><strong>{recallReport.latency_ms.toFixed(1)} ms</strong><small>budget {recallReport.latency_budget_ms.toFixed(0)} ms</small>
              </article>
              <article><span>Confidence</span><strong>{Math.round(recallReport.retrieval_confidence * 100)}%</strong><small>{recallReport.total_candidates} candidates</small></article>
              <article><span>Prompt cost</span><strong>{recallReport.token_estimate}</strong><small>estimated tokens</small></article>
              <article><span>Provenance</span><strong>{Math.round(recallReport.provenance_coverage * 100)}%</strong><small>coverage</small></article>
              <article className={recallReport.retrieval_degraded ? "failed" : "passed"}><span>Retrieval</span><strong>{recallReport.retrieval_degraded ? "Degraded" : "Healthy"}</strong><small>{recallReport.mode} mode</small></article>
            </div>
            <div className="recall-results">
              <div className="recall-results-title"><span>RANKED EVIDENCE</span><code>{shortId(recallReport.receipt_sha256)}</code></div>
              {recallReport.memories.map((memory, index) => (
                <article key={memory.id}>
                  <span className="recall-rank">{String(index + 1).padStart(2, "0")}</span>
                  <div><strong>{memory.content ?? "Unavailable content"}</strong><small>{memory.source ?? "direct write"} · {formatTime(memory.event_time)}</small></div>
                  <span className="recall-score">{memory.score == null ? "—" : memory.score.toFixed(3)}</span>
                </article>
              ))}
              {recallReport.memories.length === 0 && <p className="no-results">No memory passed the current temporal and policy filters.</p>}
            </div>
          </div>
        )}
      </section>

      <footer className="studio-footer">
        <span>Lians Studio / history-preserving memory operations</span>
        <span>API keys are not persisted by this interface.</span>
      </footer>
    </main>
  );
}
