import type { AppConfig, AppKind, MiniApp } from "./types";

const palette = ["#765786", "#1f1723"];

const kindDetails: Record<AppKind, { emoji: string; description: string; unit: string; target: number }> = {
  challenge: { emoji: "✦", description: "Show up together. One check-in at a time.", unit: "check-ins", target: 30 },
  vote: { emoji: "↑", description: "Everyone gets a say. One answer wins.", unit: "votes", target: 1 },
  quiz: { emoji: "?", description: "Answer fast. Compare the result with your people.", unit: "plays", target: 1 },
  leaderboard: { emoji: "1", description: "Add a score. Move the whole board.", unit: "points", target: 100 },
  tracker: { emoji: "+", description: "Keep one shared number moving in the right direction.", unit: "done", target: 100 },
  list: { emoji: "✓", description: "One list that everyone can add to and finish.", unit: "items", target: 10 },
};

function detectKind(value: string): AppKind {
  const prompt = value.toLowerCase();
  if (/quiz|trivia|test|questions|how well/.test(prompt)) return "quiz";
  if (/list|grocer|packing|tasks|checklist|wishlist/.test(prompt)) return "list";
  if (/leaderboard|score|tournament|competition|rank/.test(prompt)) return "leaderboard";
  if (/vote|poll|choose|decide|pick|where should|trip/.test(prompt)) return "vote";
  if (/track|log|count|budget|savings|habit/.test(prompt)) return "tracker";
  return "challenge";
}

function titleFromPrompt(prompt: string, kind: AppKind) {
  let title = prompt
    .replace(/^(please\s+)?(make|create|build|give me|i need|i want)\s+(me\s+)?/i, "")
    .replace(/\s+for\s+(my|our)\s+(friends|family|group|class|team).*$/i, "")
    .replace(/[.!?]+$/, "")
    .trim();
  if (!title) title = kind === "challenge" ? "Our new challenge" : `Our ${kind}`;
  title = title.charAt(0).toUpperCase() + title.slice(1);
  return title.slice(0, 72);
}

function voteOptions(prompt: string) {
  const between = prompt.match(/between\s+(.+?)\s+and\s+(.+?)(?:[.!?]|$)/i);
  if (between) return [between[1].trim(), between[2].trim()];
  const lower = prompt.toLowerCase();
  if (/trip|vacation|travel/.test(lower)) return ["Beach weekend", "City break", "Cabin escape"];
  if (/food|dinner|restaurant|eat/.test(lower)) return ["Italian", "Sushi", "Tacos"];
  if (/movie|film/.test(lower)) return ["Something funny", "A thriller", "A classic"];
  return ["Yes, do it", "Maybe later", "Try something else"];
}

function quizQuestions(subject: string) {
  const short = subject.replace(/\b(a|an|the|quiz)\b/gi, "").trim().slice(0, 30) || "the group";
  return [
    { question: `What is the energy for ${short}?`, options: ["All in", "Easy does it", "Pure chaos"] },
    { question: "Who makes the first move?", options: ["Me", "The planner", "Whoever wakes up"] },
    { question: "Pick the ending.", options: ["A great story", "A new tradition", "One more round"] },
  ];
}

export function generateMiniApp(prompt: string, remixOf: string | null = null): MiniApp {
  const kind = detectKind(prompt);
  const detail = kindDetails[kind];
  const [accent, accentSoft] = palette;
  const title = titleFromPrompt(prompt, kind);
  const config: AppConfig = {
    accent,
    accentSoft,
    emoji: detail.emoji,
    options: kind === "vote" ? voteOptions(prompt) : [],
    unit: detail.unit,
    target: detail.target,
    questions: kind === "quiz" ? quizQuestions(title) : [],
  };

  return {
    id: crypto.randomUUID().replaceAll("-", "").slice(0, 10),
    kind,
    title,
    description: detail.description,
    prompt,
    config,
    remixOf,
    createdAt: Date.now(),
  };
}
