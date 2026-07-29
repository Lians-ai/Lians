package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/** Result of a recall: the current (non-stale) memories relevant to the query. */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class RecallResult {

    @JsonProperty("memories")
    public List<MemoryOut> memories = Collections.emptyList();

    /** Point-in-time checkpoint when the recall used {@code as_of}; otherwise null. */
    @JsonProperty("as_of")
    public String asOf;

    @JsonProperty("total_candidates")
    public int totalCandidates;

    @JsonProperty("retrieval_degraded")
    public boolean retrievalDegraded;

    @JsonProperty("token_estimate")
    public int tokenEstimate;

    @JsonProperty("strategy")
    public String strategy;

    @JsonProperty("query_variants")
    public List<String> queryVariants = Collections.emptyList();

    @JsonProperty("retrieval_confidence")
    public double retrievalConfidence;

    @JsonProperty("latency_ms")
    public double latencyMs;

    @JsonProperty("mode")
    public String mode;

    @JsonProperty("latency_budget_ms")
    public double latencyBudgetMs;

    @JsonProperty("deadline_exceeded")
    public boolean deadlineExceeded;

    @JsonProperty("receipt_sha256")
    public String receiptSha256;

    @JsonProperty("receipt")
    public Map<String, Object> receipt = Collections.emptyMap();

    @JsonProperty("provenance_coverage")
    public double provenanceCoverage;
}
