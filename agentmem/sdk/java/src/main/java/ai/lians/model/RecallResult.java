package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Collections;
import java.util.List;

/** Result of a recall: the current (non-stale) memories relevant to the query. */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class RecallResult {

    /** Memories selected for the recall, ordered by relevance. */
    @JsonProperty("memories")
    public List<MemoryOut> memories = Collections.emptyList();

    /** Point-in-time checkpoint when the recall used {@code as_of}; otherwise null. */
    @JsonProperty("as_of")
    public String asOf;

    /** Total number of candidate memories considered before result limiting. */
    @JsonProperty("total_candidates")
    public int totalCandidates;
}
