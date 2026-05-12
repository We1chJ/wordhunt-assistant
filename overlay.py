"""
Transparent screen overlay + floating solution panel for Word Hunt.

Both windows are rendered as PIL RGBA images and displayed via NSImageView
to avoid PyObjC drawRect_ crashes.
"""

import io
import math
from PIL import Image, ImageDraw, ImageFont

from AppKit import (
    NSApplication, NSApplicationActivationPolicyAccessory,
    NSWindow, NSImageView, NSImage, NSColor,
    NSWindowStyleMaskBorderless, NSBackingStoreBuffered,
    NSFloatingWindowLevel, NSImageScaleAxesIndependently,
    NSScreen,
)
from Foundation import NSMakeRect, NSData, NSRunLoop, NSDate

from grid import GRID_X1, GRID_Y1, GRID_X2, GRID_Y2, GRID_ROWS, GRID_COLS

# Palette: (R, G, B) 0-255
PALETTE = [
    (255,  71,  71),  # red
    ( 50, 210,  90),  # green
    ( 70, 150, 255),  # blue
    (255, 200,  30),  # yellow
    (255, 130,  30),  # orange
]

WORD_HUNT_POINTS = {3: 100, 4: 400, 5: 800, 6: 1400, 7: 1800, 8: 2200, 9: 2600}


def _points(word: str) -> int:
    n = len(word)
    return WORD_HUNT_POINTS.get(n, 3000 if n >= 10 else 100)


def _stars(word: str) -> str:
    n = len(word)
    filled = max(1, min(5, n - 2))
    return "★" * filled + "☆" * (5 - filled)


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    paths = [
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNSDisplay.ttf",
        "/System/Library/Fonts/SFNSText.ttf",
        "/System/Library/Fonts/Geneva.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Overlay renderer
# ---------------------------------------------------------------------------

def _draw_arrow(draw: ImageDraw.Draw, p1, p2, color, width=6) -> None:
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return
    ux, uy = dx / length, dy / length
    tip_x, tip_y = x2 - ux * 22, y2 - uy * 22
    draw.line([(x1, y1), (tip_x, tip_y)], fill=color, width=width)
    angle = math.atan2(dy, dx)
    ha, hl = math.pi / 5, 18
    ax1 = tip_x - hl * math.cos(angle - ha)
    ay1 = tip_y - hl * math.sin(angle - ha)
    ax2 = tip_x - hl * math.cos(angle + ha)
    ay2 = tip_y - hl * math.sin(angle + ha)
    draw.polygon([(tip_x, tip_y), (ax1, ay1), (ax2, ay2)], fill=color)


def _draw_step(draw: ImageDraw.Draw, center, number: int, rgb) -> None:
    cx, cy = center
    r = 18
    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                 fill=(255, 255, 255, 230), outline=rgb + (255,), width=3)
    font = _load_font(18, bold=True)
    draw.text((cx, cy), str(number), fill=rgb + (255,), anchor="mm", font=font)


def _cell_center(row: int, col: int) -> tuple[float, float]:
    """Cell center in screenshot physical-pixel coordinates."""
    cell_w = (GRID_X2 - GRID_X1) / GRID_COLS
    cell_h = (GRID_Y2 - GRID_Y1) / GRID_ROWS
    return (GRID_X1 + (col + 0.5) * cell_w,
            GRID_Y1 + (row + 0.5) * cell_h)


def render_overlay(w_px: int, h_px: int, path_data: list) -> Image.Image:
    """Render solution arrows onto a transparent RGBA image at physical resolution."""
    img = Image.new("RGBA", (w_px, h_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for _word, path, rgb in path_data:
        pts = [_cell_center(r, c) for r, c in path]
        shadow = (0, 0, 0, 90)
        for i in range(len(pts) - 1):
            _draw_arrow(draw, pts[i], pts[i + 1], shadow, width=11)
        for i in range(len(pts) - 1):
            _draw_arrow(draw, pts[i], pts[i + 1], rgb + (220,), width=7)
        for idx, pt in enumerate(pts):
            _draw_step(draw, pt, idx + 1, rgb)
    return img


# ---------------------------------------------------------------------------
# Panel renderer
# ---------------------------------------------------------------------------

PANEL_W = 280
ROW_H   = 48
HEADER_H = 52
FOOTER_H = 16


def render_panel(path_data: list, scale: float) -> Image.Image:
    """Render the word-list panel at physical resolution."""
    n = len(path_data)
    lw = int(PANEL_W * scale)
    lh = int((HEADER_H + n * ROW_H + FOOTER_H) * scale)
    s = scale  # shorthand

    img = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Background — rounded dark card
    bg = (28, 28, 32, 235)
    r = int(14 * s)
    draw.rounded_rectangle([(0, 0), (lw - 1, lh - 1)], radius=r, fill=bg)

    # Header
    font_title = _load_font(int(15 * s), bold=True)
    draw.text((lw // 2, int(HEADER_H * s * 0.55)), "WORD HUNT",
              fill=(255, 255, 255, 255), anchor="mm", font=font_title)
    draw.line([(int(16 * s), int(HEADER_H * s - 1)),
               (lw - int(16 * s), int(HEADER_H * s - 1))],
              fill=(80, 80, 90, 180), width=1)

    font_word   = _load_font(int(14 * s), bold=True)
    font_info   = _load_font(int(11 * s))
    font_stars  = _load_font(int(11 * s))

    for i, (word, _path, rgb) in enumerate(path_data):
        y0 = int((HEADER_H + i * ROW_H) * s)
        cy = y0 + int(ROW_H * s // 2)

        # Color dot
        dot_r = int(7 * s)
        dot_x = int(20 * s)
        draw.ellipse([(dot_x - dot_r, cy - dot_r),
                      (dot_x + dot_r, cy + dot_r)],
                     fill=rgb + (255,))

        # Word
        draw.text((int(38 * s), cy), word,
                  fill=(255, 255, 255, 255), anchor="lm", font=font_word)

        # Stars
        draw.text((int(158 * s), cy - int(3 * s)), _stars(word),
                  fill=(255, 210, 50, 220), anchor="lm", font=font_stars)

        # Points
        pts_str = f"{_points(word):,} pts"
        draw.text((lw - int(14 * s), cy), pts_str,
                  fill=(160, 220, 160, 220), anchor="rm", font=font_info)

        # Row separator
        if i < n - 1:
            draw.line([(int(16 * s), y0 + int(ROW_H * s)),
                       (lw - int(16 * s), y0 + int(ROW_H * s))],
                      fill=(60, 60, 70, 120), width=1)

    return img


# ---------------------------------------------------------------------------
# NSWindow helpers
# ---------------------------------------------------------------------------

def _pil_to_nswindow(img: Image.Image, logical_w: float, logical_h: float,
                     screen_x: float, screen_y: float,
                     level: int = NSFloatingWindowLevel,
                     click_through: bool = False) -> NSWindow:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw = buf.getvalue()
    ns_data = NSData.dataWithBytes_length_(raw, len(raw))
    ns_img = NSImage.alloc().initWithData_(ns_data)
    # Tell NSImage its logical display size so Retina scaling works correctly
    ns_img.setSize_((logical_w, logical_h))

    win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(screen_x, screen_y, logical_w, logical_h),
        NSWindowStyleMaskBorderless,
        NSBackingStoreBuffered,
        False,
    )
    win.setBackgroundColor_(NSColor.clearColor())
    win.setOpaque_(False)
    win.setIgnoresMouseEvents_(click_through)
    win.setLevel_(level)
    win.setHasShadow_(not click_through)

    view = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, logical_w, logical_h))
    view.setImage_(ns_img)
    view.setImageScaling_(NSImageScaleAxesIndependently)
    win.setContentView_(view)
    return win


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _build_path_data(solutions, top_n):
    seen: set[str] = set()
    path_data = []
    for word, path in solutions:
        if word in seen:
            continue
        seen.add(word)
        path_data.append((word, path, PALETTE[len(path_data) % len(PALETTE)]))
        if len(path_data) >= top_n:
            break
    return path_data


def _init_app():
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    app.activateIgnoringOtherApps_(True)
    return app


def show_solutions_nonblocking(
    window_bounds: dict,
    solutions: list[tuple[str, list[tuple[int, int]]]],
    top_n: int = 5,
) -> list:
    """
    Create and show the overlay. Does NOT block — returns list of NSWindows.
    Call win.close() on each when done.
    Designed to be used alongside tkinter's mainloop.
    """
    _init_app()

    scale    = NSScreen.mainScreen().backingScaleFactor()
    screen_h = NSScreen.mainScreen().frame().size.height

    wx    = window_bounds["x"]
    ww    = window_bounds["w"]
    wh    = window_bounds["h"]
    wy_ns = screen_h - window_bounds["y"] - wh

    path_data = _build_path_data(solutions, top_n)
    print(f"[overlay] {[w for w,_,_ in path_data]}")

    overlay_img = render_overlay(int(ww * scale), int(wh * scale), path_data)
    overlay_win = _pil_to_nswindow(overlay_img, ww, wh, wx, wy_ns, click_through=True)
    overlay_win.orderFrontRegardless()

    return [overlay_win]


if __name__ == "__main__":
    # Blocking test — runs without the GUI
    from AppKit import NSRunLoop
    from Foundation import NSDate
    from capture import get_phone_mirroring_bounds

    bounds = get_phone_mirroring_bounds()
    if bounds is None:
        bounds = {"x": 100, "y": 100, "w": 316, "h": 696}
        print("[test] using dummy bounds")
    else:
        print("[test] iPhone Mirroring bounds:", bounds)

    fake = [
        ("GOALIE", [(0, 2), (1, 1), (1, 0), (0, 0), (0, 1), (1, 2)]),
        ("GOATEE", [(0, 2), (1, 1), (2, 0), (3, 0), (2, 1), (1, 2)]),
        ("ALIEN",  [(1, 0), (0, 0), (0, 1), (1, 2), (1, 3)]),
        ("HEAL",   [(2, 2), (2, 1), (1, 0), (0, 0)]),
        ("GOA",    [(0, 2), (1, 1), (1, 0)]),
    ]
    print("[test] Showing for 15s...")
    wins = show_solutions_nonblocking(bounds, fake, top_n=5)
    NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(15))
    for w in wins:
        w.close()
    print("[test] Done.")
