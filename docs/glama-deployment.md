# Glama deployment

Lians includes a dedicated container recipe for Glama's MCP build and
introspection pipeline.

## Build specification

- Dockerfile: `Dockerfile.glama`
- Command: use the image entrypoint without additional arguments
- Transport: stdio
- Persistent volume: `/data`
- API key: not required for local SQLite mode

The image runs `lians-sdk[mcp]==0.4.1` as a non-root user. Its default database
path is `/data/mcp.db`.

## Local build

```bash
docker build --file Dockerfile.glama --tag lians-mcp-glama .
docker run --rm -i --volume lians-data:/data lians-mcp-glama
```

An MCP client must communicate with the container over standard input and
standard output. The process is expected to remain open while the client session
is active.

## Automated verification

Repository CI builds the same image, starts it through the official Python MCP
client, performs the initialization handshake, calls `tools/list`, and verifies
that all eight expected Lians tools are present.

The image does not declare a hosted endpoint. Glama can wrap the stdio process
with its own gateway when deploying the server.
