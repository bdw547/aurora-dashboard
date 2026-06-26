#!/usr/bin/env python3
"""
Aurora screensaver photo prep — bake "blurred-fill" backgrounds.

The Aurora panel can't scale or blur images on-device, so we pre-bake each
photo into a <W>x<H> JPEG: a cover-cropped, blurred, slightly darkened copy of
the photo fills the whole frame (no black bars), with the full uncropped photo
fit and centered on top. The panel shows these 1:1 and overlays its own
clock/weather. Run this whenever you add/change photos (they change rarely).

Source photos can be any format Pillow reads (jpg/png/gif/webp/bmp/tiff/...);
output is always JPEG, sized exactly for the panel.

Usage:
  python3 screensaver_prep.py INPUT_DIR OUTPUT_DIR
      [--size 1024x600] [--blur 28] [--darken 0.65] [--quality 88]

Then copy OUTPUT_DIR/*.jpg into Home Assistant's  config/www/screensaver/.
"""
import argparse
import os
import sys

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

READABLE = (".jpg", ".jpeg", ".png", ".bmp", ".webp", ".gif", ".tif", ".tiff")


def cover(im, w, h):
    """Scale to fill WxH, then center-crop the overflow (no distortion)."""
    sw, sh = im.size
    scale = max(w / sw, h / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return im.crop((left, top, left + w, top + h))


def contain(im, w, h):
    """Fit entirely within WxH, preserving aspect (no crop)."""
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    return im


def bake(path, w, h, blur, darken):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    bg = cover(im, w, h).filter(ImageFilter.GaussianBlur(blur))
    if darken < 1.0:
        bg = ImageEnhance.Brightness(bg).enhance(darken)
    fg = contain(im, w, h)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


def main():
    ap = argparse.ArgumentParser(description="Bake blurred-fill screensaver photos for Aurora.")
    ap.add_argument("input", help="folder of source photos")
    ap.add_argument("output", help="folder to write baked JPEGs into")
    ap.add_argument("--size", default="1024x600", help="panel size WxH (default 1024x600)")
    ap.add_argument("--blur", type=float, default=28.0, help="background blur radius (default 28)")
    ap.add_argument("--darken", type=float, default=0.65, help="bg brightness 0-1 (default 0.65)")
    ap.add_argument("--quality", type=int, default=88, help="JPEG quality (default 88)")
    a = ap.parse_args()

    w, h = (int(v) for v in a.size.lower().split("x"))
    os.makedirs(a.output, exist_ok=True)
    files = [f for f in sorted(os.listdir(a.input)) if f.lower().endswith(READABLE)]
    if not files:
        print(f"No images found in {a.input}")
        return 1

    done = 0
    for f in files:
        try:
            out = bake(os.path.join(a.input, f), w, h, a.blur, a.darken)
            dst = os.path.join(a.output, os.path.splitext(f)[0] + ".jpg")
            out.save(dst, "JPEG", quality=a.quality)
            print(f"  {f} -> {os.path.basename(dst)} ({w}x{h})")
            done += 1
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {f}: {e}")
    print(f"Done: {done}/{len(files)} baked into {a.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
