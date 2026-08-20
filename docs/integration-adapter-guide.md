# Integration adapter guide

Maintained adapters are part of Lians' distribution and trust surface. A listed
integration should prove the smallest useful loop before adding advanced options:

1. connect the client without collecting its account password;
2. save one synthetic project decision;
3. recall it in a visibly fresh task;
4. correct the decision and keep the stale value out of current recall;
5. confirm deletion of the synthetic record; and
6. document the exact uninstall or disconnect path.

## Adapter acceptance checklist

- Official client and extension documentation is linked.
- The configuration change is minimal and reviewable.
- Project identity is stable and documented.
- Local mode works without a Lians account or provider API key.
- Secrets and confidential project content are not used in fixtures.
- A fresh-session test is automated where the host exposes a trustworthy
  boundary; otherwise it is labeled as a manual acceptance test.
- Timeouts, missing executables, and permission denials fail clearly.
- Existing user configuration is preserved or backed up.
- Uninstall restores the prior configuration.
- The integration owner and last-tested client version are recorded.

Open an [integration request](https://github.com/Lians-ai/Lians/issues/new?template=integration_request.yml)
and state whether you can implement, maintain, or test the adapter. Compatibility
claims are removed or downgraded when a listed adapter is no longer maintained.
