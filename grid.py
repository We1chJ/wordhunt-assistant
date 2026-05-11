"""
Detects the 4x4 Word Hunt letter grid from a screenshot and OCRs each cell.

Detection strategy:
  1. Convert to HSV, mask highly-saturated colored blobs (the tiles).
  2. Filter contours to tile-sized, roughly-square shapes.
  3. From however many candidates we find, compute the dominant grid spacing
     using pairwise distances between blob centers, then extrapolate all 16
     cell positions — works even if some tiles were missed.
  4. Fall back to saved calibration or interactive prompt if detection fails.

OCR per cell:
  - Isolate the white letter via brightness threshold.
  - Run Tesseract in single-char mode with A-Z whitelist.
"""

import json
import sys
from pathlib import Path

import cv2
import numpy as np
import Quartz
import Vision

CALIBRATION_FILE = Path(__file__).parent / "calibration.json"
GRID_ROWS, GRID_COLS = 4, 4

# Known grid corners (relative to the iPhone Mirroring screenshot).
# Top-left and bottom-right of the 4×4 letter grid.
GRID_X1, GRID_Y1 = 49, 324
GRID_X2, GRID_Y2 = 266, 544


def _region_to_grid(x1: int, y1: int, x2: int, y2: int) -> list[list[tuple]]:
    """Divide a rectangle into a GRID_ROWS × GRID_COLS array of (x,y,w,h) cells."""
    cell_w = (x2 - x1) // GRID_COLS
    cell_h = (y2 - y1) // GRID_ROWS
    return [
        [(x1 + col * cell_w, y1 + row * cell_h, cell_w, cell_h)
         for col in range(GRID_COLS)]
        for row in range(GRID_ROWS)
    ]


# ---------------------------------------------------------------------------
# Grid detection
# ---------------------------------------------------------------------------

def _colored_blob_mask(bgr: np.ndarray) -> np.ndarray:
    """Binary mask of pixels belonging to brightly-colored tiles."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (0, 60, 60), (180, 255, 255))
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,  k, iterations=2)
    return mask


def _blob_centers(bgr: np.ndarray) -> list[tuple[float, float, float]]:
    """
    Return (cx, cy, size) for each candidate tile blob.
    size = sqrt(area), used to estimate tile dimensions later.
    """
    img_area = bgr.shape[0] * bgr.shape[1]
    mask = _colored_blob_mask(bgr)
    cv2.imwrite("debug_mask.png", mask)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    blobs = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        area = w * h
        if area < img_area * 0.0005 or area > img_area * 0.06:
            continue
        aspect = w / h if h else 0
        if not (0.5 < aspect < 2.0):
            continue
        blobs.append((x + w / 2, y + h / 2, (area ** 0.5)))
    return blobs


def _dominant_spacing(values: list[float], min_spacing: float) -> float | None:
    """
    Given a list of 1-D positions, find the most common gap between them
    that is >= min_spacing. Returns None if nothing reliable found.
    Uses a histogram with bin width = 5% of min_spacing.
    """
    diffs = []
    sv = sorted(values)
    for i in range(len(sv)):
        for j in range(i + 1, len(sv)):
            d = sv[j] - sv[i]
            if d >= min_spacing:
                diffs.append(d)
    if not diffs:
        return None

    bin_w = max(1.0, min_spacing * 0.05)
    n_bins = int(max(diffs) / bin_w) + 1
    hist = np.zeros(n_bins)
    for d in diffs:
        hist[int(d / bin_w)] += 1

    # Weight by 1/multiplier so we prefer the fundamental period, not harmonics
    for i in range(n_bins):
        for m in range(2, 5):
            j = int(i * m)
            if j < n_bins:
                hist[i] += hist[j] / m

    best_bin = int(np.argmax(hist))
    return (best_bin + 0.5) * bin_w


def auto_detect_grid(bgr: np.ndarray) -> list[list[tuple]] | None:
    """
    Attempt to auto-detect the 4×4 grid.
    Returns a 4×4 list of (x, y, w, h) bounding boxes, or None on failure.

    Works in three stages:
      1. Find colored blobs and their centers.
      2. Compute the dominant horizontal and vertical grid spacing from
         all pairwise distances — doesn't require exactly 16 blobs.
      3. Snap blob centers to the nearest grid position, pick the 4×4
         region with the most coverage, and extrapolate any missing cells.
    """
    blobs = _blob_centers(bgr)
    print(f"[grid] Colored blobs found: {len(blobs)}")

    if len(blobs) < 4:
        print("[grid] Too few blobs for auto-detection.")
        return None

    xs = [b[0] for b in blobs]
    ys = [b[1] for b in blobs]
    sizes = [b[2] for b in blobs]
    median_size = float(np.median(sizes))

    # Minimum plausible tile spacing = half the median tile size
    min_gap = median_size * 0.5

    step_x = _dominant_spacing(xs, min_gap)
    step_y = _dominant_spacing(ys, min_gap)

    if step_x is None or step_y is None:
        print(f"[grid] Could not determine grid spacing (step_x={step_x}, step_y={step_y}).")
        return None

    print(f"[grid] Detected tile spacing: x={step_x:.1f}px  y={step_y:.1f}px")

    # Snap every blob to a grid coordinate
    # grid_coord = round(position / step)
    def snap(pos, step):
        return round(pos / step)

    snapped = [(snap(cx, step_x), snap(cy, step_y), cx, cy) for cx, cy, _ in blobs]

    # Find the 4×4 window of grid coords with the most blobs
    gxs = [s[0] for s in snapped]
    gys = [s[1] for s in snapped]
    best_origin = None
    best_count = 0
    for ox in range(min(gxs), max(gxs) - GRID_COLS + 2):
        for oy in range(min(gys), max(gys) - GRID_ROWS + 2):
            count = sum(
                1 for gx, gy, _, _ in snapped
                if ox <= gx < ox + GRID_COLS and oy <= gy < oy + GRID_ROWS
            )
            if count > best_count:
                best_count = count
                best_origin = (ox, oy)

    if best_origin is None or best_count < 4:
        print(f"[grid] Could not find a plausible 4×4 window (best coverage: {best_count}/16).")
        return None

    ox, oy = best_origin
    print(f"[grid] Grid window origin ({ox},{oy}), coverage {best_count}/16")

    # Build a lookup: (grid_col, grid_row) → actual pixel center
    lookup: dict[tuple, tuple] = {}
    for gx, gy, cx, cy in snapped:
        col, row = gx - ox, gy - oy
        if 0 <= col < GRID_COLS and 0 <= row < GRID_ROWS:
            lookup[(col, row)] = (cx, cy)

    # Estimate the grid origin in pixel space from known blob positions
    if lookup:
        sample_col, sample_row = next(iter(lookup))
        sample_cx, sample_cy = lookup[(sample_col, sample_row)]
        origin_px_x = sample_cx - sample_col * step_x
        origin_px_y = sample_cy - sample_row * step_y
    else:
        return None

    cell_w = int(step_x)
    cell_h = int(step_y)
    half_w = cell_w // 2
    half_h = cell_h // 2

    grid: list[list[tuple]] = []
    for row in range(GRID_ROWS):
        cols = []
        for col in range(GRID_COLS):
            cx = int(origin_px_x + col * step_x)
            cy = int(origin_px_y + row * step_y)
            cols.append((cx - half_w, cy - half_h, cell_w, cell_h))
        grid.append(cols)

    print("[grid] Auto-detection succeeded.")
    return grid


# ---------------------------------------------------------------------------
# Calibration fallback
# ---------------------------------------------------------------------------

def calibrate(bgr: np.ndarray) -> list[list[tuple]]:
    """
    Interactive calibration: show the screenshot and ask the user to enter
    the pixel coordinates of the top-left and bottom-right corners of the grid.
    Saves result to calibration.json.
    """
    cv2.imwrite("debug_capture.png", bgr)
    print("\n[calibrate] Saved screenshot to debug_capture.png")
    print("[calibrate] Open that image, find the 4×4 letter grid,")
    print("            and enter the pixel coordinates of its corners.\n")

    x1 = int(input("  Grid top-left  X: "))
    y1 = int(input("  Grid top-left  Y: "))
    x2 = int(input("  Grid bottom-right X: "))
    y2 = int(input("  Grid bottom-right Y: "))

    cell_w = (x2 - x1) // GRID_COLS
    cell_h = (y2 - y1) // GRID_ROWS

    grid = []
    for row in range(GRID_ROWS):
        cols = []
        for col in range(GRID_COLS):
            cx = x1 + col * cell_w
            cy = y1 + row * cell_h
            cols.append((cx, cy, cell_w, cell_h))
        grid.append(cols)

    data = {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
    CALIBRATION_FILE.write_text(json.dumps(data, indent=2))
    print(f"[calibrate] Saved → {CALIBRATION_FILE}")
    return grid


def load_calibration() -> list[list[tuple]] | None:
    """Load grid layout from saved calibration file, or return None."""
    if not CALIBRATION_FILE.exists():
        return None
    data = json.loads(CALIBRATION_FILE.read_text())
    x1, y1, x2, y2 = data["x1"], data["y1"], data["x2"], data["y2"]
    cell_w = (x2 - x1) // GRID_COLS
    cell_h = (y2 - y1) // GRID_ROWS
    grid = []
    for row in range(GRID_ROWS):
        cols = []
        for col in range(GRID_COLS):
            cx = x1 + col * cell_w
            cy = y1 + row * cell_h
            cols.append((cx, cy, cell_w, cell_h))
        grid.append(cols)
    print(f"[grid] Loaded calibration from {CALIBRATION_FILE}")
    return grid


# ---------------------------------------------------------------------------
# Template matching
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent / "templates"
_templates: dict[str, np.ndarray] = {}
_templates_loaded = False


def _isolate_letter(gray: np.ndarray) -> np.ndarray:
    """Threshold to binary, then keep only the component closest to center."""
    _, mask = cv2.threshold(gray, 80, 255, cv2.THRESH_BINARY_INV)
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 1:
        return mask
    h, w = mask.shape
    cx, cy = w / 2, h / 2
    best = min(range(1, n), key=lambda i: (centroids[i][0]-cx)**2 + (centroids[i][1]-cy)**2)
    result = np.zeros_like(mask)
    result[labels == best] = 255
    return result


def _letter_mask(bgr: np.ndarray, size: int = 32) -> np.ndarray:
    """Isolate the letter (no frame), resize to square for comparison."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    letter = _isolate_letter(gray)
    return cv2.resize(letter, (size, size), interpolation=cv2.INTER_AREA)


def _load_templates() -> None:
    _templates.clear()
    for path in TEMPLATES_DIR.glob("*.png"):
        letter = path.stem.upper()
        if len(letter) == 1 and letter.isalpha():
            _templates[letter] = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    print(f"[ocr] Loaded {len(_templates)}/26 templates: {sorted(_templates)}")


def _match_template(cell_bgr: np.ndarray) -> tuple[str, float]:
    """Compare filtered cell against all templates. Returns (letter, score 0–1)."""
    cell_sig = _letter_mask(cell_bgr).astype(np.float32)
    best_letter, best_score = "?", -1.0
    for letter, tmpl in _templates.items():
        tmpl_sig = cv2.resize(tmpl, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
        score = float(cv2.matchTemplate(cell_sig, tmpl_sig, cv2.TM_CCOEFF_NORMED)[0, 0])
        if score > best_score:
            best_score = score
            best_letter = letter
    return best_letter, best_score


# ---------------------------------------------------------------------------
# OCR — Apple Vision (fallback)
# ---------------------------------------------------------------------------

def _bgr_to_cgimage(bgr: np.ndarray) -> object:
    """Convert a BGR numpy array to a CGImage for use with Vision."""
    rgb = bgr[..., ::-1].copy()  # BGR → RGB
    h, w = rgb.shape[:2]
    data = rgb.tobytes()
    color_space = Quartz.CGColorSpaceCreateDeviceRGB()
    provider = Quartz.CGDataProviderCreateWithData(None, data, len(data), None)
    return Quartz.CGImageCreate(
        w, h, 8, 24, w * 3,
        color_space,
        Quartz.kCGBitmapByteOrderDefault | Quartz.kCGImageAlphaNone,
        provider, None, False,
        Quartz.kCGRenderingIntentDefault,
    )


def _vision_ocr(cell_bgr: np.ndarray) -> str:
    """Apple Vision fallback for letters without a template."""
    scale = max(1, 128 // cell_bgr.shape[0])
    if scale > 1:
        cell_bgr = cv2.resize(cell_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    cg_image = _bgr_to_cgimage(cell_bgr)
    results: list[str] = []

    def handler(request, error):
        if error or not request.results():
            return
        for obs in request.results():
            candidates = obs.topCandidates_(1)
            if candidates:
                results.append(candidates[0].string())

    request = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(handler)
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(False)
    request.setCustomWords_(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    img_handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, {})
    img_handler.performRequests_error_([request], None)

    text = "".join(results).strip().upper()
    for ch in text:
        if ch.isalpha():
            return ch
    return "?"


def ocr_cell(cell_bgr: np.ndarray) -> str:
    """Template match first, Vision fallback for low confidence or missing templates."""
    global _templates_loaded
    if not _templates_loaded:
        _load_templates()
        _templates_loaded = True

    if _templates:
        letter, score = _match_template(cell_bgr)
        if score > 0.6:
            return letter
        vision_result = _vision_ocr(cell_bgr)
        print(f"[ocr] Low confidence ({score:.2f}) for '{letter}' → Vision: '{vision_result}'")
        return vision_result

    return _vision_ocr(cell_bgr)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def get_grid_layout(bgr: np.ndarray) -> list[list[tuple]]:
    """
    Return the 4×4 grid layout (each cell as (x,y,w,h)).
    Uses the hardcoded GRID_X/Y constants directly — no auto-detection needed.
    """
    print(f"[grid] Using hardcoded region ({GRID_X1},{GRID_Y1}) → ({GRID_X2},{GRID_Y2})")
    return _region_to_grid(GRID_X1, GRID_Y1, GRID_X2, GRID_Y2)


def parse_grid(bgr: np.ndarray, save_debug: bool = True) -> list[list[str]]:
    """
    Full pipeline: screenshot → 4×4 letter array.
    Returns e.g. [['A','B','C','D'], ['E','F','G','H'], ...]
    """
    grid_layout = get_grid_layout(bgr)

    letters: list[list[str]] = []
    debug = bgr.copy() if save_debug else None

    for row_idx, row in enumerate(grid_layout):
        letter_row = []
        for col_idx, (x, y, w, h) in enumerate(row):
            cell = bgr[y: y + h, x: x + w]
            letter = ocr_cell(cell)
            letter_row.append(letter)

            if save_debug and debug is not None:
                cv2.rectangle(debug, (x, y), (x + w, y + h), (0, 255, 0), 2)
                cv2.putText(debug, letter, (x + 4, y + h - 6),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

        letters.append(letter_row)

    if save_debug and debug is not None:
        cv2.imwrite("debug_grid.png", debug)
        print("[grid] Annotated grid saved → debug_grid.png")

    return letters


def print_grid(letters: list[list[str]]) -> None:
    print("\n+---+---+---+---+")
    for row in letters:
        print("| " + " | ".join(row) + " |")
        print("+---+---+---+---+")


if __name__ == "__main__":
    from capture import capture_phone_mirroring

    img, _ = capture_phone_mirroring()
    grid = parse_grid(img)
    print_grid(grid)
