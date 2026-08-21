import { env } from "cloudflare:workers";
import type { AppConfig, AppEvent, AppKind, MiniApp } from "./types";

let schemaReady: Promise<void> | null = null;

function getBinding() {
  const binding = (env as unknown as { DB?: D1Database }).DB;
  if (!binding) throw new Error("The Lians database is unavailable.");
  return binding;
}

async function ensureSchema() {
  if (schemaReady) return schemaReady;
  const db = getBinding();
  schemaReady = db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS mini_apps (
      id TEXT PRIMARY KEY,
      kind TEXT NOT NULL,
      title TEXT NOT NULL,
      description TEXT NOT NULL,
      prompt TEXT NOT NULL,
      config TEXT NOT NULL,
      remix_of TEXT,
      created_at INTEGER NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS app_events (
      id TEXT PRIMARY KEY,
      app_id TEXT NOT NULL,
      actor TEXT NOT NULL,
      action TEXT NOT NULL,
      value TEXT NOT NULL,
      metadata TEXT NOT NULL,
      created_at INTEGER NOT NULL
    )`),
    db.prepare("CREATE INDEX IF NOT EXISTS idx_app_events_app_created ON app_events(app_id, created_at)"),
  ]).then(() => undefined);
  return schemaReady;
}

function fromStoredApp(row: Record<string, unknown>): MiniApp {
  const config = JSON.parse(String(row.config)) as AppConfig;
  config.accent = "#765786";
  config.accentSoft = "#1f1723";
  return {
    id: String(row.id),
    kind: String(row.kind) as AppKind,
    title: String(row.title),
    description: String(row.description),
    prompt: String(row.prompt),
    config,
    remixOf: row.remix_of ? String(row.remix_of) : null,
    createdAt: Number(row.created_at),
  };
}

function fromStoredEvent(row: Record<string, unknown>): AppEvent {
  return {
    id: String(row.id),
    appId: String(row.app_id),
    actor: String(row.actor),
    action: String(row.action),
    value: String(row.value),
    metadata: JSON.parse(String(row.metadata)) as AppEvent["metadata"],
    createdAt: Number(row.created_at),
  };
}

export async function createApp(app: MiniApp) {
  await ensureSchema();
  await getBinding().prepare(`INSERT INTO mini_apps
    (id, kind, title, description, prompt, config, remix_of, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)`)
    .bind(app.id, app.kind, app.title, app.description, app.prompt, JSON.stringify(app.config), app.remixOf, app.createdAt)
    .run();
  return app;
}

export async function getApp(id: string) {
  await ensureSchema();
  const row = await getBinding().prepare("SELECT * FROM mini_apps WHERE id = ? LIMIT 1").bind(id).first();
  return row ? fromStoredApp(row as Record<string, unknown>) : null;
}

export async function getRecentApps(limit = 8) {
  await ensureSchema();
  const result = await getBinding().prepare("SELECT * FROM mini_apps ORDER BY created_at DESC LIMIT ?").bind(limit).all();
  return result.results.map((row) => fromStoredApp(row as Record<string, unknown>));
}

export async function getEvents(appId: string) {
  await ensureSchema();
  const result = await getBinding().prepare("SELECT * FROM app_events WHERE app_id = ? ORDER BY created_at ASC LIMIT 500").bind(appId).all();
  return result.results.map((row) => fromStoredEvent(row as Record<string, unknown>));
}

export async function createAppEvent(appId: string, actor: string, action: string, value: string) {
  await ensureSchema();
  const db = getBinding();
  const cutoff = Date.now() - 86_400_000;
  const usage = await db.prepare("SELECT COUNT(*) AS count FROM app_events WHERE app_id = ? AND actor = ? AND created_at > ?")
    .bind(appId, actor, cutoff)
    .first<{ count: number }>();
  if (Number(usage?.count ?? 0) >= 50) throw new Error("That is enough activity for today. Come back tomorrow.");

  const event: AppEvent = {
    id: crypto.randomUUID(),
    appId,
    actor,
    action,
    value,
    metadata: {},
    createdAt: Date.now(),
  };
  await db.prepare(`INSERT INTO app_events
    (id, app_id, actor, action, value, metadata, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)`)
    .bind(event.id, appId, actor, action, value, "{}", event.createdAt)
    .run();
  return event;
}
