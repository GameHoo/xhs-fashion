#!/usr/bin/env python3
"""Combine images into a 2x2 collage with numbered labels.

Usage:
    python make_collage.py --images a.jpg b.jpg c.jpg d.jpg \
                           --start-number 1 \
                           --output collage.jpg

Each image gets a small numbered badge in the top-right corner.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def _draw_badge(draw: ImageDraw.ImageDraw, number: int, x: int, y: int, cell_w: int) -> None:
    """Draw a semi-transparent circle with a white number in the top-right area of a cell."""
    radius = max(20, cell_w // 12)
    cx = x + cell_w - radius - 8
    cy = y + radius + 8
    bbox = (cx - radius, cy - radius, cx + radius, cy + radius)

    draw.ellipse(bbox, fill=(0, 0, 0, 140))

    font_size = int(radius * 1.2)
    font = None
    for candidate in (
        "/System/Library/Fonts/Helvetica.ttc",       # macOS
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Debian/Ubuntu
        "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf", # Fedora/RHEL
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",  # Arch
    ):
        try:
            font = ImageFont.truetype(candidate, font_size)
            break
        except (OSError, IOError):
            continue
    if font is None:
        font = ImageFont.load_default()

    text = str(number)
    text_bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    draw.text((cx - tw / 2, cy - th / 2 - 1), text, fill="white", font=font)


def make_collage(image_paths: list[str], start_number: int, output: str, cell_size: int = 600, gap: int = 6) -> str:
    """Create a 2x2 collage from up to 4 images and return the output path."""
    imgs: list[Image.Image] = []
    for p in image_paths[:4]:
        img = Image.open(p).convert("RGBA")
        img.thumbnail((cell_size, cell_size), Image.LANCZOS)
        imgs.append(img)

    cols, rows = 2, 2
    canvas_w = cols * cell_size + (cols + 1) * gap
    canvas_h = rows * cell_size + (rows + 1) * gap
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    for idx, img in enumerate(imgs):
        col, row = idx % 2, idx // 2
        x = gap + col * (cell_size + gap) + (cell_size - img.width) // 2
        y = gap + row * (cell_size + gap) + (cell_size - img.height) // 2
        canvas.paste(img, (x, y), img)
        cell_x = gap + col * (cell_size + gap)
        cell_y = gap + row * (cell_size + gap)
        _draw_badge(draw, start_number + idx, cell_x, cell_y, cell_size)

    out = canvas.convert("RGB")
    out.save(output, quality=90)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a 2x2 numbered collage.")
    parser.add_argument("--images", nargs="+", required=True, help="Image file paths (up to 4).")
    parser.add_argument("--start-number", type=int, default=1, help="Number for the first image.")
    parser.add_argument("--output", required=True, help="Output file path.")
    args = parser.parse_args()

    if not args.images:
        print("No images provided.", file=sys.stderr)
        sys.exit(1)

    result = make_collage(args.images, args.start_number, args.output)
    print(result)


if __name__ == "__main__":
    main()
