import { index, integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const miniApps = sqliteTable("mini_apps", {
  id: text("id").primaryKey(),
  kind: text("kind").notNull(),
  title: text("title").notNull(),
  description: text("description").notNull(),
  prompt: text("prompt").notNull(),
  config: text("config").notNull(),
  remixOf: text("remix_of"),
  createdAt: integer("created_at").notNull(),
});

export const appEvents = sqliteTable(
  "app_events",
  {
    id: text("id").primaryKey(),
    appId: text("app_id").notNull(),
    actor: text("actor").notNull(),
    action: text("action").notNull(),
    value: text("value").notNull(),
    metadata: text("metadata").notNull(),
    createdAt: integer("created_at").notNull(),
  },
  (table) => [index("idx_app_events_app_created").on(table.appId, table.createdAt)],
);
