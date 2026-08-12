#!/usr/bin/env python3
import os
import time
from caen_hv_py.CAENHVController import CAENHVController

# Credentials come from hv_creds.txt (gitignored), same file run_config_beam.Config
# reads: line 1 = username, line 2 = password. Resolved relative to the repo root so
# this works from any cwd.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_REPO, 'hv_creds.txt')) as _f:
    _creds = _f.read().splitlines()
USER, PASS = _creds[0].strip(), _creds[1].strip()

IP = '128.141.177.244'
N = 10

print('--- Rapid reconnect (no sleep) ---')
times = []
for i in range(N):
    t0 = time.perf_counter()
    with CAENHVController(IP, USER, PASS):
        pass
    dt = time.perf_counter() - t0
    times.append(dt)
    print(f'  Login {i+1:2d}: {dt*1000:.1f} ms')

print(f'\nMin:  {min(times)*1000:.1f} ms')
print(f'Max:  {max(times)*1000:.1f} ms')
print(f'Mean: {sum(times)/len(times)*1000:.1f} ms')

print('\n--- Monitor holds connection open, set_hvs tries second login after 1s ---')
with CAENHVController(IP, USER, PASS) as monitor_hv:
    print('  Monitor logged in, waiting 1s...')
    time.sleep(1.0)
    t0 = time.perf_counter()
    with CAENHVController(IP, USER, PASS) as set_hv:
        dt = time.perf_counter() - t0
        print(f'  set_hvs login: {dt*1000:.1f} ms  (success if no "Start bad" above)')
