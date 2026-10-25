"""The CamAndroid icon, drawn from the same shapes as the Android launcher icon
(android/app/src/main/res/drawable/ic_launcher_foreground.xml on @color/ic_launcher_background).

Run ``python -m camandroid_desktop.icon out.ico`` to write the Windows icon used by the exe and
the installer.
"""

from __future__ import annotations

import sys

from PIL import Image, ImageDraw

BACKGROUND = (0x1F, 0x2A, 0x44)
WHITE = (255, 255, 255)
ACCENT = (0x4F, 0xC3, 0xF7)
ICO_SIZES = [16, 20, 24, 32, 40, 48, 64, 96, 128, 256]

# Visible part of the 108x108 adaptive-icon canvas (launchers mask to roughly the middle 72).
_VIEW = (14, 14, 94, 94)


def render(size: int = 256) -> Image.Image:
    """Square RGBA icon with rounded corners."""
    scale = 4 * size / (_VIEW[2] - _VIEW[0])  # draw 4x larger, then downsample for smooth edges
    big = int(size * 4)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=big // 5, fill=BACKGROUND)

    def xy(x: float, y: float) -> tuple[float, float]:
        return (x - _VIEW[0]) * scale, (y - _VIEW[1]) * scale

    def circle(cx: float, cy: float, r: float, fill) -> None:
        d.ellipse((*xy(cx - r, cy - r), *xy(cx + r, cy + r)), fill=fill)

    circle(54, 50, 20, WHITE)
    circle(54, 50, 12, BACKGROUND)
    circle(54, 50, 6, ACCENT)
    d.rectangle((*xy(50, 70), *xy(58, 76)), fill=WHITE)  # stand
    d.rectangle((*xy(40, 76), *xy(68, 80)), fill=WHITE)  # base
    return img.resize((size, size), Image.Resampling.LANCZOS)


def save_ico(path: str) -> None:
    render(256).save(path, format="ICO", sizes=[(s, s) for s in ICO_SIZES])


if __name__ == "__main__":
    save_ico(sys.argv[1] if len(sys.argv) > 1 else "camandroid.ico")
