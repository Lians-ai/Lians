"use client";

import { useEffect, useMemo, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import type { AppEvent, MiniApp } from "../../../lib/types";

type Props = { app: MiniApp; initialEvents: AppEvent[] };

export function MiniAppView({ app, initialEvents }: Props) {
  const [events, setEvents] = useState(initialEvents);
  const [actor, setActor] = useState(() => typeof window === "undefined" ? "" : window.localStorage.getItem("lians-name") ?? "");
  const [value, setValue] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [quizStep, setQuizStep] = useState(0);
  const [quizScore, setQuizScore] = useState(0);
  const visitorId = useMemo(() => {
    if (typeof window === "undefined") return "";
    const saved = window.localStorage.getItem("lians-visitor");
    if (saved) return saved;
    const created = `visitor-${crypto.randomUUID().slice(0, 12)}`;
    window.localStorage.setItem("lians-visitor", created);
    return created;
  }, []);

  useEffect(() => {
    if (!visitorId) return;
    void fetch(`/api/apps/${app.id}/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ actor: visitorId, action: "view", value: "1" }),
    });
  }, [app.id, visitorId]);

  const actorTotals = useMemo(() => {
    const totals = new Map<string, number>();
    for (const event of events) {
      if (["checkin", "score", "track"].includes(event.action)) {
        const amount = event.action === "checkin" ? 1 : Number(event.value) || 0;
        totals.set(event.actor, (totals.get(event.actor) ?? 0) + amount);
      }
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1]);
  }, [events]);

  const votes = useMemo(() => {
    const latest = new Map<string, string>();
    events.filter((event) => event.action === "vote").forEach((event) => latest.set(event.actor, event.value));
    return app.config.options.map((option) => ({ option, count: [...latest.values()].filter((value) => value === option).length }));
  }, [app.config.options, events]);

  const listItems = useMemo(() => {
    const done = new Set(events.filter((event) => event.action === "done").map((event) => event.value));
    return events.filter((event) => event.action === "item").map((event) => ({ ...event, done: done.has(event.id) }));
  }, [events]);

  async function refresh() {
    const response = await fetch(`/api/apps/${app.id}/events`);
    if (response.ok) {
      const payload = await response.json() as { events: AppEvent[] };
      setEvents(payload.events);
    }
  }

  async function post(action: string, nextValue: string | number) {
    const cleanActor = actor.trim();
    if (!cleanActor) {
      setMessage("Add your name first.");
      document.getElementById("participant-name")?.focus();
      return false;
    }
    setBusy(true);
    setMessage("");
    window.localStorage.setItem("lians-name", cleanActor);
    try {
      const response = await fetch(`/api/apps/${app.id}/events`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ actor: cleanActor, action, value: nextValue }),
      });
      const payload = await response.json() as { error?: string };
      if (!response.ok) throw new Error(payload.error || "Could not save that.");
      await refresh();
      return true;
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : "Could not save that.");
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function record(action: "share", nextValue: string) {
    if (!visitorId) return;
    await fetch(`/api/apps/${app.id}/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ actor: visitorId, action, value: nextValue }),
    }).catch(() => undefined);
  }

  async function share() {
    const data = { title: app.title, text: `Join ${app.title}`, url: window.location.href };
    if (navigator.share) {
      try {
        await navigator.share(data);
        await record("share", "native");
      } catch {
        return;
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(window.location.href);
      await record("share", "copy");
      setMessage("Link copied.");
    } catch {
      setMessage("Copy this page from your address bar.");
    }
  }

  async function addValue(action: "score" | "track") {
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount <= 0) {
      setMessage("Add a number above zero.");
      return;
    }
    if (await post(action, amount)) setValue("");
  }

  async function answerQuiz(optionIndex: number) {
    if (!actor.trim()) {
      setMessage("Add your name first.");
      document.getElementById("participant-name")?.focus();
      return;
    }
    const nextScore = quizScore + optionIndex;
    if (quizStep + 1 >= app.config.questions.length) {
      if (!(await post("quiz", String(nextScore)))) return;
      setQuizScore(nextScore);
      setQuizStep(app.config.questions.length);
      return;
    }
    setMessage("");
    setQuizScore(nextScore);
    setQuizStep((step) => step + 1);
  }

  async function addListItem() {
    const clean = value.trim();
    if (!clean) {
      setMessage("Add something first.");
      return;
    }
    if (await post("item", clean)) setValue("");
  }

  const total = actorTotals.reduce((sum, [, amount]) => sum + amount, 0);
  const completeQuiz = quizStep >= app.config.questions.length;
  const quizResult = quizScore <= 2 ? "The calm one" : quizScore <= 4 ? "The spark" : "The chaos captain";

  return (
    <main className="mini-shell" style={{ "--app-accent": app.config.accent, "--app-soft": app.config.accentSoft } as React.CSSProperties}>
      <nav className="mini-nav">
        <Link className="brand-mark" href="/"><Image src="/lians-lotus.svg" width={46} height={28} alt="" /><span>lians</span></Link>
        <div><button type="button" onClick={() => void share()}>Share</button><Link href={`/?remix=${app.id}&prompt=${encodeURIComponent(app.prompt)}`}>Remix</Link></div>
      </nav>

      <header className="mini-hero">
        <span className="mini-symbol" aria-hidden="true">{app.config.emoji}</span>
        <h1>{app.title}</h1>
        <h2>{app.description}</h2>
      </header>

      <section className="mini-workspace">
        <div className="mini-identity">
          <label htmlFor="participant-name">Your name</label>
          <input id="participant-name" value={actor} onChange={(event) => setActor(event.target.value.slice(0, 32))} placeholder="Add a name" />
        </div>

        {app.kind === "challenge" && (
          <div className="mini-action-panel">
            <strong className="big-number">{total}</strong><span>{app.config.unit} together</span>
            <button type="button" disabled={busy} onClick={() => void post("checkin", "1")}>I did it today <b>+1</b></button>
          </div>
        )}

        {app.kind === "vote" && (
          <div className="vote-panel">
            {votes.map(({ option, count }) => (
              <button key={option} type="button" disabled={busy} onClick={() => void post("vote", option)}><strong>{option}</strong><span>{count} {count === 1 ? "vote" : "votes"}</span></button>
            ))}
          </div>
        )}

        {app.kind === "quiz" && !completeQuiz && (
          <div className="quiz-panel">
            <p>{quizStep + 1} of {app.config.questions.length}</p>
            <h3>{app.config.questions[quizStep].question}</h3>
            {app.config.questions[quizStep].options.map((option, index) => <button key={option} type="button" disabled={busy} onClick={() => void answerQuiz(index)}>{option}</button>)}
          </div>
        )}

        {app.kind === "quiz" && completeQuiz && (
          <div className="quiz-result"><p>You are</p><strong>{quizResult}</strong><button type="button" onClick={() => { setQuizStep(0); setQuizScore(0); }}>Play again</button></div>
        )}

        {(app.kind === "leaderboard" || app.kind === "tracker") && (
          <div className="number-panel">
            <div><strong className="big-number">{total}</strong><span>{app.config.unit} together</span></div>
            <div className="number-entry"><input inputMode="numeric" value={value} onChange={(event) => setValue(event.target.value.replace(/[^0-9.]/g, "").slice(0, 8))} placeholder="0" /><button type="button" disabled={busy} onClick={() => void addValue(app.kind === "leaderboard" ? "score" : "track")}>Add it</button></div>
          </div>
        )}

        {app.kind === "list" && (
          <div className="list-panel">
            <div className="list-entry"><input value={value} onChange={(event) => setValue(event.target.value.slice(0, 100))} placeholder="Add something" /><button type="button" disabled={busy} onClick={() => void addListItem()}>Add</button></div>
            <div className="list-items">
              {listItems.length === 0 && <p>The list is empty. Add the first thing.</p>}
              {listItems.map((item) => <button className={item.done ? "is-done" : ""} key={item.id} type="button" onClick={() => !item.done && void post("done", item.id)}><i>{item.done ? "✓" : ""}</i><span>{item.value}</span><small>{item.actor}</small></button>)}
            </div>
          </div>
        )}

        {message && <p className="mini-message" role="status">{message}</p>}
      </section>

      {(app.kind === "challenge" || app.kind === "leaderboard" || app.kind === "tracker") && (
        <section className="people-board">
          <h2>{actorTotals.length ? "Your people" : "Be the first one in."}</h2>
          {actorTotals.map(([name, amount], index) => <div key={name}><b>{index + 1}</b><strong>{name}</strong><span>{amount}</span></div>)}
        </section>
      )}

      <section className="mini-bottom">
        <h2>Want your own?</h2>
        <Link href={`/?remix=${app.id}&prompt=${encodeURIComponent(app.prompt)}`}>Make it yours ↗</Link>
      </section>
    </main>
  );
}
