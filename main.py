"""
Entry point — captures iPhone Mirroring and parses the Word Hunt grid.

Usage:
    python main.py                    # live capture from iPhone Mirroring
    python main.py debug_capture.png  # test against a saved image
"""
import sys
import cv2
from grid import parse_grid, print_grid


def main():
    print("=== Word Hunt Assistant ===\n")

    if len(sys.argv) > 1:
        path = sys.argv[1]
        img = cv2.imread(path)
        if img is None:
            sys.exit(f"Could not load image: {path}")
        print(f"[1/2] Using saved image: {path}")
    else:
        from capture import capture_phone_mirroring
        print("[1/2] Capturing iPhone Mirroring window...")
        try:
            img, _ = capture_phone_mirroring()
        except RuntimeError as e:
            sys.exit(f"Error: {e}")

    print("\n[2/2] Detecting and parsing grid...")
    letters = parse_grid(img, save_debug=True)

    print("\nDetected grid:")
    print_grid(letters)
    print("\nFlat list (row by row):", [l for row in letters for l in row])


if __name__ == "__main__":
    main()
