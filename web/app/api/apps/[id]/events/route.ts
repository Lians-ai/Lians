import { createAppEvent, getApp, getEvents } from "../../../../../lib/data";

const allowedActions = new Set(["view", "share", "checkin", "vote", "score", "track", "item", "done", "quiz"]);

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!(await getApp(id))) return Response.json({ error: "That app does not exist." }, { status: 404 });
  return Response.json({ events: await getEvents(id) });
}

export async function POST(request: Request, context: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await context.params;
    if (!(await getApp(id))) return Response.json({ error: "That app does not exist." }, { status: 404 });
    const payload = (await request.json()) as { actor?: string; action?: string; value?: string | number };
    const actor = payload.actor?.replace(/\s+/g, " ").trim().slice(0, 32) ?? "";
    const action = payload.action?.trim() ?? "";
    const value = String(payload.value ?? "").trim().slice(0, 120);
    if (!actor) return Response.json({ error: "Add your name first." }, { status: 400 });
    if (!allowedActions.has(action)) return Response.json({ error: "That action is not available." }, { status: 400 });
    if (!value) return Response.json({ error: "Add a value first." }, { status: 400 });
    const event = await createAppEvent(id, actor, action, value);
    return Response.json({ event }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not save that." }, { status: 500 });
  }
}
