import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(
    0,
    str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "xhs-fashion-search" / "scripts"),
)
from split_collage import detect_grid, reassemble, split_image  # noqa: E402


def _make_solid(path: Path, color: str, size: tuple[int, int] = (400, 400)) -> None:
    Image.new("RGB", size, color).save(path)


def _make_grid_collage(
    path: Path,
    cols: int,
    rows: int,
    cell_size: tuple[int, int] = (300, 300),
    gap: int = 8,
    border_color: str = "white",
) -> None:
    """Create a synthetic grid collage with solid-color cells and uniform gaps."""
    colors = ["red", "green", "blue", "yellow", "cyan", "magenta", "orange", "purple", "gray"]
    cw, ch = cell_size
    w = cols * cw + (cols + 1) * gap
    h = rows * ch + (rows + 1) * gap
    img = Image.new("RGB", (w, h), border_color)

    for ri in range(rows):
        for ci in range(cols):
            idx = ri * cols + ci
            cell = Image.new("RGB", (cw, ch), colors[idx % len(colors)])
            x = gap + ci * (cw + gap)
            y = gap + ri * (ch + gap)
            img.paste(cell, (x, y))

    img.save(path)


# -- detect_grid --


def test_detect_grid_2x2(tmp_path):
    p = tmp_path / "grid2x2.jpg"
    _make_grid_collage(p, cols=2, rows=2)
    img = Image.open(p)
    result = detect_grid(img)
    assert result == (2, 2)


def test_detect_grid_3x3(tmp_path):
    p = tmp_path / "grid3x3.jpg"
    _make_grid_collage(p, cols=3, rows=3)
    img = Image.open(p)
    result = detect_grid(img)
    assert result == (3, 3)


def test_detect_grid_1x3(tmp_path):
    p = tmp_path / "grid1x3.jpg"
    _make_grid_collage(p, cols=3, rows=1, cell_size=(200, 400))
    img = Image.open(p)
    result = detect_grid(img)
    assert result is not None
    cols, rows = result
    assert cols == 3
    assert rows == 1


def test_detect_grid_not_a_collage(tmp_path):
    p = tmp_path / "single.jpg"
    _make_solid(p, "blue", size=(600, 600))
    img = Image.open(p)
    result = detect_grid(img)
    assert result is None


# -- split_image --


def test_split_2x2(tmp_path):
    collage = tmp_path / "collage.jpg"
    _make_grid_collage(collage, cols=2, rows=2, cell_size=(300, 300), gap=10)
    out_dir = tmp_path / "splits"

    result = split_image(str(collage), str(out_dir))

    assert result["status"] == "ok"
    assert result["grid"] == "2x2"
    assert len(result["pieces"]) == 4
    for piece in result["pieces"]:
        assert Path(piece["path"]).exists()
        assert piece["width"] >= 280  # roughly cell_size minus gap tolerance
        assert piece["height"] >= 280


def test_split_explicit_grid(tmp_path):
    collage = tmp_path / "collage.jpg"
    _make_grid_collage(collage, cols=2, rows=2, cell_size=(300, 300), gap=10)
    out_dir = tmp_path / "splits"

    result = split_image(str(collage), str(out_dir), grid=(2, 2))

    assert result["status"] == "ok"
    assert len(result["pieces"]) == 4


def test_split_not_a_collage(tmp_path):
    p = tmp_path / "single.jpg"
    _make_solid(p, "red", size=(600, 600))
    out_dir = tmp_path / "splits"

    result = split_image(str(p), str(out_dir))

    assert result["status"] == "not_a_collage"



def test_split_3x3(tmp_path):
    collage = tmp_path / "collage3x3.jpg"
    _make_grid_collage(collage, cols=3, rows=3, cell_size=(300, 300), gap=8)
    out_dir = tmp_path / "splits"

    result = split_image(str(collage), str(out_dir))

    assert result["status"] == "ok"
    assert result["grid"] == "3x3"
    assert len(result["pieces"]) == 9


# -- reassemble --


def test_reassemble(tmp_path):
    pieces = []
    for i, color in enumerate(["red", "green", "blue", "yellow"]):
        p = tmp_path / f"piece_{i}.jpg"
        _make_solid(p, color, size=(300, 300))
        pieces.append(str(p))

    output = str(tmp_path / "reassembled.jpg")
    result = reassemble(pieces, grid=(2, 2), output=output)

    assert result == output
    assert Path(output).exists()
    img = Image.open(output)
    assert img.width > 300
    assert img.height > 300


# -- round-trip: split then reassemble --


def test_split_reassemble_roundtrip(tmp_path):
    collage = tmp_path / "collage.jpg"
    _make_grid_collage(collage, cols=2, rows=2, cell_size=(300, 300), gap=10)
    split_dir = tmp_path / "splits"

    split_result = split_image(str(collage), str(split_dir))
    assert split_result["status"] == "ok"

    piece_paths = [p["path"] for p in split_result["pieces"]]
    output = str(tmp_path / "reassembled.jpg")
    reassemble(piece_paths, grid=(2, 2), output=output)

    assert Path(output).exists()
    img = Image.open(output)
    assert img.width > 0
    assert img.height > 0
