package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/** Deterministic input and work limits used to calculate a memory score. */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class MemoryScoringLimits {
    @JsonProperty("text_sample_chars") public int textSampleChars;
    @JsonProperty("token_cap")         public int tokenCap;
    @JsonProperty("metadata_chars")    public int metadataChars;
    @JsonProperty("metadata_items")    public int metadataItems;
    @JsonProperty("metadata_depth")    public int metadataDepth;
    @JsonProperty("metadata_value_chars") public int metadataValueChars;
}
