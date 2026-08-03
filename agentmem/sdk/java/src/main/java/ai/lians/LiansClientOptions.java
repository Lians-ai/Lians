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
    private final int maxRetries;
    private final Duration maxRetryDelay;
    private final int maxResponseBytes;

    private LiansClientOptions(Builder b) {
        this.baseUrl = Objects.requireNonNull(b.baseUrl, "baseUrl is required");
        this.apiKey = Objects.requireNonNull(b.apiKey, "apiKey is required");
        this.adminSecret = b.adminSecret;
        this.timeout = b.timeout != null ? b.timeout : Duration.ofSeconds(30);
        this.maxRetries = b.maxRetries != null ? b.maxRetries : 2;
        this.maxRetryDelay = b.maxRetryDelay != null ? b.maxRetryDelay : Duration.ofSeconds(2);
        this.maxResponseBytes = b.maxResponseBytes != null ? b.maxResponseBytes : 16 * 1024 * 1024;
        if (timeout.isZero() || timeout.isNegative() || timeout.compareTo(Duration.ofMinutes(10)) > 0) {
            throw new IllegalArgumentException("timeout must be greater than zero and at most 10 minutes");
        }
        if (maxRetries < 0 || maxRetries > 5) {
            throw new IllegalArgumentException("maxRetries must be between 0 and 5");
        }
        if (maxRetryDelay.isZero() || maxRetryDelay.isNegative()
                || maxRetryDelay.compareTo(Duration.ofSeconds(30)) > 0) {
            throw new IllegalArgumentException("maxRetryDelay must be greater than zero and at most 30 seconds");
        }
        if (maxResponseBytes < 1024 || maxResponseBytes > 256 * 1024 * 1024) {
            throw new IllegalArgumentException("maxResponseBytes must be between 1 KiB and 256 MiB");
        }
    }

    /**
     * Returns the base URL of the Lians server.
     *
     * @return the configured server base URL
     */
    public String baseUrl()     { return baseUrl; }

    /**
     * Returns the API key sent with SDK requests.
     *
     * @return the configured API key
     */
    public String apiKey()      { return apiKey; }

    /**
     * Returns the optional secret used for administrative audit endpoints.
     *
     * @return the configured admin secret, or {@code null} when none was supplied
     */
    public String adminSecret() { return adminSecret; }

    /**
     * Returns the request timeout.
     *
     * @return the configured timeout
     */
    public Duration timeout()   { return timeout; }

    /**
     * Returns the maximum number of retry attempts after the initial request.
     *
     * @return the configured retry count
     */
    public int maxRetries()     { return maxRetries; }

    /**
     * Returns the maximum delay between retry attempts.
     *
     * @return the configured retry-delay cap
     */
    public Duration maxRetryDelay() { return maxRetryDelay; }

    /**
     * Returns the maximum accepted response-body size.
     *
     * @return the response size limit in bytes
     */
    public int maxResponseBytes() { return maxResponseBytes; }

    /**
     * Creates a builder for client options.
     *
     * @return a new options builder
     */
    public static Builder builder() {
        return new Builder();
    }

    /** Builder for immutable {@link LiansClientOptions} instances. */
    public static final class Builder {
        private String baseUrl;
        private String apiKey;
        private String adminSecret;
        private Duration timeout;
        private Integer maxRetries;
        private Duration maxRetryDelay;
        private Integer maxResponseBytes;

        /**
         * Sets the base URL of the Lians server, for example
         * {@code https://api.lians.dev}.
         *
         * @param baseUrl the absolute HTTP or HTTPS server URL
         * @return this builder
         */
        public Builder baseUrl(String baseUrl) { this.baseUrl = baseUrl; return this; }

        /**
         * Sets the API key with the scopes required by the client calls.
         *
         * @param apiKey the Lians API key
         * @return this builder
         */
        public Builder apiKey(String apiKey) { this.apiKey = apiKey; return this; }

        /**
         * Sets the secret required by {@code /v1/admin/*} audit endpoints.
         *
         * @param adminSecret the admin secret, or {@code null} when admin calls are not used
         * @return this builder
         */
        public Builder adminSecret(String adminSecret) { this.adminSecret = adminSecret; return this; }

        /**
         * Sets the per-request timeout, which defaults to 30 seconds.
         *
         * @param timeout a positive duration of at most 10 minutes
         * @return this builder
         */
        public Builder timeout(Duration timeout) { this.timeout = timeout; return this; }

        /**
         * Sets retries after the first attempt for safe reads and idempotent writes.
         *
         * @param maxRetries the retry count, from 0 through 5
         * @return this builder
         */
        public Builder maxRetries(int maxRetries) { this.maxRetries = maxRetries; return this; }

        /**
         * Sets the maximum backoff or server {@code Retry-After} delay.
         *
         * @param maxRetryDelay a positive duration of at most 30 seconds
         * @return this builder
         */
        public Builder maxRetryDelay(Duration maxRetryDelay) {
            this.maxRetryDelay = maxRetryDelay;
            return this;
        }

        /**
         * Sets the maximum response body retained in memory.
         *
         * @param maxResponseBytes the response limit in bytes, from 1 KiB through 256 MiB
         * @return this builder
         */
        public Builder maxResponseBytes(int maxResponseBytes) {
            this.maxResponseBytes = maxResponseBytes;
            return this;
        }

        /**
         * Validates the accumulated values and creates immutable client options.
         *
         * @return the configured client options
         */
        public LiansClientOptions build() {
            return new LiansClientOptions(this);
        }
    }
}
