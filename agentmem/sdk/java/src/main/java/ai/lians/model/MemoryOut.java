package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

/**
 * A single memory returned by recall, snapshot, or fact-history.
 *
 * <p>{@code content} is {@code null} when the memory was crypto-shredded
 * (GDPR/HIPAA erasure) — its existence and metadata survive, the content does not.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class MemoryOut {

    /** Stable memory identifier. */
    @JsonProperty("id")
    public String id;

    /** Namespace that owns the memory. */
    @JsonProperty("namespace")
    public String namespace;

    /** Agent identifier associated with the memory. */
    @JsonProperty("agent_id")
    public String agentId;

    /** Plaintext memory content, or {@code null} after erasure. */
    @JsonProperty("content")
    public String content;

    /** Optional data-subject identifier used for governed erasure. */
    @JsonProperty("subject_id")
    public String subjectId;

    /** Business event time in ISO-8601 format. */
    @JsonProperty("event_time")
    public String eventTime;

    /** Start of the memory's system-time validity interval. */
    @JsonProperty("valid_from")
    public String validFrom;

    /** End of the validity interval, or {@code null} while currently valid. */
    @JsonProperty("valid_to")
    public String validTo;

    /** Identifier of the memory that superseded this one, when present. */
    @JsonProperty("superseded_by")
    public String supersededBy;

    /** Caller-supplied importance score. */
    @JsonProperty("importance")
    public double importance;

    /** Optional provenance label for the memory. */
    @JsonProperty("source")
    public String source;

    /** Tamper-evidence hash of the stored content. */
    @JsonProperty("content_hash")
    public String contentHash;

    /** Erasure timestamp, or {@code null} when the content remains available. */
    @JsonProperty("erased_at")
    public String erasedAt;

    /** Structured metadata associated with the memory. */
    @JsonProperty("metadata")
    public JsonNode metadata;

    /**
     * Returns a concise diagnostic representation of this memory.
     *
     * @return a string containing the identifier, event time, and content state
     */
    @Override
    public String toString() {
        return "MemoryOut{id=" + id + ", eventTime=" + eventTime
                + ", content=" + (content == null ? "<erased>" : content) + "}";
    }
}
