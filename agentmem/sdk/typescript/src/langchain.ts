/**
 * LangChain.js integration for AgentMem.
 *
 * Install the optional peer dep first:
 *   npm install @langchain/core
 *
 * @example
 * import { AgentMemClient } from "agentmem-sdk";
 * import { createRecallTool, createRememberTool } from "agentmem-sdk/langchain";
 *
 * const client = new AgentMemClient({ baseUrl: "...", apiKey: "..." });
 * const tools  = [createRecallTool(client, "equity-desk"), createRememberTool(client, "equity-desk")];
 */

import type { AgentMemClient } from "./client.js";

type DynamicTool = { name: string; description: string; func: (input: string) => Promise<string> };

async function getDynamicTool(): Promise<new (fields: DynamicTool) => DynamicTool> {
  try {
    const mod = await import("@langchain/core/tools");
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (mod as any).DynamicTool;
  } catch {
    throw new Error(
      "agentmem-sdk: @langchain/core is not installed. " +
      "Run `npm install @langchain/core` to use LangChain integration.",
    );
  }
}

/**
 * Returns a LangChain DynamicTool that recalls memories by semantic search.
 * The tool accepts a plain-text query string and returns formatted results.
 */
export async function createRecallTool(
  client: AgentMemClient,
  agentId: string,
  opts: { k?: number } = {},
): Promise<DynamicTool> {
  const DynamicTool = await getDynamicTool();
  return new DynamicTool({
    name: "recall_memory",
    description:
      "Retrieve relevant memories for a query. Input: a natural-language question. " +
      "Returns current valid facts — superseded facts are excluded automatically.",
    func: async (query: string) => {
      const result = await client.recall({ agent_id: agentId, query, k: opts.k ?? 5 });
      if (!result.memories.length) return "No relevant memories found.";
      return result.memories
        .map((m) => `[${(m.event_time ?? "").slice(0, 10)}] ${m.content ?? "[erased]"}`)
        .join("\n");
    },
  });
}

/**
 * Returns a LangChain DynamicTool that stores a memory.
 * The tool accepts a plain-text string describing the fact to remember.
 */
export async function createRememberTool(
  client: AgentMemClient,
  agentId: string,
): Promise<DynamicTool> {
  const DynamicTool = await getDynamicTool();
  return new DynamicTool({
    name: "remember",
    description:
      "Store a fact or observation in persistent memory. " +
      "Input: the text to remember. Uses the current timestamp as event_time.",
    func: async (content: string) => {
      await client.addMemory({
        agent_id: agentId,
        content,
        event_time: new Date().toISOString(),
        source: "langchain_tool",
      });
      return `Stored: ${content.slice(0, 120)}`;
    },
  });
}
