#!/usr/bin/env python3
"""
Test CAEN HV crate simultaneous connection limit.
Uses multiprocessing so each connection gets its own C library state,
accurately simulating independent clients (CAENGECO, hv_control, etc.).

Run when NO other processes are connected to the crate.
"""
import os
import time
import multiprocessing as mp
from caen_hv_py.CAENHVController import CAENHVController

# Credentials come from hv_creds.txt (gitignored), same file run_config_beam.Config
# reads: line 1 = username, line 2 = password. Resolved relative to the repo root so
# this works from any cwd.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(_REPO, 'hv_creds.txt')) as _f:
    _creds = _f.read().splitlines()
USER, PASS = _creds[0].strip(), _creds[1].strip()

IP = '128.141.177.244'
HOLD_SECONDS = 4  # how long each process holds its connection open


def try_connect(index, result_queue):
    t0 = time.perf_counter()
    try:
        with CAENHVController(IP, USER, PASS) as hv:
            dt = time.perf_counter() - t0
            power = hv.get_ch_power(5, 0)  # real call to confirm handle is valid
            if power < 0 or power > 1:  # sanity check — power should be 0 or 1
                result_queue.put((index, 'bad_handle', dt, f'power={power}'))
            else:
                result_queue.put((index, 'ok', dt, f'power={power}'))
                time.sleep(HOLD_SECONDS)
    except Exception as e:
        dt = time.perf_counter() - t0
        result_queue.put((index, 'exception', dt, str(e)))


def test_n_simultaneous(n):
    print(f'\n--- {n} simultaneous connection(s) ---')
    result_queue = mp.Queue()
    procs = [mp.Process(target=try_connect, args=(i, result_queue)) for i in range(n)]

    for p in procs:
        p.start()
    for p in procs:
        p.join()

    results = {}
    while not result_queue.empty():
        index, status, dt, extra = result_queue.get()
        results[index] = (status, dt, extra)

    ok_count = 0
    for i in range(n):
        if i not in results:
            print(f'  Connection {i+1}: no result (process may have crashed)')
            continue
        status, dt, extra = results[i]
        if status == 'ok':
            ok_count += 1
            print(f'  Connection {i+1}: OK        ({dt*1000:.0f} ms, {extra})')
        elif status == 'bad_handle':
            print(f'  Connection {i+1}: BAD HANDLE ({dt*1000:.0f} ms, {extra})')
        else:
            print(f'  Connection {i+1}: FAIL       ({dt*1000:.0f} ms, {extra})')

    print(f'  --> {ok_count}/{n} succeeded')
    return ok_count


if __name__ == '__main__':
    print('CAEN HV simultaneous connection limit test')
    print(f'Each connection held open for {HOLD_SECONDS}s to force overlap.')
    print('Make sure NO other clients (CAENGECO, hv_control.py) are connected.\n')

    input('Press Enter to begin...')

    for n in range(1, 6):
        ok = test_n_simultaneous(n)
        time.sleep(3)  # let crate settle between tests
        if ok < n:
            print(f'\nLimit is {ok} simultaneous connection(s).')
            break
    else:
        print('\nAll tests passed — limit is at least 5.')
