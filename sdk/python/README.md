# Legacy Python client tree

This directory contains the older `lians` 0.2.0 thin-client package metadata.
It remains in the repository for compatibility and migration work, but it is
not the supported starting point for a new Lians integration.

For new work:

- install the current **`lians-sdk`** package;
- use the [`agentmem/sdk/python`](../../agentmem/sdk/python) source tree; and
- follow the [current install guide](../../docs/install.md).

The published package name is `lians-sdk`; its Python import namespace is still
`lians`. Do not use this legacy tree in new examples or installation commands.
