"""
Interactive template builder for Word Hunt letter recognition.

Usage:
    python build_templates.py                  # uses debug_capture.png
    python build_templates.py my_screenshot.png

For each cell in the 4x4 grid it shows you the tile and asks you to
type the letter. Saves each crop to templates/<LETTER>.png.
Already-saved letters are skipped unless you pass --overwrite.
Run this across a few screenshots until all 26 letters are collected.
"""

import sys
import shutil
from pathlib import Path

import cv2
import numpy as np
from grid import GRID_X1, GRID_Y1, GRID_X2, GRID_Y2, GRID_ROWS, GRID_COLS, _region_to_grid

TEMPLATES_DIR = Path(__file__).parent / "templates"
TEMPLATES_DIR.mkdir(exist_ok=True)

OVERWRITE = "--overwrite" in sys.argv
image_path = next((a for a in sys.argv[1:] if not a.startswith("--")), "debug_capture.png")

img = cv2.imread(image_path)
if img is None:
    sys.exit(f"Could not load: {image_path}")

already_saved = {p.stem for p in TEMPLATES_DIR.glob("*.png")}
still_needed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ") - (set() if OVERWRITE else already_saved)

if not still_needed:
    print("All 26 templates already saved. Pass --overwrite to redo.")
    sys.exit(0)

print(f"\nLoaded: {image_path}")
print(f"Templates saved so far: {sorted(already_saved) or 'none'}")
print(f"Still needed: {sorted(still_needed)}")
print("\nFor each tile: type the letter and press Enter. Leave blank to skip. Ctrl+C to quit.\n")

grid_layout = _region_to_grid(GRID_X1, GRID_Y1, GRID_X2, GRID_Y2)
ZOOM = 8  # upscale factor for display

for row in grid_layout:
    for (x, y, w, h) in row:
        cell = img[y:y+h, x:x+w]
        zoomed = cv2.resize(cell, (w * ZOOM, h * ZOOM), interpolation=cv2.INTER_NEAREST)

        bordered = cv2.copyMakeBorder(zoomed, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=(40, 40, 40))

        cv2.imshow("Cell — type the letter in the terminal", bordered)
        cv2.waitKey(1)

        while True:
            raw = input("  Letter (blank=skip): ").strip().upper()
            if raw == "":
                print("  Skipped.")
                break
            if len(raw) == 1 and raw.isalpha():
                if raw not in still_needed and not OVERWRITE:
                    print(f"  {raw} already saved — skipping (use --overwrite to replace).")
                    break
                out_path = TEMPLATES_DIR / f"{raw}.png"
                cv2.imwrite(str(out_path), cell)
                already_saved.add(raw)
                still_needed.discard(raw)
                print(f"  Saved → templates/{raw}.png  "
                      f"({len(already_saved)}/26 collected, "
                      f"still need: {sorted(still_needed) or 'all done!'})")
                break
            print("  Please type a single letter A-Z, or leave blank to skip.")

cv2.destroyAllWindows()
print(f"\nDone. {len(already_saved)}/26 templates collected.")
if still_needed:
    print(f"Still missing: {sorted(still_needed)}")
    print("Run again on another screenshot to collect the rest.")
