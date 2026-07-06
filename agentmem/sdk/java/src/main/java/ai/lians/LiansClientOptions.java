package ai.lians;

import java.time.Duration;
import java.util.Objects;

/**
 * Connection options for {@link LiansClient}.
 *
 * <pre>{@code
 * LiansClient client = new LiansClient(
 *     LiansClientOptions.builder()
 *         .baseUrl("https://mem.yourfirm.internal")
 *         .apiKey(System.getenv("LIANS_API_KEY"))
 *         .adminSecret(System.getenv("LIANS_ADMIN_SECRET"))  // optional
 *         .build());
 * }</pre>
 */
public final class LiansClientOptions {

    private final String baseUrl;
    private final String apiKey;
    private final String adminSecret;
    private final Duration timeout;

    private LiansClientOptions(Builder b) {
        this.baseUrl = Objects.requireNonNull(b.baseUrl, "baseUrl is required");
        this.apiKey = Objects.requireNonNull(b.apiKey, "apiKey is required");
        this.adminSecret = b.adminSecret;
        this.timeout = b.timeout != null ? b.timeout : Duration.ofSeconds(30);
    }

    public String baseUrl()     { return baseUrl; }
    public String apiKey()      { return apiKey; }
    public String adminSecret() { return adminSecret; }
    public Duration timeout()   { return timeout; }

    public static Builder builder() {
        return new Builder();
    }

    public static final class Builder {
        private String baseUrl;
        private String apiKey;
        private String adminSecret;
        private Duration timeout;

        /** Base URL of the Lians server, e.g. {@code https://api.lians.dev}. */
        public Builder baseUrl(String baseUrl) { this.baseUrl = baseUrl; return this; }

        /** API key with the scopes your calls require (read/write/admin). */
        public Builder apiKey(String apiKey) { this.apiKey = apiKey; return this; }

        /** Admin secret, required only for {@code /v1/admin/*} audit endpoints. */
        public Builder adminSecret(String adminSecret) { this.adminSecret = adminSecret; return this; }

        /** Per-request timeout (default 30s). */
        public Builder timeout(Duration timeout) { this.timeout = timeout; return this; }

        public LiansClientOptions build() {
            return new LiansClientOptions(this);
        }
    }
}
