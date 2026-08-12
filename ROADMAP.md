# Lians community roadmap

Lians is building time-aware, verifiable memory for AI agents: memory that can
keep current context clean and reconstruct what was knowable when an action
happened.

This roadmap lists contribution areas, not contractual release dates. Specific
work is tracked in public issues and pull requests.

## Now

- Make the local Python and MCP paths reliable on every supported platform.
- Turn temporal failure modes into small, reproducible demos and CI gates.
- Improve memory receipts so developers can see exactly which authorized facts
  shaped an answer.
- Keep documentation, package versions, and runnable examples aligned.

## Next

- Add inspect, correct, and forget workflows across local and hosted clients.
- Expand examples for LangGraph, CrewAI, OpenAI Agents SDK, AutoGen, and MCP
  hosts.
- Publish more adversarial temporal-recall fixtures beyond finance.
- Make it easier to compare current recall with point-in-time recall in one
  developer workflow.

## Later

- Broaden decision reconstruction and blast-radius analysis across providers.
- Add more customer-operated deployment and air-gap reference architectures.
- Grow external benchmark coverage and independent reproductions.

## Good ways to contribute

- Run the [lookahead-bias demo](demo/lookahead-bias) and report a different
  temporal failure.
- Test an integration in a real agent project.
- Improve a quickstart on Windows, macOS, or Linux.
- Add a safe, synthetic benchmark fixture from another domain.
- Review methodology and challenge a public claim with a reproduction.

Start with [the community guide](docs/community.md), browse issues labeled
[`good first issue`](https://github.com/Lians-ai/Lians/labels/good%20first%20issue),
or propose an idea in [Discussions](https://github.com/Lians-ai/Lians/discussions/categories/ideas).
