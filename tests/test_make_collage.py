import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / ".claude" / "skills" / "xhs-fashion-search" / "scripts"))
from make_collage import make_collage


def _make_image(path: Path, color: str, size: tuple[int, int] = (200, 200)) -> None:
    Image.new("RGB", size, color).save(path)


def test_make_collage_4_images(tmp_path):
    images = []
    for i, color in enumerate(["red", "green", "blue", "yellow"]):
        p = tmp_path / f"img_{i}.jpg"
        _make_image(p, color)
        images.append(str(p))

    output = str(tmp_path / "collage.jpg")
    result = make_collage(images, start_number=1, output=output)

    assert result == output
    assert Path(output).exists()
    img = Image.open(output)
    assert img.width > 0
    assert img.height > 0


def test_make_collage_2_images(tmp_path):
    images = []
    for i, color in enumerate(["red", "green"]):
        p = tmp_path / f"img_{i}.jpg"
        _make_image(p, color)
        images.append(str(p))

    output = str(tmp_path / "collage.jpg")
    result = make_collage(images, start_number=5, output=output)

    assert Path(output).exists()
    img = Image.open(output)
    assert img.width > 0


def test_make_collage_1_image(tmp_path):
    p = tmp_path / "solo.jpg"
    _make_image(p, "purple")

    output = str(tmp_path / "collage.jpg")
    make_collage([str(p)], start_number=1, output=output)

    assert Path(output).exists()


def test_make_collage_different_aspect_ratios(tmp_path):
    images = []
    sizes = [(100, 300), (300, 100), (200, 200), (50, 400)]
    for i, size in enumerate(sizes):
        p = tmp_path / f"img_{i}.jpg"
        _make_image(p, "gray", size=size)
        images.append(str(p))

    output = str(tmp_path / "collage.jpg")
    make_collage(images, start_number=1, output=output)

    assert Path(output).exists()
    img = Image.open(output)
    assert img.width > 0


def test_make_collage_custom_start_number(tmp_path):
    """Ensure start_number offsets badge labels correctly (no crash)."""
    p = tmp_path / "img.jpg"
    _make_image(p, "white")

    output = str(tmp_path / "collage.jpg")
    make_collage([str(p)], start_number=99, output=output)
    assert Path(output).exists()
