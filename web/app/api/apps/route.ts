import { createApp, getRecentApps } from "../../../lib/data";
import { generateMiniApp } from "../../../lib/generator";

export async function GET() {
  try {
    return Response.json({ apps: await getRecentApps() });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not load apps." }, { status: 500 });
  }
}

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { prompt?: string; remixOf?: string | null };
    const prompt = payload.prompt?.replace(/\s+/g, " ").trim() ?? "";
    if (prompt.length < 5) return Response.json({ error: "Tell us a little more about what you need." }, { status: 400 });
    if (prompt.length > 280) return Response.json({ error: "Keep the idea under 280 characters." }, { status: 400 });

    const app = generateMiniApp(prompt, payload.remixOf?.slice(0, 40) ?? null);
    await createApp(app);
    return Response.json({ app }, { status: 201 });
  } catch (error) {
    return Response.json({ error: error instanceof Error ? error.message : "Could not make that yet." }, { status: 500 });
  }
}
