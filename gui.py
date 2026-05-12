"""
Floating control panel for Word Hunt Assistant.
Models preload in the background; click Start to capture + solve.
Paths are drawn directly on the 4x4 grid canvas.
Arrow keys (global, via NSEvent monitor) cycle through words.
"""

import math
import threading
import tkinter as tk

PALETTE_HEX = ["#ff4747", "#32d25a", "#4696ff", "#ffc81e", "#ff821e"]
WORD_HUNT_POINTS = {3: 100, 4: 400, 5: 800, 6: 1400, 7: 1800, 8: 2200, 9: 2600}

CELL = 58
GAP  = 5


def _points(word: str) -> int:
    n = len(word)
    return WORD_HUNT_POINTS.get(n, 3000 if n >= 10 else 100)


def _stars(word: str) -> str:
    filled = max(1, min(5, len(word) - 2))
    return "★" * filled + "☆" * (5 - filled)


def _cell_center(row: int, col: int) -> tuple[int, int]:
    return GAP + col * (CELL + GAP) + CELL // 2, GAP + row * (CELL + GAP) + CELL // 2


def _cell_bbox(row: int, col: int) -> tuple[int, int, int, int]:
    x0 = GAP + col * (CELL + GAP)
    y0 = GAP + row * (CELL + GAP)
    return x0, y0, x0 + CELL, y0 + CELL


CANVAS_SIZE = 4 * CELL + 5 * GAP


class WordHuntApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Word Hunt")
        self.root.resizable(False, False)
        self.root.attributes("-topmost", True)

        self._letters: list[list[str]] = [["·"] * 4 for _ in range(4)]
        self._words: list[tuple[str, list]] = []   # ALL deduplicated words
        self._current_idx: int = 0
        self._key_proc = None

        self._build_ui()
        self._start_global_hotkeys()
        threading.Thread(target=self._preload, daemon=True).start()
        self.root.mainloop()

    # ------------------------------------------------------------------
    # UI layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        PAD = 10
        root = self.root

        # ── Status bar ────────────────────────────────────────────────
        sf = tk.Frame(root)
        sf.pack(fill="x", padx=PAD, pady=(PAD, 4))
        self._dot = tk.Label(sf, text="●", fg="#f5a623", font=("Helvetica", 13))
        self._dot.pack(side="left")
        self.status_var = tk.StringVar(value="Loading models…")
        tk.Label(sf, textvariable=self.status_var,
                 font=("Helvetica", 12)).pack(side="left", padx=6)

        # ── Grid canvas ───────────────────────────────────────────────
        gf = tk.LabelFrame(root, text="Grid", padx=GAP, pady=GAP)
        gf.pack(padx=PAD, pady=4, fill="x")
        self.canvas = tk.Canvas(gf, width=CANVAS_SIZE, height=CANVAS_SIZE,
                                bg="#f0f0f0", highlightthickness=0)
        self.canvas.pack()
        self._draw_grid_empty()

        # ── Current word info + navigation ────────────────────────────
        nf = tk.Frame(root)
        nf.pack(padx=PAD, pady=(2, 2), fill="x")
        self.prev_btn = tk.Button(nf, text="◀", font=("Helvetica", 12),
                                  relief="flat", state="disabled",
                                  command=self._prev_word)
        self.prev_btn.pack(side="left")
        self.word_info_var = tk.StringVar(value="")
        tk.Label(nf, textvariable=self.word_info_var,
                 font=("Menlo", 13, "bold"), width=22).pack(side="left", expand=True)
        self.next_btn = tk.Button(nf, text="▶", font=("Helvetica", 12),
                                  relief="flat", state="disabled",
                                  command=self._next_word)
        self.next_btn.pack(side="right")
        self.nav_idx_var = tk.StringVar(value="")
        tk.Label(nf, textvariable=self.nav_idx_var,
                 font=("Helvetica", 10), fg="#888888").pack(side="right", padx=4)

        # ── Word list (scrollable, all words) ─────────────────────────
        wf = tk.LabelFrame(root, text="Words", padx=6, pady=4)
        wf.pack(padx=PAD, pady=4, fill="both", expand=True)

        sb = tk.Scrollbar(wf, orient="vertical")
        self.listbox = tk.Listbox(
            wf, yscrollcommand=sb.set,
            font=("Menlo", 11), height=8,
            selectmode="browse", activestyle="none",
            exportselection=False,
        )
        sb.config(command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.pack(side="left", fill="both", expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_listbox_select)

        # ── Start button ──────────────────────────────────────────────
        bf = tk.Frame(root)
        bf.pack(fill="x", padx=PAD, pady=(6, PAD))
        self.start_btn = tk.Button(
            bf, text="Start", state="disabled",
            font=("Helvetica", 14, "bold"),
            bg="#007aff", fg="white",
            activebackground="#0062cc", activeforeground="white",
            relief="flat", padx=16, pady=6, cursor="hand2",
            command=self.on_start,
        )
        self.start_btn.pack(fill="x")

        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = root.winfo_reqwidth(), root.winfo_reqheight()
        root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    # ------------------------------------------------------------------
    # Grid drawing
    # ------------------------------------------------------------------

    def _draw_grid_empty(self):
        c = self.canvas
        c.delete("all")
        mono = ("Menlo", 17, "bold")
        for r in range(4):
            for col in range(4):
                x0, y0, x1, y1 = _cell_bbox(r, col)
                c.create_rectangle(x0, y0, x1, y1, fill="#e0e0e0", outline="#cccccc", width=1)
                cx, cy = _cell_center(r, col)
                c.create_text(cx, cy, text=self._letters[r][col], font=mono, fill="#555555")

    def _draw_path(self, path: list, color: str):
        c = self.canvas
        c.delete("all")
        mono = ("Menlo", 17, "bold")
        start = path[0] if path else None

        for r in range(4):
            for col in range(4):
                x0, y0, x1, y1 = _cell_bbox(r, col)
                if (r, col) == start:
                    fill, outline, lcolor, bw = self._lighten(color), color, "#222222", 2
                else:
                    fill, outline, lcolor, bw = "#e0e0e0", "#cccccc", "#555555", 1
                c.create_rectangle(x0, y0, x1, y1, fill=fill, outline=outline, width=bw)
                cx, cy = _cell_center(r, col)
                c.create_text(cx, cy, text=self._letters[r][col], font=mono, fill=lcolor)

        for i in range(len(path) - 1):
            r1, c1 = path[i]
            r2, c2 = path[i + 1]
            x1, y1 = _cell_center(r1, c1)
            x2, y2 = _cell_center(r2, c2)
            dx, dy = x2 - x1, y2 - y1
            dist = math.hypot(dx, dy)
            if dist == 0:
                continue
            shrink = 6
            sx = x1 + dx / dist * shrink
            sy = y1 + dy / dist * shrink
            ex = x2 - dx / dist * shrink
            ey = y2 - dy / dist * shrink
            c.create_line(sx, sy, ex, ey, fill=color, width=3,
                          arrow=tk.LAST, arrowshape=(10, 12, 4))

    @staticmethod
    def _lighten(hex_color: str, amount: float = 0.45) -> str:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"#{int(r+(255-r)*amount):02x}{int(g+(255-g)*amount):02x}{int(b+(255-b)*amount):02x}"

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _show_word(self, idx: int):
        if not self._words:
            return
        idx = max(0, min(idx, len(self._words) - 1))
        self._current_idx = idx
        word, path = self._words[idx]
        color = PALETTE_HEX[idx % len(PALETTE_HEX)]

        self._draw_path(path, color)
        self.word_info_var.set(f"{word}  {_stars(word)}  {_points(word):,}pts")
        self.nav_idx_var.set(f"{idx + 1}/{len(self._words)}")
        self.prev_btn.config(state="normal" if idx > 0 else "disabled")
        self.next_btn.config(state="normal" if idx < len(self._words) - 1 else "disabled")

        # Sync listbox selection without triggering the select callback
        self.listbox.selection_clear(0, tk.END)
        self.listbox.selection_set(idx)
        self.listbox.see(idx)

    def _prev_word(self):
        self._show_word(self._current_idx - 1)

    def _next_word(self):
        self._show_word(self._current_idx + 1)

    def _on_listbox_select(self, _event):
        sel = self.listbox.curselection()
        if sel:
            self._show_word(sel[0])

    # ------------------------------------------------------------------
    # Global arrow-key hotkeys
    # Runs the NSEvent monitor in a child process to avoid GIL conflicts
    # between AppKit's event thread and tkinter's main loop.
    # ------------------------------------------------------------------

    def _start_global_hotkeys(self):
        import subprocess, sys

        _MONITOR_SCRIPT = """
import sys
from AppKit import NSEvent, NSKeyDownMask, NSApplication
from AppKit import NSApplicationActivationPolicyProhibited
from Foundation import NSRunLoop, NSDate

app = NSApplication.sharedApplication()
app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)

def _handler(event):
    code = event.keyCode()
    if code == 124:
        sys.stdout.write("R\\n"); sys.stdout.flush()
    elif code == 123:
        sys.stdout.write("L\\n"); sys.stdout.flush()

NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSKeyDownMask, _handler)

while True:
    NSRunLoop.mainRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
"""

        try:
            self._key_proc = subprocess.Popen(
                [sys.executable, "-c", _MONITOR_SCRIPT],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )

            def _read_loop():
                for line in self._key_proc.stdout:
                    ch = line.strip()
                    if ch == "R":
                        self.root.after(0, self._next_word)
                    elif ch == "L":
                        self.root.after(0, self._prev_word)

            threading.Thread(target=_read_loop, daemon=True).start()
            print("[hotkey] Global arrow-key monitor active (← / →)")
        except Exception as exc:
            print(f"[hotkey] Subprocess monitor failed: {exc}. Falling back to window-focus bindings.")

        # Also bind locally for when the GUI window has focus
        self.root.bind("<Left>",  lambda _: self._prev_word())
        self.root.bind("<Right>", lambda _: self._next_word())

    # ------------------------------------------------------------------
    # Preloading
    # ------------------------------------------------------------------

    def _preload(self):
        self._set_status("Loading wordbank…", "orange")
        from solver import preload as preload_words
        preload_words()
        self._set_status("Loading OCR model…", "orange")
        from grid import preload_ocr
        preload_ocr()
        self.root.after(0, self._on_ready)

    def _on_ready(self):
        self._set_status("Ready", "green")
        self.start_btn.config(state="normal")

    # ------------------------------------------------------------------
    # Start pipeline
    # ------------------------------------------------------------------

    def on_start(self):
        self.start_btn.config(state="disabled")
        self._words = []
        self.listbox.delete(0, tk.END)
        self._draw_grid_empty()
        self.word_info_var.set("")
        self.nav_idx_var.set("")
        self.prev_btn.config(state="disabled")
        self.next_btn.config(state="disabled")
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            from capture import capture_phone_mirroring
            self._set_status("Capturing…", "blue")
            img, _ = capture_phone_mirroring()

            self._set_status("Recognizing grid…", "blue")
            from grid import parse_grid
            letters = parse_grid(img, save_debug=True)

            self._set_status("Solving…", "blue")
            from solver import solve
            solutions = solve(letters)

            self.root.after(0, lambda: self._show_results(solutions, letters))
        except Exception as exc:
            self.root.after(0, lambda e=exc: self._on_error(e))

    def _show_results(self, solutions, letters):
        self._letters = letters

        # Deduplicate all words (solver already sorts longest-first)
        seen: set[str] = set()
        all_words: list[tuple] = []
        for word, path in solutions:
            if word not in seen:
                seen.add(word)
                all_words.append((word, path))
        self._words = all_words

        # Populate scrollable list
        self.listbox.delete(0, tk.END)
        for i, (word, _) in enumerate(all_words):
            entry = f"  {word:<10}  {_stars(word)}  {_points(word):,} pts"
            self.listbox.insert(tk.END, entry)
            self.listbox.itemconfig(i, fg=PALETTE_HEX[i % len(PALETTE_HEX)])

        n = len(all_words)
        self._set_status(f"{len(solutions)} paths · {n} words", "green")
        self._show_word(0)
        self.start_btn.config(state="normal")

    def _on_error(self, exc: Exception):
        self._set_status(f"Error: {exc}", "red")
        self.start_btn.config(state="normal")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _set_status(self, text: str, color: str = "gray"):
        colors = {
            "green": "#34c759", "orange": "#f5a623",
            "blue": "#007aff", "red": "#ff3b30", "gray": "#8e8e93",
        }
        hx = colors.get(color, color)
        self.root.after(0, lambda: (self._dot.config(fg=hx), self.status_var.set(text)))


def launch():
    WordHuntApp()


if __name__ == "__main__":
    launch()
