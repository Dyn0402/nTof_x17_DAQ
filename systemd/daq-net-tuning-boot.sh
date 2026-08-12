#!/bin/bash
# Applied at boot by daq-net-tuning.service (see apply_daq_net_tuning.sh,
# ZS/IPD study 2026-07-18). Sysctls live in /etc/sysctl.d/99-custom.conf.
ethtool -G eno1 rx 4096 tx 4096 2>/dev/null
for c in /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor; do
    echo performance > "$c"
done
