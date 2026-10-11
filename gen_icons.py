#!/usr/bin/env python3
"""Generate 64x64 button icons for StreamDock N3 from buttons.json config."""
import json
import os
from PIL import Image, ImageDraw, ImageFont

ICON_SIZE = 64
ICONS_DIR = os.path.join(os.path.dirname(__file__), "icons")
CONFIG = os.path.join(os.path.dirname(__file__), "buttons.json")


def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def generate_icon(button):
    bg = hex_to_rgb(button["color"])
    img = Image.new("RGB", (ICON_SIZE, ICON_SIZE), bg)
    draw = ImageDraw.Draw(img)

    text = button["icon_text"]
    label = button["label"]

    # Main text - big and bold
    try:
        font_big = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 22)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 10)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Center the main text
    bbox = draw.textbbox((0, 0), text, font=font_big)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (ICON_SIZE - tw) // 2
    y = (ICON_SIZE - th) // 2 - 6
    draw.text((x, y), text, fill="white", font=font_big)

    # Label at bottom
    bbox2 = draw.textbbox((0, 0), label, font=font_small)
    lw = bbox2[2] - bbox2[0]
    draw.text(((ICON_SIZE - lw) // 2, ICON_SIZE - 14), label, fill=(255, 255, 255, 180), font=font_small)

    path = os.path.join(ICONS_DIR, f"key_{button['key']}.jpg")
    img.save(path, "JPEG", quality=95)
    print(f"  Key {button['key']}: {path} ({button['label']})")
    return path


def main():
    os.makedirs(ICONS_DIR, exist_ok=True)
    with open(CONFIG) as f:
        buttons = json.load(f)

    print(f"Generating {len(buttons)} button icons...")
    for btn in buttons:
        generate_icon(btn)
    print("Done.")


if __name__ == "__main__":
    main()
