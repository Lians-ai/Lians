package ai.lians;

/**
 * Thrown when the Lians server returns a non-2xx response, or when a request
 * cannot be completed (network/timeout/serialization error).
 */
public class LiansException extends RuntimeException {

    private static final long serialVersionUID = 1L;

    /** HTTP status code, or 0 when the failure was not an HTTP response. */
    private final int status;

    /** Raw response body (or error detail) returned by the server. */
    private final String body;

    public LiansException(int status, String body, String message) {
        super(message);
        this.status = status;
        this.body = body;
    }

    public LiansException(String message, Throwable cause) {
        super(message, cause);
        this.status = 0;
        this.body = "";
    }

    /** HTTP status code, or 0 when the failure was not an HTTP response. */
    public int status() {
        return status;
    }

    /** Raw response body (or error detail) returned by the server. */
    public String body() {
        return body;
    }
}
