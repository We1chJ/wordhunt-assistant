"""
Finds the iPhone Mirroring window on Mac and captures a screenshot of it.
"""
import sys
import cv2
import numpy as np
from PIL import Image

try:
    import Quartz
except ImportError:
    sys.exit("Missing: pip install pyobjc-framework-Quartz")

try:
    import mss
except ImportError:
    sys.exit("Missing: pip install mss")


def get_phone_mirroring_bounds() -> dict | None:
    """Return logical-pixel bounds of the iPhone Mirroring window, or None."""
    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly
        | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    for win in window_list:
        owner = win.get("kCGWindowOwnerName", "")
        if "iPhone Mirroring" in owner:
            b = win.get("kCGWindowBounds", {})
            return {
                "x": int(b.get("X", 0)),
                "y": int(b.get("Y", 0)),
                "w": int(b.get("Width", 0)),
                "h": int(b.get("Height", 0)),
            }
    return None


def capture_phone_mirroring() -> tuple[np.ndarray, dict]:
    """
    Locate iPhone Mirroring window and return (BGR numpy image, bounds dict).
    The image is at physical resolution (2x on Retina displays).
    """
    bounds = get_phone_mirroring_bounds()
    if bounds is None:
        raise RuntimeError(
            "iPhone Mirroring window not found. "
            "Open the app and make sure it is visible on screen."
        )
    print(
        f"[capture] Found iPhone Mirroring  "
        f"x={bounds['x']} y={bounds['y']} "
        f"w={bounds['w']} h={bounds['h']}"
    )

    with mss.mss() as sct:
        monitor = {
            "left": bounds["x"],
            "top": bounds["y"],
            "width": bounds["w"],
            "height": bounds["h"],
        }
        grab = sct.grab(monitor)
        img_rgb = Image.frombytes("RGB", grab.size, grab.bgra, "raw", "BGRX")
        img_bgr = np.array(img_rgb)[..., ::-1].copy()  # RGB → BGR for OpenCV

    print(f"[capture] Screenshot size: {img_bgr.shape[1]}×{img_bgr.shape[0]} px")
    from datetime import datetime
    filename = datetime.now().strftime("debug_capture_%Y%m%d_%H%M%S.png")
    cv2.imwrite(filename, img_bgr)
    print(f"[capture] Saved → {filename}")
    return img_bgr, bounds


if __name__ == "__main__":
    import cv2

    img, _ = capture_phone_mirroring()
    out = "debug_capture.png"
    cv2.imwrite(out, img)
    print(f"[capture] Saved → {out}")
