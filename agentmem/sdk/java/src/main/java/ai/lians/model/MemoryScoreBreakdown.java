package ai.lians.model;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;
import java.util.Map;

/** Typed, auditable component scores and ranking provenance for a recall hit. */
@JsonIgnoreProperties(ignoreUnknown = true)
public final class MemoryScoreBreakdown {
    @JsonProperty("importance_score")       public double importanceScore;
    @JsonProperty("confidence_score")       public double confidenceScore;
    @JsonProperty("trust_score")            public double trustScore;
    @JsonProperty("freshness_score")        public double freshnessScore;
    @JsonProperty("relevance_score")        public double relevanceScore;
    @JsonProperty("stability_score")        public double stabilityScore;
    @JsonProperty("safety_score")           public double safetyScore;
    @JsonProperty("final_score")            public double finalScore;
    @JsonProperty("eligible")               public boolean eligible;
    @JsonProperty("safety_eligible")        public Boolean safetyEligible;
    @JsonProperty("temporal_eligible")      public Boolean temporalEligible;
    @JsonProperty("purpose")                public String purpose;
    @JsonProperty("scoring_policy_version") public String scoringPolicyVersion;
    @JsonProperty("reference_time")         public String referenceTime;
    @JsonProperty("scoring_limits")         public MemoryScoringLimits scoringLimits;
    @JsonProperty("weights")                public Map<String, Double> weights;
    @JsonProperty("reasons")                public List<String> reasons;
    @JsonProperty("quality_score")          public Double qualityScore;
    @JsonProperty("pre_fusion_score")       public Double preFusionScore;
    @JsonProperty("ranking_weights")        public Map<String, Double> rankingWeights;
    @JsonProperty("ranking_stages")         public List<MemoryRankingStage> rankingStages;
    @JsonProperty("fusion")                 public JsonNode fusion;
}
