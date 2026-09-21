# Verification record - v0.11.0 beta candidate

Date: 2026-09-21

## Passed locally

- 70/70 automated tests.
- 73% branch-aware coverage overall; 70% for `codex_app_server.py`.
- Ruff 0.16.3 lint and formatting.
- Python bytecode compilation.
- Browser JavaScript syntax and product-manifest JSON parsing.
- Real initialize handshake with the installed `codex app-server --stdio`.
- Wheel and source build for version 0.11.0.
- Isolated wheel import, packaged assets, loopback server, readiness, and
  security headers.
- PyInstaller 6.22.0 Windows build and frozen-app smoke test.
- Portable-agent round trip with no run-state mutation.
- Empty privacy-bounded beta export with no run-state mutation.
- Archive file list and executable/provenance hash agreement.
- Clean GitHub pull-request source, wheel, and Windows jobs, followed by an
  independent download check of the artifact checksum, clean source provenance,
  and GitHub build attestation.

## Local artifact hashes

These are development artifacts from a dirty pre-commit source tree. They are
recorded for reproducibility and must not be published as the beta release.

```text
6457E42172BE2E1B04E08BECC3D21068EF6F573A81607DFDC7F7B2CCB52458D1  Lians-0.11.0-Windows-x64.zip
74B8BCE8A558ABE49D8E96D96E5D54294F9BD57B422EFDA968A2B33473D04DFD  Lians.exe
B25A3EC0FC3CCF4C0A2817A3008708F9EC92D59C1A44AC57BA0E93FDD08B2D1A  lians_finish-0.11.0-py3-none-any.whl
E26FFAB3E64D42033DD57D4ECF2BF94107D3C5092BEFA35566E3906396A4CBEE  lians_finish-0.11.0.tar.gz
```

## Still required

- Human review, merge, and tagged prerelease publication.
- One full live multi-turn coding mission through app-server. The protocol
  handshake was real; model turns were not consumed merely to validate the
  adapter.
- The external proof gate in `BETA-PROOF-GATE.md`.
- Independent security review and Windows code signing before general release.
