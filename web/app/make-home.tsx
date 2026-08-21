"use client";

import { animate, stagger } from "animejs";
import gsap from "gsap";
import Lenis from "lenis";
import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { MiniApp } from "../lib/types";

const starters = [
  "Make a 30-day gym challenge for my friends",
  "Make a vote for our next group trip",
  "Make a birthday quiz for Maya",
];

const examples = [
  { title: "Read 20 pages", text: "12 friends · 84 check-ins", prompt: "Make a 30-day reading challenge for my friends", symbol: "30" },
  { title: "Where are we going?", text: "8 votes · City break wins", prompt: "Make a vote for our next group trip", symbol: "↑" },
  { title: "How well do you know Maya?", text: "43 plays · 3 questions", prompt: "Make a birthday quiz for Maya", symbol: "?" },
];

const appTypes = [
  { number: "01", name: "Challenge", text: "Turn a goal into something everyone can join.", prompt: starters[0] },
  { number: "02", name: "Vote", text: "Give the group one place to decide.", prompt: starters[1] },
  { number: "03", name: "Quiz", text: "Make a question worth sending around.", prompt: starters[2] },
  { number: "04", name: "Tracker", text: "Keep one shared number moving.", prompt: "Make a shared water tracker for my family" },
  { number: "05", name: "List", text: "Put every person and item in the same place.", prompt: "Make a packing list for our group trip" },
  { number: "06", name: "Leaderboard", text: "Turn a score into a reason to come back.", prompt: "Make a leaderboard for our game night" },
];

const buildWords = ["Reading your idea", "Picking the right shape", "Making it work", "Putting it online"];

export function MakeHome() {
  const [prompt, setPrompt] = useState("");
  const [remixOf, setRemixOf] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);
  const [buildStep, setBuildStep] = useState(0);
  const [error, setError] = useState("");
  const [recent, setRecent] = useState<MiniApp[]>([]);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const progressRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const params = new URLSearchParams(window.location.search);
      const sharedPrompt = params.get("prompt")?.slice(0, 280) ?? "";
      setPrompt((current) => current || sharedPrompt);
      setRemixOf(params.get("remix")?.slice(0, 40) ?? null);
    });
    return () => window.cancelAnimationFrame(frame);
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
      const scrollable = document.documentElement.scrollHeight - window.innerHeight;
      const progress = scrollable > 0 ? Math.min(window.scrollY / scrollable, 1) : 0;
      progressRef.current?.style.setProperty("--page-progress", String(progress));
      frame = requestAnimationFrame(raf);
    };
    frame = requestAnimationFrame(raf);
    gsap.fromTo("[data-intro]", { y: 30, opacity: 0 }, { y: 0, opacity: 1, duration: 1, stagger: 0.09, ease: "power3.out" });
    const revealElements = gsap.utils.toArray<HTMLElement>("[data-reveal]");
    gsap.set(revealElements, { y: 55, opacity: 0 });
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        gsap.to(entry.target, { y: 0, opacity: 1, duration: 1, ease: "power3.out" });
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.12 });
    revealElements.forEach((element) => observer.observe(element));
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
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
      <a className="skip-link" href="#make">Skip to maker</a>
      <nav className="nav-shell" aria-label="Main navigation">
        <a className="brand-mark" href="#top" aria-label="Lians home"><Image src="/lians-lotus.svg" width={46} height={28} alt="" priority /><span>lians</span></a>
        <div className="nav-actions"><a href="#types">What it makes</a><a className="nav-button" href="#make">Make</a></div>
        <span className="scroll-progress" ref={progressRef} aria-hidden="true" />
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

      <section className="artifact-section" id="examples">
        <div className="artifact-copy" data-reveal>
          <h2>The working thing.<br /><em>Not the answer.</em></h2>
          <p>Chat gives you words. Lians gives your people somewhere to go, do the thing, and come back.</p>
        </div>
        <div className="artifact-stage" data-reveal>
          <button className="floating-app floating-left" type="button" onClick={() => chooseStarter(examples[0].prompt)}>
            <span>{examples[0].symbol}</span><strong>{examples[0].title}</strong><small>{examples[0].text}</small>
          </button>
          <button className="floating-app floating-right" type="button" onClick={() => chooseStarter(examples[2].prompt)}>
            <span>{examples[2].symbol}</span><strong>{examples[2].title}</strong><small>{examples[2].text}</small>
          </button>
          <button className="center-app" type="button" onClick={() => chooseStarter(examples[1].prompt)}>
            <div className="center-app-top"><Image src="/lians-lotus.svg" width={36} height={22} alt="" /><span>lians</span><i>Live</i></div>
            <div className="center-app-copy"><small>8 people are here</small><strong>{examples[1].title}</strong></div>
            <div className="mock-votes"><span>Beach weekend <i>3</i></span><span className="is-winning">City break <i>5</i></span><span>Cabin escape <i>0</i></span></div>
          </button>
        </div>
      </section>

      <section className="outcome-section">
        <div className="outcome-noise" aria-hidden="true" />
        <div className="outcome-copy" data-reveal>
          <h2>A sentence becomes<br />somewhere people <em>go.</em></h2>
          <p>Not a plan for an app. Not code you need to finish. The finished, hosted place where the result happens.</p>
        </div>
        <div className="outcome-lines" data-reveal>
          <span>Join without an account</span><span>Use it together</span><span>Share one link</span>
        </div>
      </section>

      <section className="types-section" id="types">
        <h2 data-reveal>What can<br />you make?</h2>
        <div className="type-list">
          {appTypes.map((item) => (
            <button type="button" key={item.number} onClick={() => chooseStarter(item.prompt)} data-reveal>
              <small>{item.number}</small><strong>{item.name}</strong><span>{item.text}</span><i>↗</i>
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
        <div className="final-lotus" aria-hidden="true"><Image src="/lians-lotus.svg" width={540} height={320} alt="" /></div>
        <h2 data-reveal>Make it real.</h2>
        <button type="button" onClick={() => { document.getElementById("make")?.scrollIntoView(); promptRef.current?.focus(); }}>Start with a sentence <span>↑</span></button>
      </section>

      <footer><a className="brand-mark" href="#top"><Image src="/lians-lotus.svg" width={46} height={28} alt="" /><span>lians</span></a><p>Make useful things for your people.</p><span>2026</span></footer>
    </main>
  );
}
