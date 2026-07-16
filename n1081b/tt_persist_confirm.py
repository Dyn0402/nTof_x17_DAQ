#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONFIRMATION: persistent-FIFO multi-section dead-time readout on .244 (B & C).

Mechanism found in the first test:
  - start_tt_data ALONE -> 0 tags.
  - reset_channel + start_tt_data DUMPS the section's accumulated FIFO backlog
    (does NOT clear it first); FIFO fills continuously while section is in TT,
    even when not the actively-tapped stream.

This run proves the two things that make the scheme usable:
  (1) INDEPENDENCE / CONCURRENCY: after flushing both B & C to a common start t0
      and waiting DT with neither tapped, drain B (takes tB wall seconds) then
      drain C. If C's timestamp span covers DT + tB (i.e. C kept accumulating
      while B was being read), sections accumulate independently & concurrently.
  (2) ATTRIBUTION: draining B returns only B-connected panels, C only C's.
  Also probes FIFO DEPTH via a long dead time (does span saturate below DT?).

One persistent connection, paced. Touches only B & C. Restores counters + counting.
"""
import json, time, sys, socket
from n1081b_sdk import N1081B
try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception): pass
IDLE=(socket.timeout, WebSocketTimeoutException)
IP="192.168.10.244"; PW="password"
SEC={s.name[-1]:s for s in N1081B.Section}
B=SEC["B"]; C=SEC["C"]
TT=N1081B.FunctionType.FN_TIME_TAG; CNT=N1081B.FunctionType.FN_COUNTER
NS_PER_S=1e8
OUT={}

def raw(d,o): d.ws.send(json.dumps(o))
def tap(d,sec):   # reset + start (the sequence that actually flushes)
    raw(d,{"command":"reset_channel","callback":"s","params":{"section":sec.value,"channel":1}})
    raw(d,{"command":"start_tt_data","callback":"s","params":{"section":sec.value}})
def untap(d,sec):
    raw(d,{"command":"stop_tt_data","callback":"s","params":{"section":sec.value}})

def drain_until_quiet(d, max_s, quiet_s=0.45):
    """Drain the whole backlog: stop after quiet_s with no new tags (or max_s)."""
    d.ws.settimeout(0.2); tags=[]; t0=time.time(); last=time.time()
    while time.time()-t0 < max_s:
        try: r=d.ws.recv()
        except IDLE:
            if tags and (time.time()-last)>quiet_s: break
            continue
        except Exception: break
        try: p=json.loads(r.replace(",]","]"))
        except Exception: continue
        if p.get("command")=="send_data":
            new=[[e[0],e[1]] for e in p.get("timetag_data",[]) if isinstance(e,list) and len(e)>=2]
            if new: tags+=new; last=time.time()
    return tags, round(time.time()-t0,3)

def summ(tags):
    if not tags: return {"n":0}
    ts=[t[1] for t in tags]; per={}
    for pn,_ in tags: per[pn]=per.get(pn,0)+1
    return {"n":len(tags),"panels":sorted(per),"per_panel":per,
            "t_min":min(ts),"t_max":max(ts),"span_s":round((max(ts)-min(ts))/NS_PER_S,3)}

def flush(d,sec,label):
    tap(d,sec); tg,w=drain_until_quiet(d,3.0); untap(d,sec)
    time.sleep(0.15); drain_until_quiet(d,0.4)
    return summ(tg)

def main():
    d=N1081B(IP); assert d.connect(); d.ws.settimeout(6); assert d.login(PW)
    try:
        # arm B & C in TT (paid once)
        for s in (B,C):
            d.set_section_function(s,TT); time.sleep(0.1)
            d.configure_time_tagging(s,True,True,True,True,True,True); time.sleep(0.1)

        # ---- flush stale backlog on both so t0 is a clean start ----
        OUT["flushA_B"]=flush(d,B,"B"); OUT["flushA_C"]=flush(d,C,"C")
        print(f"[flush] B={OUT['flushA_B']['n']} C={OUT['flushA_C']['n']} (cleared)",file=sys.stderr)

        # ============ TRIAL 1: DT=3s, prove independence + attribution ============
        DT=3.0
        print(f"[T1] dead {DT}s, both un-tapped...",file=sys.stderr)
        time.sleep(DT)
        # read B (measure how long B's readout takes)
        wall_before_B=time.time()
        tap(d,B); tgB,wB=drain_until_quiet(d,3.0); untap(d,B); time.sleep(0.1); drain_until_quiet(d,0.3)
        wall_B_readout=round(time.time()-wall_before_B,3)
        # read C right after
        tap(d,C); tgC,wC=drain_until_quiet(d,3.0); untap(d,C); time.sleep(0.1); drain_until_quiet(d,0.3)
        sB=summ(tgB); sC=summ(tgC)
        OUT["T1_DT_s"]=DT
        OUT["T1_B"]=sB; OUT["T1_B_readout_wall_s"]=wall_B_readout
        OUT["T1_C"]=sC
        OUT["T1_interpretation"]={
            "B_span_vs_DT":f"{sB.get('span_s')} vs {DT} (expect ~DT if accumulated un-tapped)",
            "C_span_vs_DT_plus_Bread":f"{sC.get('span_s')} vs {round(DT+wall_B_readout,2)} "
               f"(expect ~DT+B_readout if C kept accumulating while B was read => INDEPENDENT)",
        }
        print(f"[T1] B n={sB.get('n')} span={sB.get('span_s')}s | "
              f"C n={sC.get('n')} span={sC.get('span_s')}s (DT={DT}, B_read={wall_B_readout}s)",file=sys.stderr)

        # ============ TRIAL 2: long DT to probe FIFO depth (saturation) ============
        DT2=8.0
        flush(d,B,"B")
        print(f"[T2] dead {DT2}s then drain B (depth probe)...",file=sys.stderr)
        time.sleep(DT2)
        tap(d,B); tgB2,_=drain_until_quiet(d,4.0); untap(d,B); time.sleep(0.1); drain_until_quiet(d,0.3)
        sB2=summ(tgB2)
        OUT["T2_DT_s"]=DT2; OUT["T2_B"]=sB2
        OUT["T2_depth_note"]=(f"span {sB2.get('span_s')}s vs DT {DT2}s: "
            f"{'SATURATED (older tags dropped) -> FIFO depth ~'+str(sB2.get('n')) if sB2.get('span_s',0)<DT2*0.8 else 'NOT saturated, full history retained'}")
        print(f"[T2] B n={sB2.get('n')} span={sB2.get('span_s')}s | {OUT['T2_depth_note']}",file=sys.stderr)
    except Exception as e:
        OUT["FATAL_trial"]=repr(e); print("FATAL_trial",repr(e),file=sys.stderr)
    finally:
        try:
            for s in (B,C): untap(d,s)
            drain_until_quiet(d,0.6)
            for s in (B,C):
                d.set_section_function(s,CNT); time.sleep(0.1)
                d.configure_counter(s,True,True,True,True,False); time.sleep(0.1)
                for ch in range(4):
                    raw(d,{"command":"reset_channel","callback":"s","params":{"section":s.value,"channel":ch}})
                time.sleep(0.1)
            d.disconnect()
            OUT["restored"]="B,C -> counter + reset (verify separately)"
        except Exception as e:
            OUT["restore_ERR"]=repr(e)
    OUT.pop("_ignore",None)
    print(json.dumps(OUT,indent=2,default=str))

if __name__=="__main__":
    main()
