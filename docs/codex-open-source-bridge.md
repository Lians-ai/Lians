# Bridging open-source Lians and Codex

The product has one architecture with two user surfaces:

| Layer | User value | Current surface |
|---|---|---|
| Lians Guard | Durable, inspectable state and evidence | Open-source MCP, plugin, SDK, and repository tools |
| Codex | Model execution, tools, sandboxes, and native task history | Codex app, CLI, and IDE |
| Mission Control | Mission contract, model policy, verifier, and comparable receipt | Local web application and CLI |

The previous missing join was execution continuity. Mission Control launched a
fresh `codex exec` process for each stage even though the open-source Lians
layer was designed to carry state across work. Version 0.11.0 uses one Codex
app-server thread for one mission when the installed Codex CLI supports it.
Stage-level model choice and safety boundaries remain explicit, while Codex
retains its own thread, authentication, repository instructions, skills,
plugins, and MCP configuration.

This does not merge the projects or hide their boundaries. The open-source
layer remains useful without Mission Control. Mission Control remains usable
without the Lians plugin. Installing both gives the intended loop: state and
corrections stay available inside Codex, while Mission Control decides how much
capability to spend and whether the final work earned a PASS receipt.

The app-server surface is currently experimental, so the product retains the
one-shot runner for compatibility and labels the persistent bridge beta. The
next proof is external behavior, not more architecture: five outside users,
twenty matched Protect-versus-Maximum task pairs, later-day reuse, and one
willingness-to-pay result.

See the [implementation and authority boundary](../packages/lians-finish/CODEX-BRIDGE.md)
and [proof-gate protocol](../packages/lians-finish/BETA-PROOF-GATE.md).
