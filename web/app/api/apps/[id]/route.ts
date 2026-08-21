import { getApp } from "../../../../lib/data";

export async function GET(_request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  const app = await getApp(id);
  if (!app) return Response.json({ error: "That app does not exist." }, { status: 404 });
  return Response.json({ app });
}
