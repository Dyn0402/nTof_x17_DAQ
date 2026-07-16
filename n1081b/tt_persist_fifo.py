#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PERSISTENT-FIFO DEAD-TIME READOUT TEST (.244, non-wedged sections B & C).

Decisive questions:
 (a) Does a section whose function is TIME_TAG accumulate hits to its own FIFO
     while it is NOT the actively-tapped stream (i.e. no active start_tt_data)?
 (b) Can we drain that accumulated backlog with start_tt_data ALONE (no
     reset_channel), or does the normal start sequence's reset_channel wipe it?
 (c) Multi-section: with B and C both in TT, can we read each section's backlog
     one-at-a-time in dead time with clean per-section attribution?

Signature we look for: the board clock is free-running (10 ns steps, ~64-bit,
common to all sections). If we record a reference timestamp T_ref "now", wait a
dead time of DT seconds, then re-tap:
  - BUFFERED (H2, the hopeful result): first backlog tag has board time near
    T_ref (old, generated right after we stopped), count ~ rate*(DT+drain),
    capped at FIFO depth (~1024).
  - NOT BUFFERED (H1): first tag has board time near T_ref + DT*1e8 (live now),
    count ~ rate*drain only.

Uses ONE persistent connection, paced. Touches only sections B and C (A is left
alone — its TT streaming path is wedged and it stays on FN_COUNTER). Restores
B & C to FN_COUNTER and restarts counting in finally.
"""
import json, time, sys, socket
from n1081b_sdk import N1081B
try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception): pass
IDLE = (socket.timeout, WebSocketTimeoutException)

IP="192.168.10.244"; PW="password"
SEC={s.name[-1]:s for s in N1081B.Section}
B=SEC["B"]; C=SEC["C"]
TT=N1081B.FunctionType.FN_TIME_TAG; CNT=N1081B.FunctionType.FN_COUNTER
NS_PER_S=1e8   # 10 ns steps -> 1e8 steps per second
OUT={}

def raw(d,o): d.ws.send(json.dumps(o))
def start_tap(d,sec,reset):
    if reset:
        raw(d,{"command":"reset_channel","callback":"s",
               "params":{"section":sec.value,"channel":1}})
    raw(d,{"command":"start_tt_data","callback":"s","params":{"section":sec.value}})
def stop_tap(d,sec):
    raw(d,{"command":"stop_tt_data","callback":"s","params":{"section":sec.value}})

def drain(d,secs):
    """Collect (panel,t) tags for `secs`. Returns list of [panel,t]."""
    d.ws.settimeout(0.3); tstop=time.time()+secs; tags=[]
    while time.time()<tstop:
        try: r=d.ws.recv()
        except IDLE: continue
        except Exception as e:
            print("  recv err",repr(e),file=sys.stderr); break
        try: p=json.loads(r.replace(",]","]"))
        except Exception: continue
        if p.get("command")=="send_data":
            for el in p.get("timetag_data",[]):
                if isinstance(el,list) and len(el)>=2:
                    tags.append([el[0],el[1]])
    return tags

def summ(tags):
    if not tags: return {"n":0}
    ts=[t[1] for t in tags]; panels=sorted(set(t[0] for t in tags))
    per={}
    for pn,_ in tags: per[pn]=per.get(pn,0)+1
    return {"n":len(tags),"panels":panels,"per_panel":per,
            "t_min":min(ts),"t_max":max(ts),"span_s":round((max(ts)-min(ts))/NS_PER_S,4),
            "first5":[t[1] for t in tags[:5]],"last5":[t[1] for t in tags[-5:]]}

def main():
    DT=2.0          # dead time (s) with NO tapping
    DRAIN=0.6       # backlog drain window (s)
    d=N1081B(IP); assert d.connect(); d.ws.settimeout(6); assert d.login(PW)
    try:
        # snapshot starting functions
        OUT["fn_before"]=[(x["section"],x["function_name"]) for x in d.get_sections_function()["data"]]

        # ---- Put B and C into TIME_TAG (paid once) ----
        d.set_section_function(B,TT); time.sleep(0.1)
        d.configure_time_tagging(B,True,True,True,True,True,True); time.sleep(0.1)
        d.set_section_function(C,TT); time.sleep(0.1)
        d.configure_time_tagging(C,True,True,True,True,True,True); time.sleep(0.2)

        # ---- Reference live read on B (with reset) to learn rate + "now" ----
        start_tap(d,B,reset=True)
        ref=drain(d,1.0)
        stop_tap(d,B); drain(d,0.4)   # flush
        s_ref=summ(ref)
        rate_B=s_ref["n"]/1.0 if s_ref["n"] else 0
        OUT["phase1_ref_live_B"]=s_ref
        OUT["phase1_rate_B_hz"]=round(rate_B,1)
        t_now=s_ref.get("t_max",0)
        print(f"[1] B live rate ~{rate_B:.0f} Hz, t_now~{t_now}",file=sys.stderr)

        # ---- TEST (a)+(b): dead time, then re-tap B WITHOUT reset ----
        print(f"[2] dead time {DT}s (B un-tapped, still FN_TIME_TAG)...",file=sys.stderr)
        time.sleep(DT)
        start_tap(d,B,reset=False)          # <-- key: NO reset_channel
        back=drain(d,DRAIN)
        stop_tap(d,B); drain(d,0.3)
        s_back=summ(back)
        OUT["phase2_backlog_B_noreset"]=s_back
        # interpret
        if s_back["n"]:
            age_first=(s_back["t_min"]-t_now)/NS_PER_S   # ~0 => buffered; ~DT => live
            OUT["phase2_first_tag_age_s"]=round(age_first,4)
            OUT["phase2_expected_if_buffered"]=round(rate_B*(DT+DRAIN),0)
            OUT["phase2_expected_if_live_only"]=round(rate_B*DRAIN,0)
            buffered = (age_first < DT*0.5) and (s_back["n"] > rate_B*DRAIN*1.8)
            OUT["phase2_VERDICT"]="BUFFERED (accumulated un-tapped, survived no-reset)" if buffered \
                else "LIVE-ONLY (no accumulation while un-tapped)"
        print(f"    -> {OUT.get('phase2_VERDICT')}  n={s_back['n']} span={s_back.get('span_s')}s",file=sys.stderr)

        # ---- CONTROL: dead time, then re-tap B WITH reset (expect live-only) ----
        print(f"[3] control: dead {DT}s then re-tap WITH reset...",file=sys.stderr)
        time.sleep(DT)
        start_tap(d,B,reset=True)
        ctrl=drain(d,DRAIN)
        stop_tap(d,B); drain(d,0.3)
        OUT["phase3_control_B_withreset"]=summ(ctrl)

        # ---- MULTI-SECTION: B & C both armed, dead time, drain B then C ----
        print(f"[4] multi-section: dead {DT}s (B&C both TT, un-tapped), then drain B then C...",file=sys.stderr)
        time.sleep(DT)
        start_tap(d,B,reset=False); mb=drain(d,DRAIN); stop_tap(d,B); drain(d,0.25)
        start_tap(d,C,reset=False); mc=drain(d,DRAIN); stop_tap(d,C); drain(d,0.25)
        OUT["phase4_multi_B"]=summ(mb)
        OUT["phase4_multi_C"]=summ(mc)
        print(f"    B n={OUT['phase4_multi_B']['n']} C n={OUT['phase4_multi_C']['n']}",file=sys.stderr)

    except Exception as e:
        OUT["FATAL"]=repr(e); print("FATAL",repr(e),file=sys.stderr)
    finally:
        try:
            stop_tap(d,B); stop_tap(d,C); drain(d,0.5)
            for s in (B,C):
                d.set_section_function(s,CNT); time.sleep(0.1)
                d.configure_counter(s,True,True,True,True,False); time.sleep(0.1)
                for ch in range(4):
                    raw(d,{"command":"reset_channel","callback":"s",
                           "params":{"section":s.value,"channel":ch}})
                time.sleep(0.1)
            OUT["fn_after"]=[(x["section"],x["function_name"]) for x in d.get_sections_function()["data"]]
            # confirm counting restarts
            time.sleep(0.6)
            r1=d.get_function_results(B); time.sleep(0.5); r2=d.get_function_results(B)
            OUT["restore_B_counts_t1"]=r1.get("data") if isinstance(r1,dict) else str(r1)
            OUT["restore_B_counts_t2"]=r2.get("data") if isinstance(r2,dict) else str(r2)
            d.disconnect()
        except Exception as e:
            OUT["restore_ERR"]=repr(e); print("restore ERR",repr(e),file=sys.stderr)
    print(json.dumps(OUT,indent=2,default=str))

if __name__=="__main__":
    main()
