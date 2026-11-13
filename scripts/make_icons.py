"""Draw the brand icons (an original pictogram, not the Jellyfin logo).

Run in the test image, which has Pillow:
    python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
TOP = (170, 92, 195)
BOTTOM = (0, 164, 220)


def draw(size: int) -> Image.Image:
    """Return a rounded gradient tile with a remote-control play pictogram."""
    scale = 4
    big = size * scale
    gradient = Image.new("RGBA", (big, big))
    pixels = gradient.load()
    for y in range(big):
        t = y / (big - 1)
        color = tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True))
        for x in range(big):
            pixels[x, y] = (*color, 255)
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, big - 1, big - 1), radius=big // 5, fill=255
    )
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    tile.paste(gradient, mask=mask)

    pen = ImageDraw.Draw(tile)
    white = (255, 255, 255, 255)
    # Play triangle.
    cx, cy, r = big * 0.53, big * 0.36, big * 0.19
    pen.polygon(
        [(cx - r * 0.7, cy - r), (cx - r * 0.7, cy + r), (cx + r, cy)], fill=white
    )
    # Signal arcs: the integration controls the player remotely.
    width = max(1, big // 28)
    for radius in (0.12, 0.2):
        box = (
            big * (0.5 - radius),
            big * (0.84 - radius),
            big * (0.5 + radius),
            big * (0.84 + radius),
        )
        pen.arc(box, start=220, end=320, fill=white, width=width)
    dot = big * 0.035
    pen.ellipse(
        (big * 0.5 - dot, big * 0.84 - dot, big * 0.5 + dot, big * 0.84 + dot),
        fill=white,
    )
    return tile.resize((size, size), Image.LANCZOS)


def main() -> None:
    """Write the HACS listing icon and the integration brand icons."""
    brand = ROOT / "custom_components" / "jellyfin_control" / "brand"
    brand.mkdir(parents=True, exist_ok=True)
    draw(256).save(ROOT / "icon.png", optimize=True)
    draw(256).save(brand / "icon.png", optimize=True)
    draw(512).save(brand / "icon@2x.png", optimize=True)


if __name__ == "__main__":
    main()
