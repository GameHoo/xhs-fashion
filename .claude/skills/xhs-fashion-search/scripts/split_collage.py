#!/usr/bin/env python3
"""Split a grid/collage image into individual pieces, or reassemble pieces into a grid.

Usage:
    python split_collage.py split --image collage.jpg --output-dir /tmp/splits
    python split_collage.py split --image collage.jpg --grid 2x2 --output-dir /tmp/splits
    python split_collage.py reassemble --pieces a.jpg b.jpg c.jpg d.jpg --grid 2x2 --output out.jpg
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image


def _line_variance(pixels, fixed: int, length: int, axis: str, step: int = 4) -> float:
    """Compute variance along a row or column, sampling every *step* pixels."""
    if axis == "row":
        samples = [pixels[x, fixed] for x in range(0, length, step)]
    else:  # col
        samples = [pixels[fixed, y] for y in range(0, length, step)]
    n = len(samples)
    if n == 0:
        return 0.0
    mean = sum(samples) / n
    return sum((p - mean) ** 2 for p in samples) / n


def _find_gaps(
    variances: list[float], min_gap_width: int = 3, max_gap_ratio: float = 0.05
) -> list[tuple[int, int]]:
    """Find low-variance bands (gaps/borders) in a 1D variance profile.

    Returns list of (start, end) ranges.
    """
    sorted_vars = sorted(variances)
    ref = sorted_vars[int(len(sorted_vars) * 0.75)] if variances else 1.0
    threshold = max(ref * 0.05, 2.0)

    total = len(variances)
    max_gap_width = int(total * max_gap_ratio)

    gaps: list[tuple[int, int]] = []
    start = None
    for i, v in enumerate(variances):
        if v < threshold and start is None:
            start = i
        elif (v >= threshold or i == total - 1) and start is not None:
            end = i if v >= threshold else i + 1
            width = end - start
            if min_gap_width <= width <= max(max_gap_width, min_gap_width):
                gaps.append((start, end))
            start = None

    return gaps


def _content_regions(total: int, gaps: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Return content regions between (and around) gaps."""
    regions: list[tuple[int, int]] = []
    pos = 0
    for gs, ge in gaps:
        if gs > pos:
            regions.append((pos, gs))
        pos = ge
    if pos < total:
        regions.append((pos, total))
    return regions


def detect_grid(image: Image.Image) -> tuple[int, int] | None:
    """Auto-detect grid layout. Returns (cols, rows) or None."""
    gray = image.convert("L")
    w, h = gray.size
    pixels = gray.load()

    row_vars = [_line_variance(pixels, y, w, "row") for y in range(h)]
    col_vars = [_line_variance(pixels, x, h, "col") for x in range(w)]

    h_gaps = _find_gaps(row_vars)
    v_gaps = _find_gaps(col_vars)

    # Filter edge gaps — borders around the image don't count as splits
    edge_h = int(h * 0.05)
    edge_w = int(w * 0.05)
    interior_h = [(s, e) for s, e in h_gaps if s > edge_h and e < h - edge_h]
    interior_v = [(s, e) for s, e in v_gaps if s > edge_w and e < w - edge_w]

    rows = len(interior_h) + 1
    cols = len(interior_v) + 1

    if rows == 1 and cols == 1:
        return None
    if rows > 4 or cols > 4:
        return None

    return (cols, rows)


def split_image(
    image_path: str,
    output_dir: str,
    grid: tuple[int, int] | None = None,
) -> dict:
    """Split a collage image into individual pieces.

    Returns dict with status, grid, pieces[].
    """
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    gray = img.convert("L")
    pixels = gray.load()

    if grid is None:
        detected = detect_grid(img)
        if detected is None:
            return {"status": "not_a_collage", "message": "No grid pattern detected"}
        grid = detected

    cols, rows = grid

    # Find gaps (keep ALL gaps including edges for precise content boundaries)
    row_vars = [_line_variance(pixels, y, w, "row") for y in range(h)]
    col_vars = [_line_variance(pixels, x, h, "col") for x in range(w)]
    h_gaps = _find_gaps(row_vars)
    v_gaps = _find_gaps(col_vars)

    # Build content regions from gaps
    h_regions = _content_regions(h, h_gaps)
    v_regions = _content_regions(w, v_gaps)

    # If gap-based regions don't match expected grid, fall back to uniform split
    if len(h_regions) != rows:
        piece_h = h // rows
        h_regions = [(i * piece_h, min((i + 1) * piece_h, h)) for i in range(rows)]
    if len(v_regions) != cols:
        piece_w = w // cols
        v_regions = [(i * piece_w, min((i + 1) * piece_w, w)) for i in range(cols)]

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(image_path).stem

    pieces = []
    for ri, (top, bottom) in enumerate(h_regions):
        for ci, (left, right) in enumerate(v_regions):
            piece = img.crop((left, top, right, bottom))
            piece_path = out_dir / f"{stem}_r{ri + 1}c{ci + 1}.jpg"
            piece.save(str(piece_path), quality=92)
            pieces.append(
                {
                    "path": str(piece_path),
                    "width": piece.width,
                    "height": piece.height,
                    "grid_position": f"r{ri + 1}c{ci + 1}",
                }
            )

    return {"status": "ok", "grid": f"{cols}x{rows}", "pieces": pieces}


def reassemble(
    piece_paths: list[str], grid: tuple[int, int], output: str, gap: int = 6
) -> str:
    """Reassemble pieces back into a grid image. Returns output path."""
    cols, rows = grid
    pieces = [Image.open(p).convert("RGB") for p in piece_paths]

    cell_w = max(p.width for p in pieces)
    cell_h = max(p.height for p in pieces)

    canvas_w = cols * cell_w + (cols + 1) * gap
    canvas_h = rows * cell_h + (rows + 1) * gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))

    for idx, piece in enumerate(pieces):
        col = idx % cols
        row = idx // cols
        x = gap + col * (cell_w + gap) + (cell_w - piece.width) // 2
        y = gap + row * (cell_h + gap) + (cell_h - piece.height) // 2
        canvas.paste(piece, (x, y))

    canvas.save(output, quality=92)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Split collage images or reassemble pieces.")
    sub = parser.add_subparsers(dest="command")
    sub.required = True

    sp = sub.add_parser("split", help="Split a collage into pieces")
    sp.add_argument("--image", required=True, help="Input collage image path")
    sp.add_argument("--output-dir", required=True, help="Output directory for pieces")
    sp.add_argument("--grid", help="Grid spec like '2x2' or '3x3'. Auto-detects if omitted.")

    rp = sub.add_parser("reassemble", help="Reassemble pieces into a grid")
    rp.add_argument("--pieces", nargs="+", required=True, help="Piece image paths (row-major)")
    rp.add_argument("--grid", required=True, help="Grid spec like '2x2'")
    rp.add_argument("--output", required=True, help="Output file path")

    args = parser.parse_args()

    if args.command == "split":
        grid = None
        if args.grid:
            parts = args.grid.lower().split("x")
            grid = (int(parts[0]), int(parts[1]))
        result = split_image(args.image, args.output_dir, grid=grid)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif args.command == "reassemble":
        parts = args.grid.lower().split("x")
        grid = (int(parts[0]), int(parts[1]))
        result = reassemble(args.pieces, grid, args.output)
        print(result)


if __name__ == "__main__":
    main()
