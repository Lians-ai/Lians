# Distribution log, July 17, 2026

This ledger records zero-cost distribution work for Lians. Show HN is excluded.
Statuses reflect verified public state, not planned activity.

## Canonical assets

| Asset | Status | Evidence |
|---|---|---|
| Package discovery metadata and local MCP bundle | Merged | [Lians PR 24](https://github.com/Lians-ai/Lians/pull/24) |
| Package-page refresh | Merged | [Lians PR 31](https://github.com/Lians-ai/Lians/pull/31) passed all 12 CI jobs and removed stale scorecards and unsupported absolute claims |
| Unified SDK release | Published | [Lians v0.4.1](https://github.com/Lians-ai/Lians/releases/tag/v0.4.1) includes Java and C artifacts plus the Go module tag |
| Local-first MCP and benchmark publication | Merged | [Lians PR 23](https://github.com/Lians-ai/Lians/pull/23) |
| Regulated-memory preprint | Draft with public review request | [Draft PR 26](https://github.com/Lians-ai/Lians/pull/26) includes a validated Lians 0.4.1 evidence artifact; [issue 40](https://github.com/Lians-ai/Lians/issues/40) requests implementation-neutral methodology review |
| Website Article 12 and canonical marketing content | Merged to redesign branch | [Website PR 1](https://github.com/Ds6826/lian-website/pull/1) |
| Website production promotion | Draft review required | [Website PR 2](https://github.com/Ds6826/lian-website/pull/2) |

## Directories and curated lists

| Channel | Status | Evidence or next action |
|---|---|---|
| Official MCP Registry | Version 0.4.1 published | Registry name `io.github.ebeirne/lians`; the publisher accepted 0.4.1 and rejected a verification retry as a duplicate version |
| RoninForge State of MCP census | Indexed with a stale degraded result | The July 2 snapshot checked the retired `ebeirne/Lians` URL; the refreshed official registry manifest points to `Lians-ai/Lians` for the next census |
| mcp.so | Queued for review | [Lians listing](https://mcp.so/servers/lians-b81d5f) |
| MCPServers.org | Submitted on free tier | Review promised within 12 hours; notification routed to `support@lians.ai` |
| MCP.Directory | Submitted for review | Submission confirmed; publication review promised within 24 hours |
| MCP Server Hub | Submitted, listing not yet verified | Free form accepted and reset without a public confirmation ID |
| AgentNDX | Submitted for curated review | Public form confirmed receipt and stated a 48-hour review target |
| DeepYard | Submitted for review | Formspree confirmation page verified successful submission in the MCP Servers category |
| MCP Market | Submitted to free queue | Public form confirmed the $0 queue; estimated listing time is four to six weeks and no paid placement was selected |
| AI Tools Directory | Submission failed on public form | Completed form returned `Failed to process submission`; no listing claimed |
| ServerHub | Submission blocked by directory error | Public form returned `Cross-origin requests are not allowed` while fetching the GitHub repository |
| Smithery | Server record created, deployment blocked by registry API | [Server page](https://smithery.ai/servers/info-2zyf/lians-agent-memory), [CLI issue 797](https://github.com/smithery-ai/cli/issues/797) |
| PulseMCP | Awaiting manual ingestion request | Gmail draft prepared for `hello@pulsemcp.com` |
| Glama | Manual account and CAPTCHA required | Free Add Server flow opened; no payment authorized |
| mcpub | Not eligible for current transport | Directory requires a hosted HTTP MCP endpoint and a `/.well-known/mcp.json` resource; Lians currently publishes a local stdio server |
| Unyly | Declined | Submission requires accepting creator marketplace terms and is oriented around hosted billing and revenue share, which is outside the zero-cost local-server campaign |
| awesome-mcp-servers | Open PR, Glama check required | [PR 10320](https://github.com/punkpeye/awesome-mcp-servers/pull/10320) |
| Awesome-Agent-Memory | Open PR | [PR 61](https://github.com/TeleAI-UAGI/Awesome-Agent-Memory/pull/61) |
| awesome-ai-memory | Open PR | [PR 62](https://github.com/topoteretes/awesome-ai-memory/pull/62) |
| Awesome-AI-Memory | Open bilingual PR | [PR 124](https://github.com/IAAR-Shanghai/Awesome-AI-Memory/pull/124) |
| Awesome Agents | Open PR | [PR 649](https://github.com/kyrolabs/awesome-agents/pull/649) adds one factual entry at the bottom of the Frameworks section |
| cxxz Awesome Agent Memory | Open PR | [PR 17](https://github.com/cxxz/awesome-agent-memory/pull/17) adds a maintainer-disclosed Lians entry to the MCP memory section |
| TensorBlock Awesome MCP Servers | Open PR | [PR 1261](https://github.com/TensorBlock/awesome-mcp-servers/pull/1261) adds a tested, maintainer-disclosed entry to the Knowledge Management & Memory category |

## Framework documentation

| Framework | Status | Evidence or blocker |
|---|---|---|
| LangChain and LangGraph | Ready for review, reviewer tagged once | [PR 4949](https://github.com/langchain-ai/docs/pull/4949) |
| CrewAI | Draft, maintainer-only AI label required | [PR 6584](https://github.com/crewAIInc/crewAI/pull/6584) |
| AutoGen and Microsoft Agent Framework | Successor sample proposed | AutoGen is in maintenance mode; [Agent Framework issue 7168](https://github.com/microsoft/agent-framework/issues/7168) proposes a local bitemporal-memory MCP sample |
| OpenAI Agents SDK | No external listing submitted | Current official docs favor first-party session and memory examples rather than a vendor directory |

## Package registries

| Registry | Status | Evidence or blocker |
|---|---|---|
| PyPI | Version 0.4.1 verified live | [lians-sdk](https://pypi.org/project/lians-sdk/) exposes the new homepage, summary, README, benchmarks, and documentation links |
| Maven Central | Version 0.4.1 verified live | [Canonical POM](https://repo1.maven.org/maven2/ai/lians/lians-sdk/0.4.1/lians-sdk-0.4.1.pom) and [Sonatype artifact page](https://central.sonatype.com/artifact/ai.lians/lians-sdk/0.4.1) both returned HTTP 200 |
| npm | Version 0.4.1 publication blocked by token authorization | [Failed publish workflow](https://github.com/Lians-ai/Lians/actions/runs/29610671394) received an npm registry 404 while 0.4.0 remains public; replace the scoped package token before rerunning |

## Vendor right of reply

The detailed templates, public URLs, and response statuses are maintained in
[public-right-of-reply-2026-07-17.md](public-right-of-reply-2026-07-17.md).

## Editorial outreach

| Target | Status | Angle |
|---|---|---|
| VeritasChain | Gmail draft prepared | Execution audit trails plus bitemporal agent knowledge provenance |
| Agent-memory roundup publishers | Research in progress | Reproducible regulated-memory evaluation and lookahead-bias demo |

## Operating constraints

- No paid placements, sponsorships, subscriptions, or promoted listings.
- No secrets or API keys sent through public forms or issues.
- No Show HN submission.
- No em dashes in newly published Lians copy.
