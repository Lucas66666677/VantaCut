"""Small generated animated WebP stickers used by the built-in library."""
from __future__ import annotations

import io
import math

from PIL import Image, ImageDraw


def _frame(sticker_id: str, index: int, size: int = 160) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0)); draw = ImageDraw.Draw(image); phase = index / 8 * math.tau; center = size // 2
    if sticker_id == "snowflake_loop":
        radius = 48 + math.sin(phase) * 7
        for offset in range(6):
            angle = phase / 4 + offset * math.tau / 6; x, y = center + math.cos(angle) * radius, center + math.sin(angle) * radius
            draw.line((center, center, x, y), fill=(220, 248, 255, 255), width=7); draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(255, 255, 255, 255))
    elif sticker_id == "roadtrip_car":
        shift = int(math.sin(phase) * 7); draw.rounded_rectangle((25 + shift, 70, 135 + shift, 112), radius=12, fill=(255, 186, 56, 255)); draw.polygon(((52 + shift, 70), (77 + shift, 45), (111 + shift, 70)), fill=(141, 215, 243, 255));
        for x in (48 + shift, 112 + shift): draw.ellipse((x - 13, 100, x + 13, 126), fill=(39, 47, 62, 255)); draw.ellipse((x - 5, 108, x + 5, 118), fill=(211, 219, 230, 255))
    elif sticker_id == "shiver_face":
        shift = int(math.sin(phase * 2) * 6); draw.ellipse((35 + shift, 35, 125 + shift, 125), fill=(121, 211, 255, 255)); draw.arc((53 + shift, 55, 74 + shift, 76), 190, 350, fill=(30, 70, 120, 255), width=5); draw.arc((87 + shift, 55, 108 + shift, 76), 190, 350, fill=(30, 70, 120, 255), width=5); draw.rectangle((65 + shift, 92, 96 + shift, 100), fill=(245, 255, 255, 255))
    elif sticker_id == "heart_burst":
        pulse = int(math.sin(phase) * 8); draw.polygon(((80, 124 + pulse), (30, 76), (38, 48), (58, 42), (80, 61), (102, 42), (122, 48), (130, 76)), fill=(255, 82, 138, 255))
    elif sticker_id == "censor_duck":
        shift = int(math.sin(phase * 2) * 5); draw.ellipse((28 + shift, 26, 132 + shift, 134), fill=(255, 220, 55, 255), outline=(65, 48, 18, 255), width=6); draw.ellipse((58 + shift, 65, 70 + shift, 78), fill=(20, 20, 20, 255)); draw.ellipse((98 + shift, 65, 110 + shift, 78), fill=(20, 20, 20, 255)); draw.rounded_rectangle((54 + shift, 88, 106 + shift, 112), radius=12, fill=(255, 135, 42, 255))
    elif sticker_id == "censor_angry":
        shift = int(math.sin(phase * 3) * 4); draw.ellipse((28 + shift, 26, 132 + shift, 134), fill=(245, 69, 69, 255), outline=(80, 18, 18, 255), width=6); draw.line((53 + shift, 67, 75 + shift, 76), fill=(45, 12, 12, 255), width=8); draw.line((107 + shift, 67, 85 + shift, 76), fill=(45, 12, 12, 255), width=8); draw.arc((55 + shift, 76, 105 + shift, 116), 200, 340, fill=(45, 12, 12, 255), width=8)
    else:
        radius = 38 + int(math.sin(phase) * 12)
        for angle in range(0, 360, 45):
            rad = math.radians(angle + index * 5); x, y = center + math.cos(rad) * 57, center + math.sin(rad) * 57
            draw.line((center, center, x, y), fill=(255, 225, 70, 255), width=10)
        draw.ellipse((center - radius, center - radius, center + radius, center + radius), fill=(255, 157, 51, 255))
    return image


def animated_sticker_webp(sticker_id: str) -> bytes:
    valid = {"snowflake_loop", "roadtrip_car", "shiver_face", "sparkle_pop", "heart_burst", "alert_burst", "censor_angry", "censor_duck"}
    if sticker_id not in valid:
        raise ValueError("Unknown sticker asset")
    frames = [_frame(sticker_id, index) for index in range(8)]; output = io.BytesIO()
    frames[0].save(output, format="WEBP", save_all=True, append_images=frames[1:], duration=85, loop=0, lossless=True, quality=85)
    return output.getvalue()
