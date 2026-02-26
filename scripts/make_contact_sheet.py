#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate screenshot contact sheet")
    parser.add_argument("src", help="Screenshot directory (contains png files and optional manifest.json)")
    parser.add_argument("--out", default="all-pages-contact-sheet.jpg")
    parser.add_argument("--thumb-width", type=int, default=640)
    parser.add_argument("--thumb-height", type=int, default=400)
    parser.add_argument("--cols", type=int, default=3)
    parser.add_argument("--pad", type=int, default=20)
    parser.add_argument("--header", type=int, default=40)
    return parser.parse_args()


def load_items(src: Path) -> list[tuple[str, Path]]:
    manifest = src / "manifest.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        routes = data.get("routes", []) if isinstance(data, dict) else []
        items: list[tuple[str, Path]] = []
        for item in routes:
            if not isinstance(item, dict):
                continue
            route = str(item.get("route", "")) or "(unknown)"
            file_path = Path(str(item.get("file", "")))
            if not file_path.is_absolute():
                file_path = src / file_path
            if file_path.exists():
                items.append((route, file_path))
        if items:
            return items

    pngs = sorted(src.glob("*.png"))
    return [(f"/{img.stem}", img) for img in pngs]


def main() -> int:
    args = parse_args()
    src = Path(args.src)
    if not src.exists() or not src.is_dir():
        raise SystemExit(f"invalid src directory: {src}")

    items = load_items(src)
    if not items:
        raise SystemExit("no screenshots found")

    thumb_w = args.thumb_width
    thumb_h = args.thumb_height
    pad = args.pad
    header = args.header
    cols = max(1, args.cols)
    rows = (len(items) + cols - 1) // cols

    canvas = Image.new(
        "RGB",
        (cols * (thumb_w + pad) + pad, rows * (thumb_h + header + pad) + pad),
        (18, 18, 18),
    )
    draw = ImageDraw.Draw(canvas)

    for i, (label, path) in enumerate(items):
        im = Image.open(path).convert("RGB")
        im.thumbnail((thumb_w, thumb_h))

        box = Image.new("RGB", (thumb_w, thumb_h), (40, 40, 40))
        ox = (thumb_w - im.width) // 2
        oy = (thumb_h - im.height) // 2
        box.paste(im, (ox, oy))

        r = i // cols
        c = i % cols
        x = pad + c * (thumb_w + pad)
        y = pad + r * (thumb_h + header + pad)

        canvas.paste(box, (x, y + header))
        draw.text((x, y + 10), f"{i + 1}. {label}", fill=(230, 230, 230))

    out = src / args.out
    canvas.save(out, quality=85)
    print(str(out.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
