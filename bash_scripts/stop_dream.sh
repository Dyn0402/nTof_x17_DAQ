#!/bin/bash
# Reliably stop the DAQ (RunCtrl) on the dream_daq tmux session.
#
# 'g' tells RunCtrl to END THE CURRENT PHASE, not to quit: a multi-phase run
# (PedThr + Data) needs one 'g' per remaining phase before RunCtrl exits. So we
# send 'g', verify the process is gone, and repeat a few times. If 'g' can't
# stop it (hung), fall back to SIGINT then SIGTERM so we never orphan RunCtrl.
SESSION="dream_daq"

BASE_DIR="$(dirname "$(dirname "$(realpath "$0")")")"
LOG_FILE="$BASE_DIR/logs/daq_events.log"
mkdir -p "$BASE_DIR/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') | STOP_DREAM     | bash_script  |" >> "$LOG_FILE"

for i in 1 2 3 4; do
    if ! pgrep -x RunCtrl >/dev/null; then
        echo "RunCtrl stopped."
        exit 0
    fi
    tmux send-keys -t "$SESSION" 'g'
    sleep 2
done

if pgrep -x RunCtrl >/dev/null; then
    echo "RunCtrl still running after repeated 'g'; sending SIGINT."
    pkill -INT -x RunCtrl
    sleep 3
fi

if pgrep -x RunCtrl >/dev/null; then
    echo "RunCtrl still running after SIGINT; sending SIGTERM."
    pkill -TERM -x RunCtrl
    sleep 2
fi

if pgrep -x RunCtrl >/dev/null; then
    echo "WARNING: RunCtrl still alive after SIGTERM."
    exit 1
fi
echo "RunCtrl stopped."
