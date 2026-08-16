"""Create transparent Lotus assets and a multi-resolution Windows icon."""

from __future__ import annotations

import argparse
import base64
from io import BytesIO
from pathlib import Path

from PIL import Image

WINDOWS_SIZES = (16, 24, 32, 48, 64, 128, 256)


def remove_black_matte(image: Image.Image) -> Image.Image:
    """Recover antialiased blue pixels from the legacy black-backed favicon."""
    source = image.convert("RGBA")
    if source.getchannel("A").getextrema()[0] == 0:
        return source

    output = Image.new("RGBA", source.size)
    converted: list[tuple[int, int, int, int]] = []
    for red, green, blue, _alpha in source.getdata():
        is_blue = blue >= 5 and blue > red * 1.45 and blue > green * 1.35
        if not is_blue:
            converted.append((0, 0, 0, 0))
            continue
        if blue >= 96:
            converted.append((red, green, blue, 255))
            continue
        coverage = min(1.0, blue / 130)
        converted.append(
            (
                min(255, round(red / coverage)),
                min(255, round(green / coverage)),
                min(255, round(blue / coverage)),
                round(coverage * 255),
            )
        )
    output.putdata(converted)
    return output


def save_png(image: Image.Image, destination: Path, *, size: int = 128) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    resized = image.resize((size, size), Image.Resampling.LANCZOS)
    resized.save(destination, format="PNG", optimize=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Legacy or transparent square Lotus PNG",
    )
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parents[1]
    repository_root = package_root.parents[1]
    source = args.source or package_root / "lians_easy" / "desktop" / "favicon.png"
    if not source.is_file():
        raise SystemExit(f"Lotus source was not found: {source}")

    image = Image.open(source)
    if image.width != image.height:
        raise SystemExit(f"Lotus source must be square; found {image.size}")
    lotus = remove_black_matte(image)
    if lotus.getchannel("A").getextrema()[0] != 0:
        raise SystemExit("Lotus background is still opaque")

    desktop_png = package_root / "lians_easy" / "desktop" / "lotus.png"
    docs_png = repository_root / "docs" / "images" / "favicon.png"
    mcpb_png = package_root / "mcpb-icon.png"
    tester_base64 = package_root / "lians_easy" / "tester" / "favicon.png.b64"
    windows_icon = package_root / "windows-lians.ico"

    for destination in (desktop_png, docs_png, mcpb_png):
        save_png(lotus, destination)

    buffer = BytesIO()
    lotus.resize((128, 128), Image.Resampling.LANCZOS).save(
        buffer, format="PNG", optimize=True
    )
    tester_base64.write_text(
        base64.b64encode(buffer.getvalue()).decode("ascii") + "\n",
        encoding="ascii",
    )

    icon_source = lotus.resize((256, 256), Image.Resampling.LANCZOS)
    icon_source.save(windows_icon, format="ICO", sizes=[(size, size) for size in WINDOWS_SIZES])


if __name__ == "__main__":
    main()
