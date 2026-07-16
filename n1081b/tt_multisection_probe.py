#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Probe .244 (Module 5) for a clean multi-section Time-Tag readout.

Read-heavy, self-restoring. All 4 sections start (and are restored) to COUNTER.
Experiments:
  0  snapshot current config (functions + per-channel input config)  [restore ref]
  5f file-based readout probe: can we get/save/download a TT file per section?
  1  raw single-section stream dump: ALL keys of send_data (is there a section field?)
  2  activity scan: which sections/panels actually have signal right now
  3  two sections streaming at once: rejected? merged? distinguishable?
  4  off-channel fingerprint test (user idea): does packet structure change with
     enabled-lemo set, and can panel-number partitioning separate sections?
Restore: every touched section -> FN_COUNTER, configure_counter(T,T,T,T,False).
"""
import json, time, sys, socket

from n1081b_sdk import N1081B
try:
    from websocket import WebSocketTimeoutException
except Exception:
    class WebSocketTimeoutException(Exception): pass
IDLE = (socket.timeout, WebSocketTimeoutException)

IP = "192.168.10.244"
PW = "password"
SEC = {s.name[-1]: s for s in N1081B.Section}          # 'A'->SEC_A
TT  = N1081B.FunctionType.FN_TIME_TAG
CNT = N1081B.FunctionType.FN_COUNTER
OUT = {}   # experiment results


def log(m): print(m, file=sys.stderr, flush=True)

def conn():
    d = N1081B(IP)
    if not d.connect(): raise ConnectionError("connect failed")
    d.ws.settimeout(6)
    if not d.login(PW): raise ConnectionError("login failed")
    return d

def raw(d, obj):
    d.ws.send(json.dumps(obj))

def drain(d, seconds, on_pkt, stop_when=None):
    """Tolerant recv loop; call on_pkt(parsed) for every parsed packet."""
    d.ws.settimeout(0.4)
    t_end = time.time() + seconds
    while time.time() < t_end:
        try:
            r = d.ws.recv()
        except IDLE:
            continue
        except Exception as e:
            log(f"  recv err {e!r}"); break
        try:
            pkt = json.loads(r.replace(",]", "]"))
        except Exception:
            continue
        on_pkt(pkt)
        if stop_when and stop_when():
            break

def start_tt_raw(d, sec):
    raw(d, {"command":"reset_channel","callback":"start","params":{"section":sec.value,"channel":1}})
    raw(d, {"command":"start_tt_data","callback":"start","params":{"section":sec.value}})

def stop_tt_raw(d, sec):
    raw(d, {"command":"stop_tt_data","callback":"stop","params":{"section":sec.value}})


# ---------- Phase 0: snapshot ----------
def snapshot():
    d = conn()
    snap = {"sections_function": d.get_sections_function()}
    ic = {}
    for L, s in SEC.items():
        try:
            ic[L+"_section"] = d.get_input_configuration(s)
        except Exception as e:
            ic[L+"_section"] = repr(e)
        chans = {}
        for ch in range(1, 7):
            try:
                chans[ch] = d.get_input_channel_configuration(s, ch)
            except Exception as e:
                chans[ch] = repr(e)
        ic[L+"_channels"] = chans
    snap["input_cfg"] = ic
    d.disconnect()
    return snap


# ---------- Phase 5f: file-based readout probe ----------
def probe_file_path():
    d = conn()
    res = {}
    # Does the firmware expose a TT data file via get_config_file with various names?
    for fn in ("time_tag", "timetag", "tt", "time_tagging"):
        raw(d, {"command":"get_config_file","callback":"get_file","function":fn})
        got = None
        d.ws.settimeout(1.5)
        try:
            got = json.loads(d.ws.recv().replace(",]","]"))
        except IDLE:
            got = "TIMEOUT"
        except Exception as e:
            got = repr(e)
        res[f"get_config_file[{fn}]"] = got
    # Is there a 'save timestamps to file' style command? try a couple of guesses, tolerant.
    for cmd in ("save_tt_data", "get_tt_file", "download_tt"):
        raw(d, {"command":cmd,"callback":"probe","params":{"section":0}})
        d.ws.settimeout(1.0)
        try:
            res[cmd] = json.loads(d.ws.recv().replace(",]","]"))
        except IDLE:
            res[cmd] = "TIMEOUT (no reply)"
        except Exception as e:
            res[cmd] = repr(e)
    d.disconnect()
    return res


# ---------- Phase 1: raw single-section dump ----------
def raw_dump(letter="A", seconds=8):
    d = conn()
    s = SEC[letter]
    d.set_section_function(s, TT)
    d.configure_time_tagging(s, True, True, True, True, True, True)
    start_tt_raw(d, s)
    pkts = []
    keyset = set()
    el_shapes = set()
    def on(pkt):
        if pkt.get("command") != "send_data":
            pkts.append(("NON_SEND_DATA", pkt)); return
        keyset.update(pkt.keys())
        td = pkt.get("timetag_data", [])
        for el in td[:50]:
            el_shapes.add((type(el).__name__, len(el) if isinstance(el,(list,dict)) else 1))
        if len(pkts) < 6:
            # keep a shallow copy w/ truncated tag list for printing
            p = dict(pkt); p["timetag_data"] = td[:8]; p["_n_tags"] = len(td)
            pkts.append(("send_data", p))
    drain(d, seconds, on)
    stop_tt_raw(d, s)
    drain(d, 0.6, lambda p: None)
    d.disconnect()
    return {"send_data_keys": sorted(keyset),
            "tag_element_shapes": sorted(map(str, el_shapes)),
            "sample_packets": pkts}


# ---------- Phase 2: activity scan ----------
def activity_scan(seconds_each=3):
    counts = {}
    for L, s in SEC.items():
        d = conn()
        d.set_section_function(s, TT)
        d.configure_time_tagging(s, True, True, True, True, True, True)
        start_tt_raw(d, s)
        c = {}
        def on(pkt):
            if pkt.get("command") == "send_data":
                for el in pkt.get("timetag_data", []):
                    if isinstance(el, list) and el:
                        c[el[0]] = c.get(el[0], 0) + 1
        drain(d, seconds_each, on)
        stop_tt_raw(d, s)
        drain(d, 0.4, lambda p: None)
        d.disconnect()
        counts[L] = {"per_panel": c, "total": sum(c.values())}
    return counts


# ---------- Phase 3: two sections at once ----------
def two_sections(a, b, seconds=6):
    d = conn()
    replies = []
    sa, sb = SEC[a], SEC[b]
    replies.append(("set_fn_"+a, d.set_section_function(sa, TT)))
    replies.append(("cfg_tt_"+a, d.configure_time_tagging(sa, True,True,True,True,True,True)))
    replies.append(("set_fn_"+b, d.set_section_function(sb, TT)))
    replies.append(("cfg_tt_"+b, d.configure_time_tagging(sb, True,True,True,True,True,True)))
    start_tt_raw(d, sa)
    start_tt_raw(d, sb)
    pkt_keys = set(); n_send = 0; per_panel = {}
    def on(pkt):
        nonlocal n_send
        if pkt.get("command") == "send_data":
            n_send += 1
            pkt_keys.update(pkt.keys())
            for el in pkt.get("timetag_data", []):
                if isinstance(el, list) and el:
                    per_panel[el[0]] = per_panel.get(el[0], 0) + 1
    drain(d, seconds, on)
    stop_tt_raw(d, sa); stop_tt_raw(d, sb)
    drain(d, 0.6, lambda p: None)
    d.disconnect()
    return {"setup_replies": replies, "send_data_pkts": n_send,
            "send_data_keys": sorted(pkt_keys), "per_panel": per_panel}


# ---------- Phase 4: off-channel fingerprint (user idea) ----------
def fingerprint(letter="A", seconds=5):
    """Does the packet structure depend on which lemos are enabled?
    Compare all-enabled vs single-enabled. Also record packet keys for both."""
    res = {}
    for tag, enables in (("all6", (True,)*6), ("only_lemo0", (True,False,False,False,False,False))):
        d = conn()
        s = SEC[letter]
        d.set_section_function(s, TT)
        d.configure_time_tagging(s, *enables)
        start_tt_raw(d, s)
        keys=set(); shapes=set(); panels=set(); sample=[]
        def on(pkt):
            if pkt.get("command")=="send_data":
                keys.update(pkt.keys())
                for el in pkt.get("timetag_data", []):
                    if isinstance(el,list):
                        shapes.add(len(el))
                        if el: panels.add(el[0])
                if len(sample)<3:
                    p=dict(pkt); p["timetag_data"]=pkt.get("timetag_data",[])[:5]; sample.append(p)
        drain(d, seconds, on)
        stop_tt_raw(d, s); drain(d,0.4,lambda p:None)
        d.disconnect()
        res[tag]={"keys":sorted(keys),"tag_len":sorted(shapes),
                  "panels_seen":sorted(panels),"sample":sample}
    return res


# ---------- Restore ----------
def restore():
    d = conn()
    for L, s in SEC.items():
        d.set_section_function(s, CNT)
        d.configure_counter(s, True, True, True, True, False)
    fn = d.get_sections_function()
    d.disconnect()
    return fn


def main():
    try:
        log("Phase 0: snapshot"); OUT["snapshot"] = snapshot()
        log("Phase 5f: file-path probe"); OUT["file_probe"] = probe_file_path()
        log("Phase 1: raw single-section dump"); OUT["raw_dump"] = raw_dump("A", 8)
        log("Phase 2: activity scan"); OUT["activity"] = activity_scan(3)
        act = OUT["activity"]
        live = [L for L in "ABCD" if act.get(L,{}).get("total",0) > 0]
        log(f"   active sections: {live}")
        a = live[0] if live else "A"
        b = live[1] if len(live) > 1 else ("B" if a != "B" else "C")
        log(f"Phase 3: two sections at once ({a}+{b})")
        OUT["two_sections"] = {"pair": [a,b], **two_sections(a, b, 6)}
        log("Phase 4: off-channel fingerprint")
        OUT["fingerprint"] = fingerprint(a, 5)
    except Exception as e:
        OUT["FATAL"] = repr(e)
        log(f"FATAL {e!r}")
    finally:
        log("RESTORE -> counter")
        try:
            OUT["restore"] = restore()
        except Exception as e:
            OUT["restore"] = f"RESTORE FAILED: {e!r}"
            log(OUT["restore"])
    print(json.dumps(OUT, indent=2, default=str))


if __name__ == "__main__":
    main()
