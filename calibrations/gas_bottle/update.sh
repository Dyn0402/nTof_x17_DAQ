#!/usr/bin/env bash
# Add new argon bottle gauge photos to the pressure log.
#
# Drop the new photos into $PHOTOS, run this, review them, commit the CSV.
# Photos already in the CSV are skipped, so it is safe to re-run at any time.
set -euo pipefail

PHOTOS="${PHOTOS:-$HOME/x17/gas/bottle_pressure_photos}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CSV="$REPO/calibrations/gas_bottle/argon_bottle_pressure.csv"
READER="$REPO/gas_mixer_control/bottle_gauge_reader"
PY="$REPO/.venv/bin/python"

[[ -x "$PY" ]] || { echo "no venv python at $PY" >&2; exit 1; }
[[ -d "$PHOTOS" ]] || { echo "no photo directory at $PHOTOS" >&2; exit 1; }

cd "$PHOTOS"

# Step 1 — pre-compute guesses for photos that do not have an overlay yet.
# Reading a dial takes ~10 s, so this is the slow, unattended half. The reader
# appends nothing; it rewrites guesses.csv from whatever it is given, so it is
# only handed the photos still missing an overlay.
mkdir -p guesses
shopt -s nullglob nocaseglob
new=()
for f in *.jpg *.jpeg *.png; do
    [[ -e "guesses/${f%.*}_overlay.jpg" ]] && continue          # already guessed
    # Exact match on the last CSV field, not a substring search — filenames are
    # full of regex metacharacters.
    # (a bare `exit 0` here would fall through to END and be overwritten)
    awk -F, -v f="$f" 'NR>1 {sub(/\r$/, "", $NF); if ($NF==f) found=1} END {exit !found}' \
        "$CSV" 2>/dev/null \
        && continue                                             # already reviewed
    new+=("$f")
done
shopt -u nocaseglob

if (( ${#new[@]} )); then
    echo "pre-computing guesses for ${#new[@]} new photo(s) — this takes ~10 s each"
    "$PY" "$READER/gauge_reader.py" "${new[@]}" \
        --csv guesses_new.csv --debug guesses/ --assume-upright || true
    # Merge the new guesses into the running guesses.csv (header once).
    if [[ -f guesses_new.csv ]]; then
        [[ -f guesses.csv ]] || head -1 guesses_new.csv > guesses.csv
        tail -n +2 guesses_new.csv >> guesses.csv
        rm -f guesses_new.csv
    fi
else
    echo "no new photos to pre-compute"
fi

# Step 2 — review. Instant: reads the guesses and overlays from step 1.
"$PY" "$READER/review_gauge.py" . --csv "$CSV" \
    --guesses guesses.csv --overlays guesses

echo
echo "Log updated: $CSV"
echo "Commit it:   cd $REPO && git add calibrations/gas_bottle && git commit"
