# Draft email to CAEN support (CC: abba@nuclearinstruments.eu)

> **Before you send (operator TODO — this draft is otherwise complete):**
> 1. Fill the signature block at the bottom: `[name]`, `[institute / experiment]`,
>    `[serial numbers, MyCAEN account]` (serials are on the rear label of each unit,
>    or `get_version`/the GUI About page).
> 2. Send via the **MyCAEN support portal** (create a ticket under product **N1081B**),
>    not plain email, so it lands in their tracker.
> 3. **CC Andrea Abba <abba@nuclearinstruments.eu>** — the N1081B designer at Nuclear
>    Instruments (the closed-source firmware / libwebsock question is really for him).
> 4. Optionally attach a degradation timeline / packet capture (offered in the last line).
>
> This is the only path to a real fix: the firmware is closed-source, so all our
> mitigations are client-side (the `board_session()` hygiene gateway).

**To:** CAEN support (MyCAEN ticket, product N1081B)
**CC:** Andrea Abba <abba@nuclearinstruments.eu>
**Subject:** N1081B websocket remote-control interface hangs under sustained automated
control — session limits / recovery / firmware question

---

Dear CAEN support,

We operate six N1081B units (software 2025.3.27.0, zynq 22.10.07.00, fpga 23.11.10.00;
serial numbers available on request) as the trigger and rate-monitoring logic of a
beam test at the CERN nTOF facility. We control them over Ethernet with the official
Python SDK (n1081b-sdk 1.0.4, websocket JSON API on port 8080).

**Problem.** Under sustained scripted control (hours of polling and reconfiguration,
including scripts that sometimes disconnect without a websocket Close handshake — e.g.
after a command timeout), a unit's websocket command interface degrades progressively:

1. first, intermittent command timeouts (some commands answer in <0.1 s, others never);
2. then, write commands (select_section_function, configure_function) fail or drop the
   connection while read commands still answer;
3. finally, the interface stops responding entirely — a new websocket connects at the TCP
   level but the "login" command never receives a reply (verified with 90 s timeouts).

Throughout all stages the module itself is fine: ping, the web server on port 80, the
front-panel logic (FPGA functions keep running) and the touchscreen all work. The web GUI
also keeps working longer than SDK scripts do. If we stop ALL remote contact with the
unit, it recovers on its own after several hours (we measured ~2.5 h for stage 2), but a
deep stage-3 wedge is much slower: one unit deliberately driven to stage 3 was still
unresponsive to a websocket login (8 s timeout) more than 11 hours later, and in practice
we recover such cases with a physical touchscreen reboot rather than waiting. There is no
reliable *remote* reboot when the interface is in this state.

**What we found.** The websocket handshake identifies the embedded server as
"Server: libwebsock/1.0.7". That library (payden/libwebsock) has been unmaintained since
2014, and its issue tracker documents a deadlock triggered by client disconnects with
symptoms identical to ours (issue #18: "libwebsock won't handle new connections, and
won't send/receive any more data … everything else works"). It also never reaps clients
that disconnect without a Close frame (no server-side ping, no idle timeout, no TCP
keepalive), so scripted clients that crash or time out appear to leave permanent zombie
sessions until kernel-level TCP timeouts clear them — consistent with the hours-scale
self-recovery we observe.

**Our questions:**

1. Is this a known issue, and is there a firmware release newer than 2025.3.27.0 that
   updates or replaces the websocket server, or adds a watchdog on the command daemon?
2. What are the actual limits of the remote interface — maximum concurrent websocket
   sessions, and is there any server-side timeout for dead/idle sessions? Does the web
   GUI's periodic "alive" command play a role in session lifetime, and should SDK
   clients send it too?
3. Is there any supported way to restart the command interface or reboot the module
   remotely when the websocket backend is unresponsive (the unit is in a NIM crate that
   is not always physically accessible)? For example: SSH service credentials for an
   orderly reboot (port 22 is open on the module), or an HTTP endpoint. The GUI reboot
   requires the websocket, so it is unavailable exactly when needed.
4. Are there recommended client-side practices for long-running automated control
   (connection reuse vs reconnection, command pacing, keepalive) beyond what the
   SDK-n1081b.pdf reference documents?

We can provide detailed logs, timelines, and packet captures of the degradation if
useful, and we are happy to test a candidate firmware.

Thank you and best regards,

[name]
[institute / experiment]
[serial numbers, MyCAEN account]
