#!/usr/bin/env python3
import time
from caen_hv_py.CAENHVController import CAENHVController

IP = '128.141.177.244'
USER = 'user'
PASS = '***REMOVED***'
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
