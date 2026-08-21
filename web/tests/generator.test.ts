import assert from "node:assert/strict";
import test from "node:test";
import { generateMiniApp } from "../lib/generator.ts";

const examples = [
  ["challenge", "Make a 30-day reading challenge for my friends"],
  ["vote", "Make a vote for our next group trip"],
  ["quiz", "Make a birthday quiz for Maya"],
  ["leaderboard", "Make a leaderboard for our game night"],
  ["tracker", "Make a habit tracker for our water goal"],
  ["list", "Make a shared packing list for our trip"],
] as const;

for (const [expected, prompt] of examples) {
  test(`makes a ${expected}`, () => {
    const app = generateMiniApp(prompt);
    assert.equal(app.kind, expected);
    assert.equal(app.config.accent, "#765786");
    assert.equal(app.config.accentSoft, "#1f1723");
    assert.match(app.id, /^[a-f0-9]{10}$/);
  });
}

test("turns the prompt into a concise title", () => {
  const app = generateMiniApp("Please make me a 30-day reading challenge for my friends.");
  assert.equal(app.title, "A 30-day reading challenge");
});

test("keeps titles within the product limit", () => {
  const app = generateMiniApp(`Make ${"a very long idea ".repeat(12)}`);
  assert.ok(app.title.length <= 72);
});

test("extracts explicit vote options", () => {
  const app = generateMiniApp("Make a vote between pasta and tacos");
  assert.deepEqual(app.config.options, ["pasta", "tacos"]);
});

test("uses contextual vote options", () => {
  const app = generateMiniApp("Make a movie poll for Friday");
  assert.deepEqual(app.config.options, ["Something funny", "A thriller", "A classic"]);
});

test("builds a complete quiz", () => {
  const app = generateMiniApp("Make a birthday quiz for Maya");
  assert.equal(app.config.questions.length, 3);
  app.config.questions.forEach((question) => assert.equal(question.options.length, 3));
});

test("keeps the remix source", () => {
  const app = generateMiniApp("Make a shared packing list", "source-app");
  assert.equal(app.remixOf, "source-app");
});
