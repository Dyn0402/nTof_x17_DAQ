#!/usr/bin/env python3
"""Level-type veto test. RunCtrl must be running (DREAM busy at ~298 Hz under the
10 kHz pulse). We toggle the .245 pulse ON/OFF; a sustained-LEVEL veto on .244
section D produces edges only at the busy assert/de-assert transitions, which the
edge counter catches even though continuous counting saw zero.

Sweeps section-D input standard/threshold/impedance to cover unknown polarity.
Counter covers SDK lemo 0-3 = panel 1,2,3,4 (veto targets panel 1,4 = SDK 0,3)."""
import time
from n1081b_sdk import N1081B

Std=N1081B.SignalStandard; Imp=N1081B.SignalImpedance; Fn=N1081B.FunctionType
D=N1081B.Section.SEC_D; PC=N1081B.Section.SEC_C

pulse=N1081B("128.141.177.245"); pulse.connect(); pulse.ws.settimeout(6)
def set_pulse(on):
    pulse.configure_pulse_generator(PC, N1081B.StatisticMode.STAT_DETERMINISTIC, 20, 100000, on,on,on,on)

d=N1081B("192.168.10.244"); d.connect(); d.ws.settimeout(8); d.login("password")

SETTINGS=[("DISCR",150,"high"),("DISCR",-150,"high"),("DISCR",150,"50"),
          ("DISCR",-150,"50"),("NIM",0,"50"),("TTL",0,"high")]
STDMAP={"NIM":Std.STANDARD_NIM,"TTL":Std.STANDARD_TTL,"DISCR":Std.STANDARD_DISCRIMINATOR}
IMPMAP={"50":Imp.IMPEDANCE_50,"high":Imp.IMPEDANCE_HIGH}
CYCLES=3

def counts(): return {c['lemo']:c['value'] for c in d.get_function_results(D)['data']['counters']}

set_pulse(True); time.sleep(1)
for sname,thr,iname in SETTINGS:
    d.set_input_configuration(D, STDMAP[sname], STDMAP[sname], thr, IMPMAP[iname])
    d.set_section_function(D, Fn.FN_COUNTER)
    d.configure_counter(D, True, True, True, True, False)
    d.stop_acquisition(D, Fn.FN_COUNTER); d.start_acquisition(D, Fn.FN_COUNTER)
    base=counts()
    phase_log=[]
    for cyc in range(CYCLES):
        set_pulse(False); time.sleep(2.5); off=counts()   # busy de-asserts
        set_pulse(True);  time.sleep(2.5); on=counts()    # busy asserts
        phase_log.append((off,on))
    final=counts()
    delta={k:final[k]-base[k] for k in final}
    total=sum(v for v in delta.values())
    tag = "  <<< SIGNAL!" if total>0 else ""
    print(f"[{sname:5} th={thr:+5} {iname:4}] {CYCLES} cycles, delta per-lemo(panel1-4)={delta} total={total}{tag}")

set_pulse(True)  # leave pulse on
d.disconnect(); pulse.disconnect()
print("done (pulse left ON)")
