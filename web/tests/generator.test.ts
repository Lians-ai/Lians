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
    assert.equal(generateMiniApp(prompt).kind, expected);
  });
}
