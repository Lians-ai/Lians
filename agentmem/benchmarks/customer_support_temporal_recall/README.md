# Synthetic customer-support temporal recall

This benchmark checks three common support-memory failures with entirely
synthetic data:

- a ticket status changes and an older status arrives late;
- a return-policy window changes after a purchase;
- an incorrect customer name is corrected.

Every fixture record declares both event time (when the fact was true) and
ingestion time (when the support system learned it). Records are ingested in
the declared ingestion order. The runner checks current recall, historical
`recall_at`, exclusions, and Lians recall receipts.

From the repository root, with the local SDK installed:

```bash
python -m pip install -e "agentmem/sdk/python[local]"
python agentmem/benchmarks/customer_support_temporal_recall/run_benchmark.py
pytest agentmem/tests/test_customer_support_temporal_recall.py -q
```

No service, API key, model provider, or private customer data is used.
`expected_receipt.json` is the small reviewable contract; the runner prints an
actual receipt containing the matched content and each Lians receipt hash.
