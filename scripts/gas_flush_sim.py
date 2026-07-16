#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on July 10 3:32 PM 2026
Created in PyCharm
Created as nTof_x17_DAQ/gas_flush_sim.py

@author: Dylan Neff, dylan
"""

"""
Gas-flush strategy for daisy-chained Micromegas detectors.

Models N detectors in series as N CSTRs (well-mixed volumes), isobutane fraction
as a passive scalar. Argon flow is capped; isobutane adds on top, so richer feed
= higher total throughput AND steeper driving gradient. Compares a direct feed to
a "bang-bang" overshoot (rich feed, then drop to target), optimizing the switch time.

Tune the globals to your real volumes / MFC or backpressure limit.
"""

import numpy as np

# ---- global parameters ----
N      = 4      # detectors in series
V      = 5.0    # L each (per detector)
Q_AR   = 7.0    # L/h argon cap (MFC range OR backpressure limit)
X0     = 0.05   # initial isobutane fraction
XT     = 0.20   # target isobutane fraction
TOL    = 0.002  # settle band, absolute (0.002 = +/-0.2%; loosen to ~0.005 in practice)
DT     = 0.001  # h integration step
T_END  = 20.0   # h simulation horizon


def main():
    # Scenario A: direct feed, no overshoot
    tA = settle_time(*sim(const_feed(XT)))
    print(f"No overshoot  (feed {pct(XT)}, Q={Q_AR/(1-XT):.2f} L/h): "
          f"settle {tA:.2f} h")

    # Scenario B: overshoot to various richness, optimal single switch time
    for f_over in (0.30, 0.40, 0.50):
        best_t, best_sw = min(
            ((settle_time(*sim(overshoot(f_over, ts))), ts)
             for ts in np.arange(0.1, 6.0, 20 * DT)),
            key=lambda p: p[0],
        )
        Qo = Q_AR / (1 - f_over)
        print(f"Overshoot {pct(f_over)} (Q={Qo:.2f} L/h) switch@{best_sw:.2f} h: "
              f"settle {best_t:.2f} h  ({100*(1-best_t/tA):+.0f}% vs direct)")

    print("\nIsobutane delivery / venting rates:")
    for f in (0.20, 0.30, 0.40, 0.50):
        Q = Q_AR / (1 - f)
        print(f"  {pct(f)}: Q_tot={Q:5.2f} L/h, iso={Q-Q_AR:.2f} L/h")


def sim(feed_schedule):
    """feed_schedule: t -> (f_in, Q_total). Returns (t_array, x_array[steps, N])."""
    steps = int(T_END / DT)
    x = np.full(N, X0)
    ts = np.zeros(steps)
    xs = np.zeros((steps, N))
    for k in range(steps):
        t = k * DT
        f_in, Q = feed_schedule(t)
        upstream = np.concatenate(([f_in], x[:-1]))
        x = x + DT * (Q / V) * (upstream - x)   # dx_i/dt = (Q/V)(x_{i-1}-x_i)
        ts[k], xs[k] = t, x
    return ts, xs


def settle_time(ts, xs):
    """Last time the worst detector leaves the +/-TOL band (i.e. when all settle)."""
    outside = np.where(np.abs(xs - XT).max(axis=1) > TOL)[0]
    return 0.0 if len(outside) == 0 else ts[outside[-1]] + DT


def const_feed(f):
    Q = Q_AR / (1 - f)
    return lambda t: (f, Q)


def overshoot(f_over, t_switch):
    Qo, Qt = Q_AR / (1 - f_over), Q_AR / (1 - XT)
    return lambda t: (f_over, Qo) if t < t_switch else (XT, Qt)


def pct(f):
    return f"{int(round((1-f)*100))}/{int(round(f*100))}"


main()