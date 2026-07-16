#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Emergency HV off — power off the HV channels this DAQ controls.

Fired by the Shift Overview page's Emergency Stop button (flask_app/app.py).
Opens its OWN session to the CAEN crate (a second session alongside
hv_control.py's is fine) and powers off only the channels listed in the run
config detectors' hv_channels — not the whole crate, so anything others might
be running on other cards is untouched.

Must be run with cwd = repo root: Config() reads hv_creds.txt by relative path.
"""

import sys
from datetime import datetime

from run_config_beam import Config
from caen_hv_py.CAENHVController import CAENHVController
from caen_hv_py.exceptions import CAENHVError


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] EMERGENCY HV OFF starting")
    config = Config()
    hv_info = config.hv_info

    # Channels we control = the run config detectors' hv_channels (slots/channels
    # for drift + resist of each included detector).
    included = set(getattr(config, 'included_detectors', []) or [])
    channels = []
    for det in config.detectors:
        if included and det.get('name') not in included:
            continue
        for electrode, ch in (det.get('hv_channels') or {}).items():
            try:
                slot, channel = ch
            except (TypeError, ValueError):
                continue
            channels.append((f"{det['name']}_{electrode}", int(slot), int(channel)))

    if not channels:
        print("No hv_channels found in run config — nothing to power off")
        return 1

    failures = 0
    with CAENHVController(hv_info['ip'], hv_info['username'], hv_info['password']) as caen_hv:
        for label, slot, channel in channels:
            try:
                power = caen_hv.get_ch_power(slot, channel)
                if power == 1:
                    caen_hv.set_ch_pw(slot, channel, 0)
                    print(f"  OFF  {label} ({slot}:{channel})")
                else:
                    print(f"  already off  {label} ({slot}:{channel})")
            except CAENHVError as e:
                # Don't let one dead channel abort the rest of the shutdown.
                failures += 1
                print(f"  FAILED  {label} ({slot}:{channel}): {e}")

    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] EMERGENCY HV OFF done "
          f"({len(channels) - failures}/{len(channels)} ok)")
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
