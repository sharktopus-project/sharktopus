"""Build logo derivatives from site/assets/sharktopus_source.png.

Run once after replacing the source PNG; generates the sizes we plug into
the site, README, PyPI, GitHub avatar, and social cards.
"""
from __future__ import annotations

from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site" / "assets" / "sharktopus_source.png"
MARK = ROOT / "site" / "assets" / "sharktopus_mark.png"
OUT = ROOT / "site" / "assets"


def _fill_enclosed_holes(mask: np.ndarray) -> np.ndarray:
    """Turn True any 0-region fully enclosed by 1-pixels.

    Flood-fill the complement from the four corners: pixels the flood
    reaches are the true exterior. Any 0-pixel not reached is an
    interior hole — merge it into the mask.
    """
    h, w = mask.shape
    inv = Image.fromarray((~mask).astype(np.uint8) * 255, "L")
    seed = inv.copy()
    for corner in [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]:
        if seed.getpixel(corner) == 255:
            ImageDraw.floodfill(seed, corner, 128, thresh=0)
    exterior = np.array(seed) == 128
    return mask | ((~mask) & ~exterior)


def strip_white_background(
    im: Image.Image,
    chroma_threshold: int = 20,
    dark_threshold: int = 70,
    outline_close: int | None = None,
) -> Image.Image:
    """Separate subject from background by silhouette detection.

    Earlier versions tried to classify each pixel by color (near-white =
    bg, colored = subject). That breaks when the subject contains legit
    near-white regions (shark belly, teeth, eye sclera, weather-icon
    whites): the same pixel colors appear on both sides of the
    subject/bg boundary, so any purely per-pixel rule either eats the
    belly or leaves checker-pattern fragments.

    Edge-based strategy: find pixels that are clearly **subject**
    (colorful OR part of the dark outline stroke) and morphologically
    close the mask so the outline forms a continuous boundary; then
    fill fully-enclosed holes (belly, teeth, eye, weather icons are all
    surrounded by the dark outline). Everything OUTSIDE the silhouette
    — including between-tentacle pockets that remain open to the
    exterior — becomes transparent.
    """
    rgba = im.convert("RGBA")
    arr = np.array(rgba)
    rgb = arr[..., :3]

    if outline_close is None:
        outline_close = max(2, min(im.width, im.height) // 400)

    # Median-filter probe RGB to dissolve Gemini's dithering/checker
    # so noise speckles don't register as "colored" outside the subject.
    probe = Image.fromarray(rgb).filter(ImageFilter.MedianFilter(size=5))
    rgb_i16 = np.array(probe).astype(np.int16)
    chroma = rgb_i16.max(axis=-1) - rgb_i16.min(axis=-1)
    lightness = rgb_i16.mean(axis=-1)

    # Subject = colorful pixel OR part of the dark ink outline.
    subject_seed = (chroma > chroma_threshold) | (lightness < dark_threshold)
    seed_im = Image.fromarray(subject_seed.astype(np.uint8) * 255, "L")

    # Morphological CLOSE (dilate→erode) to bridge anti-aliased outline
    # gaps — makes belly, teeth, eye, icons become fully enclosed holes.
    closed_im = seed_im.filter(ImageFilter.MaxFilter(size=2 * outline_close + 1))
    closed_im = closed_im.filter(ImageFilter.MinFilter(size=2 * outline_close + 1))
    closed = np.array(closed_im) > 127

    silhouette = _fill_enclosed_holes(closed)

    arr[..., 3] = np.where(silhouette, 255, 0).astype(np.uint8)
    return Image.fromarray(arr)


def fit_height(im: Image.Image, h: int) -> Image.Image:
    w = round(im.width * h / im.height)
    return im.resize((w, h), Image.LANCZOS)


def pad_square(im: Image.Image, size: int) -> Image.Image:
    scaled = im.copy()
    scaled.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - scaled.width) // 2, (size - scaled.height) // 2), scaled)
    return canvas


def center_crop(im: Image.Image, target_w: int, target_h: int) -> Image.Image:
    ar_src = im.width / im.height
    ar_dst = target_w / target_h
    if ar_src > ar_dst:
        new_h = target_h
        new_w = round(im.width * new_h / im.height)
    else:
        new_w = target_w
        new_h = round(im.height * new_w / im.width)
    scaled = im.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return scaled.crop((left, top, left + target_w, top + target_h))


def save_png(im: Image.Image, name: str) -> None:
    path = OUT / name
    im.save(path, "PNG", optimize=True)
    print(f"  {path.relative_to(ROOT)}  {path.stat().st_size // 1024} KB  {im.size}")


def main() -> None:
    raw = Image.open(SRC).convert("RGBA")
    print(f"Source: {SRC.relative_to(ROOT)}  {raw.size}")

    src = strip_white_background(raw)
    src.save(MARK, "PNG", optimize=True)
    print(f"  {MARK.relative_to(ROOT)}  {MARK.stat().st_size // 1024} KB  (transparent bg)")

    # Site header logo (keeps aspect; retina-friendly width).
    save_png(fit_height(src, 400), "logo.png")
    save_png(fit_height(src, 800), "logo@2x.png")

    # PyPI / GitHub avatar — square with transparent padding.
    save_png(pad_square(src, 400), "avatar.png")

    # OG / social card — 1200x630, center-cropped with white-ish canvas.
    og = Image.new("RGBA", (1200, 630), (244, 244, 249, 255))
    fg = fit_height(src, 560)
    og.paste(fg, ((1200 - fg.width) // 2, (630 - fg.height) // 2), fg)
    save_png(og, "og.png")

    # Apple touch + web manifest icons.
    save_png(pad_square(src, 180), "apple-touch-icon.png")
    save_png(pad_square(src, 192), "icon-192.png")
    save_png(pad_square(src, 512), "icon-512.png")

    # Favicon: multi-size ICO (browsers pick best).
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64)]
    ico_frames = [pad_square(src, s[0]) for s in sizes]
    ico_path = OUT / "favicon.ico"
    ico_frames[-1].save(ico_path, format="ICO", sizes=sizes)
    print(f"  {ico_path.relative_to(ROOT)}  {ico_path.stat().st_size // 1024} KB  {sizes}")


if __name__ == "__main__":
    main()
