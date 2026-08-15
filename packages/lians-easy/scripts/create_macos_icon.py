"""Create a complete macOS iconset from the existing Lians product icon."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


ICON_VARIANTS = (
    (16, "icon_16x16.png"),
    (32, "icon_16x16@2x.png"),
    (32, "icon_32x32.png"),
    (64, "icon_32x32@2x.png"),
    (128, "icon_128x128.png"),
    (256, "icon_128x128@2x.png"),
    (256, "icon_256x256.png"),
    (512, "icon_256x256@2x.png"),
    (512, "icon_512x512.png"),
    (1024, "icon_512x512@2x.png"),
)


def _largest_frame(source: Path) -> Image.Image:
    image = Image.open(source)
    ico = getattr(image, "ico", None)
    if ico is not None:
        image = ico.getimage(max(ico.sizes(), key=lambda size: size[0] * size[1]))
    return image.convert("RGBA")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()

    source = arguments.source.resolve()
    output = arguments.output.resolve()
    if not source.is_file():
        raise SystemExit(f"Lians icon source was not found: {source}")
    if output.exists():
        raise SystemExit(f"Refusing to replace an existing iconset: {output}")

    image = _largest_frame(source)
    if image.width != image.height:
        raise SystemExit(f"Lians icon source must be square; found {image.size}")

    output.mkdir(parents=True)
    resampling = Image.Resampling.LANCZOS
    for size, name in ICON_VARIANTS:
        image.resize((size, size), resampling).save(output / name, format="PNG")


if __name__ == "__main__":
    main()
