#!/usr/bin/env python3
"""Add a hand-read argon bottle pressure to the log, without a photo.

`update.sh` is the photo path: drop images in, the reader guesses, you confirm.
When the gauge was read off the panel and written on paper instead, there is no
photo to review -- this adds the row directly.

    python3 add_reading.py                          # interactive, one per line
    python3 add_reading.py "2026-08-03 14:20" 122   # or straight from the args
    python3 add_reading.py "2026-08-03 14:20" 122 "2026-08-05 09:10" 118

Rows land with `source=manual-entry` (as opposed to `manual`, which means a
photo was on screen and the number was typed over the reader's guess) and a
`manual:<timestamp>` placeholder in the `file` column. That placeholder is not
decoration: `review_gauge.py` keys its rows by filename and silently drops any
row without one, so a blank there would delete these readings the next time
photos were reviewed.

Written to the same file `update.sh` maintains, so both paths can be used in
any order. Commit the CSV afterwards.
"""
import argparse
import csv
import datetime as dt
import os
import sys

FIELDS = ["timestamp", "bar", "psi", "status", "source", "file"]
CSV_DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "argon_bottle_pressure.csv")

# Unit conversion, and the dial's clockwise end. Same values as the reader's
# gauge_reader.PSI_PER_BAR / BAR_FULL_SCALE, restated rather than imported:
# that module pulls in OpenCV, which this script has no use for.
PSI_PER_BAR = 14.5037738
BAR_FULL_SCALE = 315.0

TIME_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M", "%Y-%m-%d"]


def parse_time(text):
    """Accept the formats a person actually types. A bare date means midnight,
    which is fine here: the model only cares where a reading falls relative to
    the others, and readings are days apart."""
    text = text.strip()
    for fmt in TIME_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"unrecognised date/time {text!r} -- try '2026-08-03 14:20'")


def parse_bar(text):
    bar = float(text)
    if not 0.0 <= bar <= BAR_FULL_SCALE:
        raise ValueError(f"{bar:g} bar is off the dial (0-{BAR_FULL_SCALE:g})")
    return bar


def load_rows(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def save_rows(path, rows):
    """Rewrite sorted by time, via a temp file + rename so an interrupted write
    cannot truncate the log."""
    tmp = path + ".tmp"
    with open(tmp, "w", newline="") as fh:
        # csv defaults to CRLF, which would leave a stray \r on every line of a
        # file the rest of the toolchain reads as plain text.
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        for r in sorted(rows, key=lambda r: r["timestamp"]):
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, path)


def make_row(when, bar):
    stamp = when.strftime("%Y-%m-%dT%H:%M:%S")
    return {"timestamp": stamp, "bar": f"{bar:.1f}", "psi": f"{bar * PSI_PER_BAR:.0f}",
            "status": "ok", "source": "manual-entry", "file": f"manual:{stamp}"}


def add(rows, when, bar, force=False):
    """Insert one reading. Returns a message describing what happened."""
    row = make_row(when, bar)
    clash = next((r for r in rows if r["timestamp"] == row["timestamp"]), None)
    if clash and not force:
        return (f"  skipped {row['timestamp']} -- already logged at "
                f"{clash['bar']} bar (--force to replace)")
    if clash:
        rows.remove(clash)
    rows.append(row)

    # A reading ABOVE the one before it means the bottle gained pressure, which
    # it cannot do while being drawn from -- so it is either a misread gauge or
    # a bottle that was swapped. The latter invalidates the mole-count anchor in
    # bottle_usage.py, so it must not pass unremarked.
    earlier = [r for r in rows if r["timestamp"] < row["timestamp"]
               and r.get("status") == "ok" and r.get("bar")]
    note = ""
    if earlier:
        prev = max(earlier, key=lambda r: r["timestamp"])
        if bar > float(prev["bar"]) + 1.0:
            note = (f"\n    NOTE: higher than {prev['bar']} bar on {prev['timestamp']}."
                    f" Misread, or was the bottle swapped? A swap needs a new anchor"
                    f" in bottle_usage.py, not just this row.")
    return f"  added {row['timestamp']}  {bar:.1f} bar ({row['psi']} psi){note}"


def interactive(rows, force):
    print("Enter readings as '<date/time> <bar>', e.g. 2026-08-03 14:20 122")
    print("Blank line when done.\n")
    added = 0
    while True:
        try:
            line = input("reading> ").strip()
        except EOFError:
            print()
            break
        if not line:
            break
        parts = line.rsplit(None, 1)
        if len(parts) != 2:
            print("  need a date/time AND a pressure, e.g. 2026-08-03 14:20 122")
            continue
        try:
            when, bar = parse_time(parts[0]), parse_bar(parts[1])
        except ValueError as e:
            print(f"  {e}")
            continue
        print(add(rows, when, bar, force))
        added += 1
    return added


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs", nargs="*", metavar="TIME BAR",
                    help="date/time and pressure pairs; omit for interactive entry")
    ap.add_argument("--csv", default=CSV_DEFAULT, help="pressure log to update")
    ap.add_argument("--force", action="store_true",
                    help="replace an existing reading with the same timestamp")
    args = ap.parse_args(argv)

    if len(args.pairs) % 2:
        ap.error("arguments come in TIME BAR pairs -- quote timestamps that "
                 "contain a space")

    rows = load_rows(args.csv)
    before = len(rows)
    if args.pairs:
        for raw_t, raw_bar in zip(args.pairs[::2], args.pairs[1::2]):
            try:
                print(add(rows, parse_time(raw_t), parse_bar(raw_bar), args.force))
            except ValueError as e:
                print(f"  {e}", file=sys.stderr)
                return 1
    else:
        interactive(rows, args.force)

    if len(rows) == before:
        print("\nNothing added.")
        return 0
    save_rows(args.csv, rows)
    ok = [r for r in rows if r.get("status") == "ok" and r.get("bar")]
    print(f"\n{args.csv}: {len(ok)} readings, latest "
          f"{max(ok, key=lambda r: r['timestamp'])['bar']} bar")
    print("The bottle-usage model re-fits on its next read (Gas card / "
          "/gas/bottle_usage). Commit the CSV when it looks right.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
