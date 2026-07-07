#!/usr/bin/env python3
"""Monitor .244 section D (panel LEMO 1,4,5 = SDK lemo 0,3,4) via Time Tag,
watching for veto/busy edges from the TCM. Tunable input standard/threshold/imp.

Usage: veto_monitor.py <standard NIM|TTL|DISCR> <threshold_mV> <imp 50|high> <seconds>
Prints periodic per-lemo edge counts; dumps the first raw packet to learn schema.
"""
import sys, time, socket, collections
from n1081b_sdk import N1081B
try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception): pass
IDLE = (socket.timeout, WebSocketTimeoutException)

STD = {"NIM": N1081B.SignalStandard.STANDARD_NIM,
       "TTL": N1081B.SignalStandard.STANDARD_TTL,
       "DISCR": N1081B.SignalStandard.STANDARD_DISCRIMINATOR}
IMP = {"50": N1081B.SignalImpedance.IMPEDANCE_50,
       "high": N1081B.SignalImpedance.IMPEDANCE_HIGH}

standard = STD[sys.argv[1]]
threshold = int(sys.argv[2])
imp = IMP[sys.argv[3]]
secs = float(sys.argv[4]) if len(sys.argv) > 4 else 30.0

IP = "192.168.10.244"
SEC = N1081B.Section.SEC_D
# enable SDK lemo 0,3,4 (panel 1,4,5); 1,2,5 off
ENABLES = (True, False, False, True, True, False)

d = N1081B(IP)
d.connect(); d.ws.settimeout(6)
print("login:", d.login("password"))
r = d.set_input_configuration(SEC, standard, standard, threshold, imp)
print("set_input:", r.get("Result") if isinstance(r, dict) else r,
      f"(std={sys.argv[1]} th={threshold} imp={sys.argv[3]})")
d.set_section_function(SEC, N1081B.FunctionType.FN_TIME_TAG)
d.configure_time_tagging(SEC, *ENABLES)
d.stop_acquisition(SEC, N1081B.FunctionType.FN_TIME_TAG)
d.start_acquisition(SEC, N1081B.FunctionType.FN_TIME_TAG)
print("acquisition started; enabled SDK lemo 0,3,4 = panel 1,4,5")

d.ws.settimeout(1.0)
counts = collections.Counter()
total = 0
first_dumped = False
t0 = time.time(); last = t0
while time.time() - t0 < secs:
    try:
        pkt = d.get_time_tag_data()
    except IDLE:
        pkt = None
    except Exception as e:
        print("recv err:", repr(e)); continue
    if pkt:
        if not first_dumped:
            print("FIRST PACKET raw (up to 5 elems):", pkt[:5])
            first_dumped = True
        for el in pkt:
            total += 1
            # best-guess channel key
            if isinstance(el, dict):
                ch = el.get("lemo", el.get("channel", el.get("ch", "?")))
            elif isinstance(el, (list, tuple)):
                ch = el[0]
            else:
                ch = el
            counts[ch] += 1
    now = time.time()
    if now - last >= 2.0:
        print(f"[t+{now-t0:5.1f}s] total_edges={total} per-lemo={dict(counts)}")
        last = now
try:
    d.stop_acquisition(SEC, N1081B.FunctionType.FN_TIME_TAG)
except Exception:
    pass
d.disconnect()
print(f"DONE total_edges={total} per-lemo={dict(counts)}")
