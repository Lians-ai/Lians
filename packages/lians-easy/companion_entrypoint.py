"""Windowed entry point for the resident Lians desktop companion."""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--background", action="store_true")
    args, _unknown = parser.parse_known_args()

    from lians_easy.web_gui import launch

    launch(background_start=args.background)


if __name__ == "__main__":
    main()
