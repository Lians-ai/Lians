"""CLI entrypoint for the isolated Gate mediator process."""

from __future__ import annotations

import argparse
import logging
import os
import ssl

import uvicorn

from .app import create_gate_mediator_app
from .config import MediatorConfigError, load_mediator_config


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lians-gate-mediator",
        description="Run the isolated Lians Gate enforcement mediator",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    serve = subcommands.add_parser("serve", help="start the mediator HTTP service")
    serve.add_argument(
        "--config",
        default=os.environ.get("LIANS_MEDIATOR_CONFIG"),
        help="absolute path to the mediator JSON configuration",
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info"),
        default="info",
    )
    return parser


def main() -> None:
    args = _parser().parse_args()
    if not args.config:
        raise SystemExit("--config or LIANS_MEDIATOR_CONFIG is required")
    if not 1 <= args.port <= 65_535:
        raise SystemExit("--port must be between 1 and 65535")
    try:
        config = load_mediator_config(args.config)
    except MediatorConfigError:
        raise SystemExit("mediator configuration is invalid or unreadable") from None

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_gate_mediator_app(config)
    server_config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        workers=1,
        access_log=False,
        server_header=False,
        proxy_headers=False,
        forwarded_allow_ips="",
        log_level=args.log_level,
        limit_concurrency=config.max_in_flight + 32,
        ssl_version=ssl.PROTOCOL_TLS_SERVER,
        ssl_certfile=config.server_tls.certificate_file,
        ssl_keyfile=config.server_tls.private_key_file,
        ssl_ca_certs=config.server_tls.client_ca_file,
        ssl_cert_reqs=(
            ssl.CERT_REQUIRED if config.server_tls.require_client_certificate else ssl.CERT_NONE
        ),
        ssl_ciphers="ECDHE+AESGCM:ECDHE+CHACHA20",
    )
    server_config.load()
    if not isinstance(server_config.ssl, ssl.SSLContext):
        raise SystemExit("mediator TLS configuration could not be loaded")
    server_config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    uvicorn.Server(server_config).run()


if __name__ == "__main__":
    main()
