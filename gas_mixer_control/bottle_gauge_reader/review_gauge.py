"""Review argon bottle gauge photos by hand and build a pressure-vs-time table.

Automatic reading of these dials is unreliable enough that the number always
wants a human glance anyway (see ``gauge_reader.py`` and the README). So this
inverts the arrangement: **you** are the reader, and the automatic guess is
demoted to a default that is usually right and always visible next to the dial
it came from.

Guesses are **pre-computed**, not made while you wait — reading a dial takes
around ten seconds, which is unbearable between key presses but fine as a batch
you run once. So the workflow is two steps:

    # 1. once per new batch of photos (slow, unattended)
    python gauge_reader.py PHOTOS/ --csv guesses.csv --debug guesses/ \
        --assume-upright

    # 2. review them (instant — reads the guesses and overlays from step 1)
    python review_gauge.py PHOTOS/ --guesses guesses.csv --overlays guesses/ \
        --csv bottle_pressure.csv

Step 2 shows the dial with the fitted scale drawn over the printed one, the
capture time, and the guess pre-filled in an entry box. Press **Enter** to
accept, type a number and Enter to correct it, **Esc** to reject the photo. That
is the whole loop. Photos with no pre-computed guess still appear, just with an
empty box for you to fill in — nothing is skipped silently.

The CSV is rewritten after every decision, so an interrupted session loses
nothing, and re-running skips photos already decided (``--redo`` to revisit).

Output columns: ``timestamp, bar, psi, status, source, file``. ``source`` is
``auto`` when you accepted the guess unchanged and ``manual`` when you typed the
number; rejected photos are kept as ``status=rejected`` with no pressure, purely
so you are not asked about them again. The pressure-vs-time table printed at the
end is just the ``status=ok`` rows.
"""
import argparse
import csv
import datetime as dt
import os
import sys
import tkinter as tk
from tkinter import ttk

import numpy as np
from PIL import Image, ImageTk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gauge_reader as gr        # noqa: E402  (needs the path set above)

FIELDS = ["timestamp", "bar", "psi", "status", "source", "file"]
VIEW_W, VIEW_H = 1000, 680


# --------------------------------------------------------------------------- #
# Pre-computed guesses
# --------------------------------------------------------------------------- #
def load_guesses(csv_path):
    """{basename: bar} from a gauge_reader --csv run. Missing file is fine."""
    if not csv_path or not os.path.exists(csv_path):
        return {}
    out = {}
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                out[row["file"]] = float(row["bar"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def find_overlay(overlay_dir, photo):
    """The `<stem>_overlay.jpg` gauge_reader --debug writes, if it exists."""
    if not overlay_dir:
        return None
    stem = os.path.splitext(os.path.basename(photo))[0]
    p = os.path.join(overlay_dir, stem + "_overlay.jpg")
    return p if os.path.exists(p) else None


def load_display(photo, overlay_dir, want_full):
    """PIL image to show: the annotated overlay by default, else the photo."""
    path = None if want_full else find_overlay(overlay_dir, photo)
    img = Image.open(path or photo)
    if path is None:
        img = _apply_exif(img)
    else:
        img = _zoom_to_marks(img)
    img.thumbnail((VIEW_W, VIEW_H), Image.LANCZOS)
    return img


def _zoom_to_marks(img):
    """Crop an overlay to the annotated dial.

    In a wide shot the dial is a small part of the frame, and judging whether the
    magenta scale sits on the printed one is exactly the fine detail that gets
    lost. The annotation is drawn in pure magenta, which appears nowhere on a
    steel gas panel, so its bounding box locates the dial for free.
    """
    a = np.asarray(img.convert("RGB")).astype(int)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    marks = (R > 150) & (B > 150) & (G < 100)
    ys, xs = np.nonzero(marks)
    if len(xs) < 20:
        return img
    pad = int(0.10 * max(np.ptp(xs), np.ptp(ys)) + 12)
    box = (max(0, xs.min() - pad), max(0, ys.min() - pad),
           min(img.width, xs.max() + pad), min(img.height, ys.max() + pad))
    return img.crop(box)


def _apply_exif(img):
    try:
        from PIL import ImageOps
        return ImageOps.exif_transpose(img)
    except Exception:
        return img


# --------------------------------------------------------------------------- #
# Output CSV
# --------------------------------------------------------------------------- #
def load_csv(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, newline="") as fh:
        return {r["file"]: r for r in csv.DictReader(fh) if r.get("file")}


def save_csv(path, rows):
    """Rewrite the whole file, sorted by time, via a temp file + rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        # lineterminator: csv defaults to CRLF, which puts a stray \r on the last
        # field of every row and trips up every non-Python reader of this file.
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows.values(), key=lambda r: r["timestamp"]):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# The reviewer window
# --------------------------------------------------------------------------- #
class Reviewer:
    def __init__(self, root, photos, rows, csv_path, guesses, overlay_dir):
        self.root, self.photos, self.rows, self.csv_path = root, photos, rows, csv_path
        self.guesses, self.overlay_dir = guesses, overlay_dir
        self.i = 0
        self.full_frame = False
        self._photo_ref = None

        root.title("Argon bottle gauge review")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self.header = ttk.Label(root, font=("TkDefaultFont", 12, "bold"))
        self.header.grid(row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        self.canvas = tk.Label(root, background="#1c1c1c")
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=12)

        self.guess_lbl = ttk.Label(root, font=("TkDefaultFont", 11))
        self.guess_lbl.grid(row=2, column=0, sticky="w", padx=12, pady=(8, 0))

        bar = ttk.Frame(root)
        bar.grid(row=3, column=0, sticky="ew", padx=12, pady=10)
        ttk.Label(bar, text="Pressure (bar):").pack(side="left")
        vcmd = (root.register(lambda s: s == "" or _is_number(s)), "%P")
        self.entry = ttk.Entry(bar, width=10, font=("TkDefaultFont", 13),
                               validate="key", validatecommand=vcmd)
        self.entry.pack(side="left", padx=(6, 14))
        ttk.Button(bar, text="Accept  (Enter)", command=self.accept).pack(side="left")
        ttk.Button(bar, text="Reject  (Esc)", command=self.reject).pack(side="left", padx=6)
        ttk.Button(bar, text="Back  (Alt+←)", command=self.back).pack(side="left")
        ttk.Button(bar, text="Full frame  (F2)", command=self.toggle_view).pack(side="left", padx=6)
        ttk.Button(bar, text="Finish", command=self.finish).pack(side="right")

        self.status = ttk.Label(root, foreground="#666")
        self.status.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 10))

        root.bind("<Return>", lambda e: self.accept())
        root.bind("<KP_Enter>", lambda e: self.accept())
        root.bind("<Escape>", lambda e: self.reject())
        root.bind("<F2>", lambda e: self.toggle_view())
        root.bind("<Alt-Left>", lambda e: self.back())
        root.protocol("WM_DELETE_WINDOW", self.finish)

        self.show()

    # -- display ------------------------------------------------------------ #
    def show(self):
        if self.i >= len(self.photos):
            return self.finish()
        photo = self.photos[self.i]
        name = os.path.basename(photo)
        ts = gr.photo_timestamp(photo)
        self.header.config(text=f"[{self.i + 1}/{len(self.photos)}]  {name}    "
                                f"{ts:%a %d %b %Y  %H:%M}")

        try:
            img = load_display(photo, self.overlay_dir, self.full_frame)
            self._photo_ref = ImageTk.PhotoImage(img)
            self.canvas.config(image=self._photo_ref, text="", width=0, height=0)
        except Exception as exc:
            self._photo_ref = None
            self.canvas.config(image="", text=f"could not open image:\n{exc}",
                               foreground="#d66", width=80, height=20)

        guess = self.guesses.get(name)
        has_overlay = find_overlay(self.overlay_dir, photo) is not None
        if guess is None:
            self.guess_lbl.config(
                text="No pre-computed guess for this photo — read the dial and type "
                     "the value.", foreground="#b00")
        elif self.full_frame or not has_overlay:
            self.guess_lbl.config(text=f"Guess: {guess:.0f} bar "
                                       f"({guess * gr.PSI_PER_BAR:.0f} psi)",
                                  foreground="#333")
        else:
            self.guess_lbl.config(
                text=f"Guess: {guess:.0f} bar ({guess * gr.PSI_PER_BAR:.0f} psi).  "
                     f"Magenta = the fitted scale — if it does not sit on the dial's "
                     f"printed scale, the guess is wrong.", foreground="#333")

        self.entry.delete(0, tk.END)
        if guess is not None:
            self.entry.insert(0, f"{guess:.0f}")
        self.entry.focus_set()
        self.entry.selection_range(0, tk.END)

        done = sum(1 for r in self.rows.values() if r.get("status") == "ok")
        self.status.config(text=f"{done} recorded • writing to {self.csv_path}")

    def toggle_view(self):
        self.full_frame = not self.full_frame
        self.show()

    # -- decisions ---------------------------------------------------------- #
    def _record(self, bar, status, source):
        photo = self.photos[self.i]
        ts = gr.photo_timestamp(photo)
        self.rows[os.path.basename(photo)] = {
            "timestamp": ts.isoformat(timespec="seconds"),
            "bar": "" if bar is None else f"{bar:.1f}",
            "psi": "" if bar is None else f"{bar * gr.PSI_PER_BAR:.0f}",
            "status": status,
            "source": source,
            "file": os.path.basename(photo),
        }
        save_csv(self.csv_path, self.rows)
        self.i += 1
        self.show()

    def accept(self):
        if self.i >= len(self.photos):
            return
        text = self.entry.get().strip()
        if not text:
            self.status.config(text="Type a pressure, or press Esc to reject this photo.")
            return
        bar = float(text)
        if not 0 <= bar <= gr.BAR_FULL_SCALE:
            self.status.config(
                text=f"Out of range — the dial only reads 0–{gr.BAR_FULL_SCALE:.0f} bar.")
            return
        guess = self.guesses.get(os.path.basename(self.photos[self.i]))
        unchanged = guess is not None and abs(bar - guess) < 0.5
        self._record(bar, "ok", "auto" if unchanged else "manual")

    def reject(self):
        if self.i < len(self.photos):
            self._record(None, "rejected", "")

    def back(self):
        if self.i > 0:
            self.i -= 1
            self.show()

    def finish(self):
        save_csv(self.csv_path, self.rows)
        self.root.quit()
        self.root.destroy()


def _is_number(s):
    try:
        float(s)
        return True
    except ValueError:
        return False


# --------------------------------------------------------------------------- #
def print_table(rows):
    ok = sorted((r for r in rows.values() if r.get("status") == "ok"),
                key=lambda r: r["timestamp"])
    if not ok:
        print("\nNo readings recorded.")
        return
    print(f"\n{'date':<17}{'bar':>7}{'psi':>8}   source")
    print("-" * 46)
    for r in ok:
        ts = dt.datetime.fromisoformat(r["timestamp"])
        print(f"{ts:%Y-%m-%d %H:%M}{float(r['bar']):>7.0f}"
              f"{float(r['psi']):>8.0f}   {r['source']}")
    first, last = ok[0], ok[-1]
    days = (dt.datetime.fromisoformat(last["timestamp"])
            - dt.datetime.fromisoformat(first["timestamp"])).total_seconds() / 86400.0
    if days > 0:
        rate = (float(last["bar"]) - float(first["bar"])) / days
        print("-" * 46)
        print(f"{len(ok)} readings over {days:.1f} d: {float(first['bar']):.0f} -> "
              f"{float(last['bar']):.0f} bar ({rate:+.2f} bar/day mean)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+", help="photo files and/or directories")
    ap.add_argument("--csv", default="bottle_pressure.csv",
                    help="table to build (default: %(default)s)")
    ap.add_argument("--guesses", default="guesses.csv",
                    help="CSV from a gauge_reader run (default: %(default)s)")
    ap.add_argument("--overlays", default="guesses",
                    help="directory of gauge_reader --debug overlays "
                         "(default: %(default)s)")
    ap.add_argument("--redo", action="store_true",
                    help="also review photos already in the CSV")
    args = ap.parse_args(argv)

    photos = list(gr.iter_images(args.paths))
    rows = load_csv(args.csv)
    if not args.redo:
        photos = [p for p in photos if os.path.basename(p) not in rows]
    photos.sort(key=gr.photo_timestamp)

    guesses = load_guesses(args.guesses)
    overlay_dir = args.overlays if os.path.isdir(args.overlays) else None
    if not guesses:
        print(f"No guesses loaded from {args.guesses} — reviewing with an empty box "
              f"for every photo.\nTo pre-compute them:\n"
              f"  python gauge_reader.py PHOTOS/ --csv {args.guesses} "
              f"--debug {args.overlays} --assume-upright")

    if not photos:
        print("Nothing to review — every photo is already in the CSV "
              "(--redo to revisit).")
        print_table(rows)
        return 0

    root = tk.Tk()
    Reviewer(root, photos, rows, args.csv, guesses, overlay_dir)
    root.mainloop()
    print_table(load_csv(args.csv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
