import { createAppEvent, getApp, getEvents } from "../../../../../lib/data";

const allowedActions = new Set(["view", "share", "checkin", "vote", "score", "track", "item", "done", "quiz"]);
const actionsByKind = {
  challenge: new Set(["view", "share", "checkin"]),
  vote: new Set(["view", "share", "vote"]),
  quiz: new Set(["view", "share", "quiz"]),
  leaderboard: new Set(["view", "share", "score"]),
  tracker: new Set(["view", "share", "track"]),
  list: new Set(["view", "share", "item", "done"]),
};

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!(await getApp(id))) return Response.json({ error: "That app does not exist." }, { status: 404 });
  return Response.json({ events: await getEvents(id) });
}

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await context.params;
    const app = await getApp(id);
    if (!app) return Response.json({ error: "That app does not exist." }, { status: 404 });
    const payload = (await request.json()) as { actor?: string; action?: string; value?: string | number };
    const actor = payload.actor?.replace(/\s+/g, " ").trim().slice(0, 32) ?? "";
    const action = payload.action?.trim() ?? "";
    const value = String(payload.value ?? "").trim().slice(0, 120);
    if (!actor) return Response.json({ error: "Add your name first." }, { status: 400 });
    if (!allowedActions.has(action)) return Response.json({ error: "That action is not available." }, { status: 400 });
    if (!actionsByKind[app.kind].has(action)) return Response.json({ error: "That action does not belong to this app." }, { status: 400 });
    if (!value) return Response.json({ error: "Add a value first." }, { status: 400 });
    if (action === "view" && value !== "1") return Response.json({ error: "That view is not valid." }, { status: 400 });
    if (action === "share" && !["native", "copy"].includes(value)) return Response.json({ error: "That share is not valid." }, { status: 400 });
    if (action === "checkin" && value !== "1") return Response.json({ error: "That check-in is not valid." }, { status: 400 });
    if (action === "vote" && !app.config.options.includes(value)) return Response.json({ error: "Choose one of the available options." }, { status: 400 });
    if (["score", "track"].includes(action)) {
      const amount = Number(value);
      if (!Number.isFinite(amount) || amount <= 0 || amount > 99_999_999) return Response.json({ error: "Add a valid number above zero." }, { status: 400 });
    }
    if (action === "quiz") {
      const score = Number(value);
      const maximum = app.config.questions.reduce((sum, question) => sum + Math.max(question.options.length - 1, 0), 0);
      if (!Number.isInteger(score) || score < 0 || score > maximum) return Response.json({ error: "That quiz result is not valid." }, { status: 400 });
    }
    if (action === "item" && value.length > 100) return Response.json({ error: "Keep list items under 100 characters." }, { status: 400 });
    if (action === "done") {
      const events = await getEvents(id);
      if (!events.some((event) => event.action === "item" && event.id === value)) return Response.json({ error: "That list item does not exist." }, { status: 400 });
    }
    const event = await createAppEvent(id, actor, action, value);
    return Response.json({ event }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not save that." }, { status: 500 });
  }
}
