package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

/** One deterministic ranking or learning adjustment in a recall explanation. */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class MemoryRankingStage {
    @JsonProperty("stage")            public String stage;
    @JsonProperty("input_score")      public double inputScore;
    @JsonProperty("output_score")     public double outputScore;
    @JsonProperty("method")           public String method;
    @JsonProperty("position")         public Integer position;
    @JsonProperty("candidate_count")  public Integer candidateCount;
}
