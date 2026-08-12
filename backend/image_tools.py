from __future__ import annotations

import hashlib
import io
import math
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    # Setup installs pillow-heif. An older environment will still return the
    # normal readable-image error instead of preventing the service from booting.
    pass


PALETTE = {
    "black": (30, 30, 30),
    "charcoal": (67, 68, 70),
    "gray": (130, 130, 130),
    "white": (239, 237, 230),
    "cream": (225, 211, 178),
    "brown": (107, 76, 56),
    "tan": (183, 147, 105),
    "navy": (35, 49, 79),
    "blue": (53, 104, 154),
    "green": (57, 106, 73),
    "olive": (103, 105, 54),
    "red": (157, 48, 48),
    "burgundy": (91, 34, 50),
    "orange": (190, 91, 39),
    "yellow": (209, 177, 52),
    "purple": (104, 66, 128),
    "pink": (193, 117, 137),
}


def decode_and_normalize(payload: bytes, destination: Path) -> tuple[str, str]:
    try:
        source = Image.open(io.BytesIO(payload))
        source.load()
    except Exception as exc:
        raise ValueError("The selected file is not a readable image.") from exc

    image = ImageOps.exif_transpose(source).convert("RGB")
    image.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "JPEG", quality=91, optimize=True)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    color_name, color_hex = dominant_color(image)
    return digest, f"{color_name}|{color_hex}"


def dominant_color(image: Image.Image) -> tuple[str, str]:
    sample = image.copy()
    sample.thumbnail((96, 96), Image.Resampling.BILINEAR)
    pixels = []
    for red, green, blue in sample.getdata():
        high = max(red, green, blue)
        low = min(red, green, blue)
        if high > 242 and low > 226:
            continue
        if high < 18:
            pixels.append((red, green, blue))
            continue
        saturation = high - low
        if saturation > 9 or high < 222:
            pixels.append((red, green, blue))

    if not pixels:
        pixels = list(sample.getdata())
    pixels.sort(key=lambda value: sum(value))
    middle = pixels[len(pixels) // 2]

    nearest = min(
        PALETTE,
        key=lambda name: math.sqrt(
            sum((middle[index] - PALETTE[name][index]) ** 2 for index in range(3))
        ),
    )
    return nearest, "#%02x%02x%02x" % middle


def make_privacy_copy(source: Path, destination: Path) -> tuple[str, int]:
    image = Image.open(source).convert("RGB")
    faces: list[tuple[int, int, int, int]] = []

    try:
        import cv2
        import numpy as np

        frame = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
        cascade = cv2.CascadeClassifier(
            str(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        )
        detected = cascade.detectMultiScale(
            frame,
            scaleFactor=1.08,
            minNeighbors=5,
            minSize=(24, 24),
        )
        faces = [tuple(int(value) for value in face) for face in detected]
    except Exception:
        faces = []

    if faces:
        for x, y, width, height in faces:
            pad_x = int(width * 0.34)
            pad_y = int(height * 0.42)
            box = (
                max(0, x - pad_x),
                max(0, y - pad_y),
                min(image.width, x + width + pad_x),
                min(image.height, y + height + pad_y),
            )
            face = image.crop(box).filter(ImageFilter.GaussianBlur(radius=max(18, width // 3)))
            image.paste(face, box)
        method = f"Detected and blurred {len(faces)} face area{'s' if len(faces) != 1 else ''}"
    else:
        # A conservative fallback keeps the workflow private even if a detector
        # misses a small or partially turned face. The user reviews this copy.
        box = (
            int(image.width * 0.18),
            0,
            int(image.width * 0.82),
            int(image.height * 0.32),
        )
        region = image.crop(box).filter(
            ImageFilter.GaussianBlur(radius=max(22, image.width // 24))
        )
        image.paste(region, box)
        method = "Conservative upper-body privacy blur (no face confidently detected)"

    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, "JPEG", quality=88, optimize=True)
    return method, len(faces)
