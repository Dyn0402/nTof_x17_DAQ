#!/usr/bin/env python3
"""Measure .244 section D for any veto activity. Called while RunCtrl runs at 10 kHz.
LA is triggered on the trigger pulse (sec1_in1 rising), so its 2048-sample window sits
right at/after each trigger -- exactly where a per-trigger veto (Trig_Conf_TrigVetoLen)
would show. Reports section D panel[1..6] high-sample counts at + and - polarity, plus a
3 s edge-counter tally. Arg1 = label."""
import sys, time
from n1081b_sdk import N1081B
Std=N1081B.SignalStandard; Imp=N1081B.SignalImpedance; Fn=N1081B.FunctionType
LA=N1081B.LogicAnalyzerTriggerMode; ED=N1081B.LogicAnalyzerTriggerEdge
label=sys.argv[1] if len(sys.argv)>1 else "?"
d=N1081B("192.168.10.244"); d.connect(); d.ws.settimeout(8); d.login("password")
D=N1081B.Section.SEC_D

def la_secD(thr):
    d.set_input_configuration(D, Std.STANDARD_DISCRIMINATOR, Std.STANDARD_DISCRIMINATOR, thr, Imp.IMPEDANCE_50)
    flags=[False]*40; flags[0]=True  # trigger on sec1_in1 (the trigger pulse) rising
    d.set_logic_analyzer_trigger(LA.LA_TRIGGER_OR, LA.LA_TRIGGER_OFF, ED.LA_EDGE_RISING, *flags)
    d.start_logic_analyzer(); time.sleep(0.5)
    inp=d.get_logic_analyzer_data()['data']['inputs']
    return [sum(inp[18+(p-1)]) for p in range(1,7)]  # secD panel 1..6, /2048

def counter_secD(thr, secs=3):
    d.set_input_configuration(D, Std.STANDARD_DISCRIMINATOR, Std.STANDARD_DISCRIMINATOR, thr, Imp.IMPEDANCE_50)
    d.set_section_function(D, Fn.FN_COUNTER); d.configure_counter(D, True,True,True,True, False)
    d.stop_acquisition(D, Fn.FN_COUNTER); d.start_acquisition(D, Fn.FN_COUNTER)
    b={c['lemo']:c['value'] for c in d.get_function_results(D)['data']['counters']}
    time.sleep(secs)
    a={c['lemo']:c['value'] for c in d.get_function_results(D)['data']['counters']}
    return {k:a[k]-b[k] for k in a}

pos=la_secD(150); neg=la_secD(-150)
cnt=counter_secD(150)
print(f"[{label}] LA secD panel[1-6] /2048  +150mV:{pos}  -150mV:{neg}   counter(3s,+150,panel1-4):{cnt}")
d.disconnect()
