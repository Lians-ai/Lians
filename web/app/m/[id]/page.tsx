import type { Metadata } from "next";
import Link from "next/link";
import { getApp, getEvents } from "../../../lib/data";
import { MiniAppView } from "./mini-app-view";

export const dynamic = "force-dynamic";

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const app = await getApp(id).catch(() => null);
  if (!app) return { title: "App not found | Lians", description: "Make a useful app with Lians." };
  const title = `${app.title} | Made with Lians`;
  const description = app.description;
  return {
    title,
    description,
    openGraph: { title, description, images: [] },
    twitter: { card: "summary", title, description, images: [] },
  };
}

export default async function MiniAppPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const app = await getApp(id).catch(() => null);
  if (!app) {
    return (
      <main className="missing-app">
        <Link className="wordmark" href="/">lians</Link>
        <h1>That one is gone.</h1>
        <Link className="missing-button" href="/">Make a new one ↗</Link>
      </main>
    );
  }
  const events = await getEvents(id).catch(() => []);
  return <MiniAppView app={app} initialEvents={events} />;
}
