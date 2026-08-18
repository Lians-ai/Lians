"""Windowed entry point for the resident Lians desktop companion."""

from __future__ import annotations

import argparse

from lians_easy.diagnostics import install_crash_logging


def main() -> None:
    install_crash_logging(component="desktop-companion")
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    parser.add_argument("--intro-complete", action="store_true")
    args, _unknown = parser.parse_known_args()

    from lians_easy.lifecycle import listen_for_windows_installer_shutdown
    from lians_easy.web_gui import launch

    # The signed per-user installer replaces the complete onedir application
    # during an upgrade. Listen before importing the WebView stack so setup can
    # release every packaged file without killing unrelated processes.
    listen_for_windows_installer_shutdown()
    launch(background_start=args.background, intro_complete=args.intro_complete)


if __name__ == "__main__":
    main()
