#!/bin/bash
# Samples backup progress every 5 min into a log, so an overnight run can be
# judged on throughput rather than on whether the pane "looks busy".
# Written 2026-07-29 while babysitting the xrdcp-wedge retune.
LOG=/home/mx17/PycharmProjects/nTof_x17_DAQ/logs/backup_progress.log
RUNS="run_100 run_99 run_98 run_97 run_96 run_95 run_94 run_93 run_92 run_91 run_90 run_89"
EOSR=/eos/experiment/ntof/data/x17/july_beam/runs

while true; do
  ts=$(date +%H:%M:%S)
  free=$(df -BG --output=avail / | tail -1 | tr -d ' G')
  # bytes actually landed on EOS across the backlog runs
  tot=0; det=""
  for r in $RUNS; do
    n=$(timeout 120 xrdfs root://eospublic.cern.ch ls -l -R $EOSR/$r 2>/dev/null \
        | awk '$1 ~ /^-/ {c++; s+=$4} END{printf "%d:%.1f", c+0, s/1073741824}')
    det="$det $r=${n}"
    tot=$((tot + ${n%%:*}))
  done
  # wedge accounting from the live pane
  pane=$(tmux capture-pane -p -J -t backup_watcher 2>/dev/null)
  w=$(echo "$pane" | grep -c "WEDGED")
  rec=$(echo "$pane" | grep -c "recovered on attempt")
  gave=$(echo "$pane" | grep -c "gave up")
  fail=$(echo "$pane" | grep -c "xrdcp FAILED")
  alive=$(pgrep -f "backup_watcher.py" >/dev/null && echo yes || echo NO)
  echo "$ts free=${free}G eosfiles=$tot wedged=$w recovered=$rec gaveup=$gave failed=$fail alive=$alive |$det" >> $LOG
  sleep 300
done
