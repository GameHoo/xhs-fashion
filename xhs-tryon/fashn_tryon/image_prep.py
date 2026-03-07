from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}


def preprocess_image(
    source_path: Path,
    target_path: Path,
    *,
    max_long_edge: int = 2000,
    jpeg_quality: int = 95,
) -> dict[str, Any]:
    with Image.open(source_path) as image:
        image = ImageOps.exif_transpose(image)
        original_width, original_height = image.size

        if image.mode not in {"RGB", "L"}:
            # Flatten alpha images onto white so Pillow can save a deterministic JPEG.
            background = Image.new("RGBA", image.size, (255, 255, 255, 255))
            background.paste(image.convert("RGBA"), mask=image.convert("RGBA"))
            image = background.convert("RGB")
        elif image.mode == "L":
            image = image.convert("RGB")

        width, height = image.size
        longest_edge = max(width, height)
        if longest_edge > max_long_edge:
            scale = max_long_edge / float(longest_edge)
            resized = (
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
            )
            image = image.resize(resized, Image.Resampling.LANCZOS)

        target_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(target_path, format="JPEG", quality=jpeg_quality, optimize=True)

    return {
        "source_path": str(source_path.resolve()),
        "prepared_path": str(target_path.resolve()),
        "original_width": original_width,
        "original_height": original_height,
        "prepared_width": image.size[0],
        "prepared_height": image.size[1],
        "mime_type": "image/jpeg",
    }


def encode_data_uri(image_path: Path) -> str:
    raw = image_path.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def write_data_uri(data_uri: str, target_path: Path) -> dict[str, Any]:
    header, encoded = split_data_uri(data_uri)
    mime_type = "application/octet-stream"
    if header.startswith("data:") and ";" in header:
        mime_type = header[5:].split(";", 1)[0]

    payload = base64.b64decode(encoded)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(payload)
    return {
        "path": str(target_path.resolve()),
        "mime_type": mime_type,
        "bytes": len(payload),
    }


def split_data_uri(data_uri: str) -> tuple[str, str]:
    if "," not in data_uri:
        raise ValueError("Invalid data URI: missing comma separator")
    header, encoded = data_uri.split(",", 1)
    if ";base64" not in header:
        raise ValueError("Invalid data URI: expected base64 header")
    return header, encoded


def guess_extension_from_mime(mime_type: str, default: str) -> str:
    mime_type = mime_type.lower().strip()
    if mime_type.endswith("png"):
        return ".png"
    if mime_type.endswith("jpeg") or mime_type.endswith("jpg"):
        return ".jpg"
    if mime_type.endswith("webp"):
        return ".webp"
    return default


def is_supported_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS


def human_file_size(num_bytes: int) -> str:
    value = float(num_bytes)
    units = ["B", "KB", "MB", "GB"]
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)}{unit}"
            return f"{value:.1f}{unit}"
        value /= 1024.0
    return f"{int(num_bytes)}B"
