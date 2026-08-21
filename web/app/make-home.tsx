"use client";

import { animate, stagger } from "animejs";
import gsap from "gsap";
import Lenis from "lenis";
import Image from "next/image";
import { useEffect, useMemo, useRef, useState } from "react";
import type { MiniApp } from "../lib/types";

const starters = [
  "Make a 30-day gym challenge for my friends",
  "Make a vote for our next group trip",
  "Make a birthday quiz for Maya",
];

const examples = [
  { title: "Read 20 pages", text: "12 friends · 84 check-ins", prompt: "Make a 30-day reading challenge for my friends" },
  { title: "Where are we going?", text: "8 votes · City break wins", prompt: "Make a vote for our next group trip" },
  { title: "How well do you know Maya?", text: "43 plays · 3 questions", prompt: "Make a birthday quiz for Maya" },
];

const buildWords = ["Reading your idea", "Picking the right shape", "Making it work", "Putting it online"];

export function MakeHome() {
  const [prompt, setPrompt] = useState(() => {
    if (typeof window === "undefined") return "";
    return new URLSearchParams(window.location.search).get("prompt")?.slice(0, 280) ?? "";
  });
  const [building, setBuilding] = useState(false);
  const [buildStep, setBuildStep] = useState(0);
  const [error, setError] = useState("");
  const [recent, setRecent] = useState<MiniApp[]>([]);
  const promptRef = useRef<HTMLTextAreaElement>(null);

  const remixOf = useMemo(() => {
    if (typeof window === "undefined") return null;
    return new URLSearchParams(window.location.search).get("remix");
  }, []);

  useEffect(() => {
    fetch("/api/apps")
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then((payload: { apps: MiniApp[] }) => setRecent(payload.apps))
      .catch(() => setRecent([]));

    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduceMotion) return;
    const lenis = new Lenis({ duration: 1.05, smoothWheel: true });
    let frame = 0;
    const raf = (time: number) => {
      lenis.raf(time);
      frame = requestAnimationFrame(raf);
    };
    frame = requestAnimationFrame(raf);
    gsap.fromTo("[data-intro]", { y: 32, opacity: 0 }, { y: 0, opacity: 1, duration: 0.75, stagger: 0.08, ease: "power3.out" });
    gsap.utils.toArray<HTMLElement>("[data-reveal]").forEach((element) => {
      gsap.fromTo(element, { y: 35, opacity: 0 }, { y: 0, opacity: 1, duration: 0.75, ease: "power3.out", delay: 0.1 });
    });
    return () => {
      cancelAnimationFrame(frame);
      lenis.destroy();
    };
  }, []);

  useEffect(() => {
    if (!building) return;
    const wordTimer = window.setInterval(() => setBuildStep((step) => (step + 1) % buildWords.length), 650);
    const dots = animate(".build-dot", {
      scale: [0.6, 1.25, 0.6],
      opacity: [0.35, 1, 0.35],
      delay: stagger(110),
      duration: 780,
      loop: true,
      ease: "inOutQuad",
    });
    return () => {
      window.clearInterval(wordTimer);
      dots.pause();
    };
  }, [building]);

  function chooseStarter(value: string) {
    setPrompt(value);
    setError("");
    document.getElementById("make")?.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(() => promptRef.current?.focus(), 350);
  }

  async function make() {
    const clean = prompt.trim();
    if (clean.length < 5) {
      setError("Tell us what you want to make.");
      promptRef.current?.focus();
      return;
    }
    setBuilding(true);
    setBuildStep(0);
    setError("");
    try {
      const response = await fetch("/api/apps", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: clean, remixOf }),
      });
      const payload = await response.json() as { app?: MiniApp; error?: string };
      if (!response.ok || !payload.app) throw new Error(payload.error || "We could not make that yet.");
      window.setTimeout(() => window.location.assign(`/m/${payload.app?.id}`), 400);
    } catch (caught) {
      setBuilding(false);
      setError(caught instanceof Error ? caught.message : "We could not make that yet.");
    }
  }

  function handleKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void make();
  }

  return (
    <main className="site-shell">
      <nav className="nav-shell" aria-label="Main navigation">
        <a className="brand-mark" href="#top" aria-label="Lians home"><Image src="/lians-lotus.svg" width={46} height={28} alt="" /><span>lians</span></a>
        <div className="nav-actions"><a href="#examples">Examples</a><a className="nav-button" href="#make">Make</a></div>
      </nav>

      <section className="hero" id="top">
        <div className="hero-copy">
          <h1 data-intro>What should we <em>make?</em></h1>
          <p className="hero-subhead" data-intro>Describe what your group needs. Get a working app and one link to share.</p>
        </div>

        <div className="maker-shell" id="make" data-intro>
          <label htmlFor="make-prompt" className="visually-hidden">Describe what to make</label>
          <textarea
            id="make-prompt"
            ref={promptRef}
            value={prompt}
            onChange={(event) => setPrompt(event.target.value.slice(0, 280))}
            onKeyDown={handleKeyDown}
            placeholder="Make a 30-day reading challenge for my friends"
            rows={3}
            disabled={building}
          />
          <div className="maker-actions">
            <p>{prompt.length}/280</p>
            <button type="button" onClick={() => void make()} disabled={building}><span>{building ? "Making" : "Make"}</span><b aria-hidden="true">↑</b></button>
          </div>
          {building && (
            <div className="build-state" role="status" aria-live="polite">
              <div><i className="build-dot" /><i className="build-dot" /><i className="build-dot" /></div>
              <strong>{buildWords[buildStep]}</strong>
            </div>
          )}
          {error && <p className="maker-error" role="alert">{error}</p>}
        </div>

        <div className="starter-row" aria-label="Example ideas" data-intro>
          {starters.map((starter) => (
            <button type="button" key={starter} onClick={() => chooseStarter(starter)}>{starter.replace(/^Make /, "")}</button>
          ))}
        </div>
        <p className="hero-note" data-intro>Challenges · votes · quizzes · trackers · lists · leaderboards</p>
      </section>

      <section className="examples-section" id="examples">
        <div className="section-title" data-reveal>
          <h2>Make the thing<br />people <em>use.</em></h2>
          <p>No instructions. No code. No setup page. Your people open the link and start.</p>
        </div>
        <div className="example-grid">
          {examples.map((example) => (
            <button className="example-card" key={example.title} onClick={() => chooseStarter(example.prompt)} data-reveal>
              <strong>{example.title}</strong><p>{example.text}</p><i>Make this ↗</i>
            </button>
          ))}
        </div>
      </section>

      <section className="simple-section">
        <div className="simple-copy" data-reveal>
          <h2>One sentence.<br />One link.<br /><em>Done.</em></h2>
          <p>Lians makes the shared place where the challenge, decision, score, or list actually happens.</p>
        </div>
        <div className="steps" data-reveal>
          <article><strong>01</strong><h3>Say it</h3><p>Describe the result your group needs.</p></article>
          <article><strong>02</strong><h3>Get it</h3><p>Lians makes the working app and puts it online.</p></article>
          <article><strong>03</strong><h3>Share it</h3><p>Your people join from one link. No accounts.</p></article>
        </div>
      </section>

      {recent.length > 0 && (
        <section className="recent-section">
          <h2 data-reveal>Made recently.</h2>
          <div className="recent-grid">
            {recent.slice(0, 6).map((app) => (
              <a href={`/m/${app.id}`} key={app.id}><strong>{app.title}</strong><i>Open ↗</i></a>
            ))}
          </div>
        </section>
      )}

      <section className="final-section">
        <h2 data-reveal>Make one.</h2>
        <button type="button" onClick={() => { document.getElementById("make")?.scrollIntoView(); promptRef.current?.focus(); }}>Start with a sentence <span>↑</span></button>
      </section>

      <footer><a className="brand-mark" href="#top"><Image src="/lians-lotus.svg" width={46} height={28} alt="" /><span>lians</span></a><p>Useful software for everyone.</p><span>Made in 2026</span></footer>
    </main>
  );
}
