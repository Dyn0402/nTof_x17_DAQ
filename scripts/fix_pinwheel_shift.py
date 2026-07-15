#!/usr/bin/env python3
"""One-off fix: halve the tangential pinwheel shift in saved run_config.json
det_center_coords for mx17_A/B/C/D, matching the run_config_beam.py correction.

Old -> new (exact match required before overwriting):
  mx17_A x: -32.7  -> -16.35
  mx17_B z: -31.5  -> -15.75
  mx17_C x:  34.6  ->  17.3
  mx17_D z:  31.0  ->  15.5
"""
import glob
import json
import os

RUNS_GLOB = '/mnt/data/x17/beam_july/runs/run_*/run_config.json'

FIXES = {
    'mx17_A': ('x', -32.7, -16.35),
    'mx17_B': ('z', -31.5, -15.75),
    'mx17_C': ('x', 34.6, 17.3),
    'mx17_D': ('z', 31.0, 15.5),
}


def main():
    paths = sorted(glob.glob(RUNS_GLOB), key=lambda p: int(p.split('/')[-2].split('_')[1]))
    changed, skipped = [], []

    for path in paths:
        with open(path) as f:
            data = json.load(f)

        run_changes = []
        for det in data.get('detectors', []):
            name = det.get('name')
            if name not in FIXES:
                continue
            axis, old, new = FIXES[name]
            coords = det.get('det_center_coords', {})
            current = coords.get(axis)
            if current == old:
                coords[axis] = new
                run_changes.append(f'{name}.{axis}: {old} -> {new}')
            elif current == new:
                pass  # already fixed
            else:
                skipped.append(f'{path}: {name}.{axis} = {current} (neither old nor new value, left untouched)')

        if run_changes:
            backup = path + '.bak_pinwheel_fix'
            if not os.path.exists(backup):
                with open(backup, 'w') as f:
                    json.dump(json.load(open(path)), f, indent=2)
            with open(path, 'w') as f:
                json.dump(data, f, indent=2)
            changed.append((path, run_changes))

    print(f'Fixed {len(changed)} files:')
    for path, run_changes in changed:
        print(f'  {path}')
        for c in run_changes:
            print(f'    {c}')

    if skipped:
        print(f'\nWARNING: {len(skipped)} unexpected values left untouched:')
        for s in skipped:
            print(f'  {s}')


if __name__ == '__main__':
    main()
