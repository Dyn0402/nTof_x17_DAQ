#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on October 22 10:36 PM 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/send_run_config_to_processor.py

@author: Dylan Neff, Dylan
"""

import os
import sys
from subprocess import Popen, PIPE
import json


BASE_DIR = "/local/home/banco/dylan/Cosmic_Bench_DAQ_Control"
# BASE_DIR = "/local/home/dn277127/PycharmProjects/Cosmic_Bench_DAQ_Control"
RUNCONFIG_DIR = f"{BASE_DIR}/config/json_run_configs/"
BASH_DIR = f"{BASE_DIR}/bash_scripts/"


def main():
    if len(sys.argv) != 2:
        print('No run config path given')
        return
    run_config_path = os.path.join(RUNCONFIG_DIR, sys.argv[1]) if not os.path.isabs(sys.argv[1]) else sys.argv[1]

    min_decoder_port = 1200
    # Get open tmux port by looking at live tmux session names
    decoder_port = get_open_decoder_port(min_decoder_port)

    # Update run_config_path json with new port in dedip196_processor_info:port
    with open(run_config_path, 'r') as file:
        data = json.load(file)
    data['dedip196_processor_info']['port'] = decoder_port
    with open(run_config_path, 'w') as file:
        json.dump(data, file)

    # Start a tmux decoder_port session --> Just run python processing_control.py port
    # bash_scripts/start_tmux.sh decoder "python processing_control.py"
    start_tmux(f'decoder_{decoder_port}', f'"python processing_control.py {decoder_port}"')

    # Start a tmux processor_port session --> Wait for decoder to start, then run the on-the-fly processing loop
    start_tmux(f'processor_{decoder_port}', f'"sleep 20; python processor_runner.py {decoder_port} {run_config_path}"')

    # Start a tmux analyzer session --> Let analyzer wait for data and process

    print('donzo')


def get_open_decoder_port(min_port=1200):
    """
    Get an available decoder port. Using min_port as the starting point, check if a decoder_port or processor_port
    exists. If so, iterate min_port and try again. Return first open port.
    :param min_port:
    :return:
    """
    tmux_ls = Popen('tmux ls', shell=True, text=True, stdin=PIPE, stdout=PIPE, stderr=PIPE).communicate()[0]
    used_ports = []
    for line in tmux_ls.splitlines():
        session_name = line.split(': ')[0]
        session_name_split = session_name.split('_')
        if len(session_name_split) == 2 and session_name_split[0] in ['decoder', 'processor']:
            # Should probably do some more checks here to ensure it's an int and such
            port_num = int(session_name_split[1])
            used_ports.append(port_num)

    used_ports = list(set(used_ports))
    # Get the minimum unused port number, starting with min_port.
    new_port = min_port
    while new_port in used_ports:
        new_port += 1

    return new_port


def start_tmux(session_name, session_command=None):
    """
    Start a new tmux session and run session_command if passed. Just a wrapper around start_tmux.sh
    :param session_name:
    :param session_command:
    :return:
    """
    bash_command = f'{BASH_DIR}start_tmux.sh {session_name}'
    if session_command:
        bash_command += ' ' + session_command
    Popen(bash_command, shell=True, stdout=PIPE, stderr=PIPE).communicate()


if __name__ == '__main__':
    main()
