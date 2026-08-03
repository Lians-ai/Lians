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

    /** Server request identifier, when present. */
    private final String requestId;

    /**
     * Creates an exception for an unsuccessful HTTP response.
     *
     * @param status the HTTP status code
     * @param body the response body or error detail
     * @param message the exception message
     */
    public LiansException(int status, String body, String message) {
		this(status, body, "", message);
	}

	/**
	 * Creates an exception for an unsuccessful HTTP response with a request identifier.
	 *
	 * @param status the HTTP status code
	 * @param body the response body or error detail
	 * @param requestId the server request identifier
	 * @param message the exception message
	 */
	public LiansException(int status, String body, String requestId, String message) {
        super(message);
        this.status = status;
        this.body = body;
		this.requestId = requestId;
    }

    /**
     * Creates an exception for a transport, timeout, or serialization failure.
     *
     * @param message the exception message
     * @param cause the underlying failure
     */
    public LiansException(String message, Throwable cause) {
        super(message, cause);
        this.status = 0;
        this.body = "";
		this.requestId = "";
    }

    /**
     * Returns the HTTP status code.
     *
     * @return the status code, or 0 when the failure was not an HTTP response
     */
    public int status() {
        return status;
    }

    /**
     * Returns the raw response body or error detail supplied by the server.
     *
     * @return the response body or error detail
     */
    public String body() {
        return body;
    }

	/**
	 * Returns the server request identifier.
	 *
	 * @return the request identifier, or an empty string when it was not supplied
	 */
	public String requestId() {
		return requestId;
	}
}
