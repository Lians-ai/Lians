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

    @JsonProperty("id")              public String id;
    @JsonProperty("namespace")       public String namespace;
    @JsonProperty("agent_id")        public String agentId;
    @JsonProperty("content")         public String content;          // null if erased
    @JsonProperty("subject_id")      public String subjectId;
    @JsonProperty("event_time")      public String eventTime;        // ISO-8601
    @JsonProperty("valid_from")      public String validFrom;
    @JsonProperty("valid_to")        public String validTo;          // null = currently valid
    @JsonProperty("superseded_by")   public String supersededBy;
    @JsonProperty("importance")      public double importance;
    @JsonProperty("source")          public String source;
    @JsonProperty("content_hash")    public String contentHash;
    @JsonProperty("erased_at")       public String erasedAt;
    @JsonProperty("metadata")        public JsonNode metadata;

    @Override
    public String toString() {
        return "MemoryOut{id=" + id + ", eventTime=" + eventTime
                + ", content=" + (content == null ? "<erased>" : content) + "}";
    }
}
