#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Panel-gating test using the PROVEN fresh-connection-per-config pattern
(the one activity_scan used successfully). Paced with 2s gaps, few connections.
Decisive question: does configure_time_tagging lemo_enables suppress a panel
that has signal (panel 2 / lemo1)?"""
import json, time, sys, socket
from n1081b_sdk import N1081B
try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception): pass
IDLE=(socket.timeout, WebSocketTimeoutException)
IP="192.168.10.244"; PW="password"
A=N1081B.Section.SEC_A
TT=N1081B.FunctionType.FN_TIME_TAG; CNT=N1081B.FunctionType.FN_COUNTER

def raw(d,o): d.ws.send(json.dumps(o))
def run_one(enables, secs=6):
    d=N1081B(IP); assert d.connect(); d.ws.settimeout(6); assert d.login(PW)
    d.set_section_function(A,TT)
    r=d.configure_time_tagging(A,*enables)
    raw(d,{"command":"reset_channel","callback":"s","params":{"section":A.value,"channel":1}})
    raw(d,{"command":"start_tt_data","callback":"s","params":{"section":A.value}})
    d.ws.settimeout(0.4); t=time.time()+secs; c={}
    while time.time()<t:
        try: rr=d.ws.recv()
        except IDLE: continue
        except Exception: break
        try: p=json.loads(rr.replace(",]","]"))
        except Exception: continue
        if p.get("command")=="send_data":
            for el in p.get("timetag_data",[]):
                if isinstance(el,list) and el: c[el[0]]=c.get(el[0],0)+1
    raw(d,{"command":"stop_tt_data","callback":"s","params":{"section":A.value}})
    time.sleep(0.3)
    try: d.disconnect()
    except Exception: pass
    return {"enables":list(enables),"cfg_Result":r.get('Result') if isinstance(r,dict) else r,
            "per_panel":c,"total":sum(c.values())}

def main():
    OUT={}
    cfgs=[("baseline_all6",(True,)*6),
          ("disable_panel2",(True,False,True,True,True,True)),
          ("only_panel2",(False,True,False,False,False,False))]
    for name,en in cfgs:
        print(name,"...",file=sys.stderr)
        OUT[name]=run_one(en,6)
        print("   ",OUT[name]["per_panel"],file=sys.stderr)
        time.sleep(2)
    # restore
    d=N1081B(IP); d.connect(); d.ws.settimeout(6); d.login(PW)
    for s in N1081B.Section:
        d.set_section_function(s,CNT); d.configure_counter(s,True,True,True,True,False)
    OUT["restore_fn"]=[(x["section"],x["function_name"]) for x in d.get_sections_function()["data"]]
    d.disconnect()
    print(json.dumps(OUT,indent=2,default=str))

if __name__=="__main__":
    main()
