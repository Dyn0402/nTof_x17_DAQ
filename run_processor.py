#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on May 14 8:19 AM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_processor.py

@author: Dylan Neff, Dylan
"""

from Client import Client
from run_config_beam import Config


def main():
    run_m3_tracking_current_config_subrun()
    # run_m3_filtering_max_hv_stats()
    # run_filtering_cleanup_sg1_hv_scan()
    # run_filtering_cleanup_banco_shift()
    # run_processing_drift_scan()
    print('donzo')


def run_processing_drift_scan():
    config = Config()

    dedip196_ip, dedip196_port = config.dedip196_processor_info['ip'], config.dedip196_processor_info['port']
    sedip28_ip, sedip28_port = config.sedip28_processor_info['ip'], config.sedip28_processor_info['port']

    dedip196_processor_client = Client(dedip196_ip, dedip196_port)
    sedip28_processor_client = Client(sedip28_ip, sedip28_port)

    with dedip196_processor_client as dedip196_processor, sedip28_processor_client as sedip28_processor:
        dedip196_processor.send('Connected to run_processor')
        dedip196_processor.receive()
        dedip196_processor.send_json(config.dedip196_processor_info)
        dedip196_processor.receive()
        dedip196_processor.send_json({'included_detectors': config.included_detectors})
        dedip196_processor.receive()
        dedip196_processor.send_json({'detectors': config.detectors})
        dedip196_processor.receive()

        sedip28_processor.send('Connected to run_processor')
        sedip28_processor.receive()
        sedip28_processor.send_json(config.sedip28_processor_info)

        sub_run_names = ['drift_800', 'drift_750']
        sub_run_files = [7, 7]
        # sub_run_names = ['drift_800']
        for sub_run_name, run_files in zip(sub_run_names, sub_run_files):
            for file_num in range(run_files + 1):
                dedip196_processor.send(f'Decode FDFs file_num={file_num} {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                sedip28_processor.send(f'Run M3 Tracking file_num={file_num} {sub_run_name}', silent=False)
                sedip28_processor.receive(silent=False)
                sedip28_processor.receive(silent=False)  # Wait for tracking to finish
                dedip196_processor.receive(silent=False)  # Wait for decoding to finish
                # Run filtering
                dedip196_processor.send(f'Filter By M3 file_num={file_num} {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                dedip196_processor.receive(silent=False)  # Wait for filtering to finish
                # Remove all but filtered files
                dedip196_processor.send(f'Clean Up Unfiltered file_num={file_num} {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                dedip196_processor.receive(silent=False)  # Wait for cleanup to finish


def run_filtering_cleanup_banco_shift():
    config = Config()

    dedip196_ip, dedip196_port = config.dedip196_processor_info['ip'], config.dedip196_processor_info['port']
    sedip28_ip, sedip28_port = config.sedip28_processor_info['ip'], config.sedip28_processor_info['port']

    dedip196_processor_client = Client(dedip196_ip, dedip196_port)
    sedip28_processor_client = Client(sedip28_ip, sedip28_port)

    with dedip196_processor_client as dedip196_processor, sedip28_processor_client as sedip28_processor:
        dedip196_processor.send('Connected to run_processor')
        dedip196_processor.receive()
        dedip196_processor.send_json(config.dedip196_processor_info)
        dedip196_processor.receive()
        dedip196_processor.send_json({'included_detectors': config.included_detectors})
        dedip196_processor.receive()
        dedip196_processor.send_json({'detectors': config.detectors})
        dedip196_processor.receive()

        sedip28_processor.send('Connected to run_processor')
        sedip28_processor.receive()
        sedip28_processor.send_json(config.sedip28_processor_info)

        sub_run_name = f'max_hv_long_1'
        for file_num in range(1, 60):
            dedip196_processor.send(f'Decode FDFs file_num={file_num} {sub_run_name}', silent=False)
            dedip196_processor.receive(silent=False)
            sedip28_processor.send(f'Run M3 Tracking file_num={file_num} {sub_run_name}', silent=False)
            sedip28_processor.receive(silent=False)
            sedip28_processor.receive(silent=False)  # Wait for tracking to finish
            dedip196_processor.receive(silent=False)  # Wait for decoding to finish
            # Run filtering
            dedip196_processor.send(f'Filter By M3 file_num={file_num} {sub_run_name}', silent=False)
            dedip196_processor.receive(silent=False)
            dedip196_processor.receive(silent=False)  # Wait for filtering to finish
            # Remove all but filtered files
            dedip196_processor.send(f'Clean Up Unfiltered file_num={file_num} {sub_run_name}', silent=False)
            dedip196_processor.receive(silent=False)
            dedip196_processor.receive(silent=False)  # Wait for cleanup to finish


def run_filtering_cleanup_sg1_hv_scan():
    config = Config()
    # drift_hvs = [700, 600]
    drift_hvs = [500]
    resist_hvs = [460, 450, 440, 430, 420, 410, 400, 390]
    dedip196_ip, dedip196_port = config.dedip196_processor_info['ip'], config.dedip196_processor_info['port']
    sedip28_ip, sedip28_port = config.sedip28_processor_info['ip'], config.sedip28_processor_info['port']

    dedip196_processor_client = Client(dedip196_ip, dedip196_port)
    sedip28_processor_client = Client(sedip28_ip, sedip28_port)

    with dedip196_processor_client as dedip196_processor, sedip28_processor_client as sedip28_processor:
        dedip196_processor.send('Connected to run_processor')
        dedip196_processor.receive()
        dedip196_processor.send_json(config.dedip196_processor_info)
        dedip196_processor.receive()
        dedip196_processor.send_json({'included_detectors': config.included_detectors})
        dedip196_processor.receive()
        dedip196_processor.send_json({'detectors': config.detectors})
        dedip196_processor.receive()

        sedip28_processor.send('Connected to run_processor')
        sedip28_processor.receive()
        sedip28_processor.send_json(config.sedip28_processor_info)

        for drift_hv in drift_hvs:
            for resist_hv in resist_hvs:
                if drift_hv == 700 and resist_hv in [460, 450, 440, 430]:
                    continue
                sub_run_name = f'drift_{drift_hv}_resist_{resist_hv}'
                dedip196_processor.send(f'Decode FDFs {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                sedip28_processor.send(f'Run M3 Tracking {sub_run_name}', silent=False)
                sedip28_processor.receive(silent=False)
                sedip28_processor.receive(silent=False)  # Wait for tracking to finish
                dedip196_processor.receive(silent=False)  # Wait for decoding to finish
                # Run filtering
                dedip196_processor.send(f'Filter By M3 {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                dedip196_processor.receive(silent=False)  # Wait for filtering to finish
                # Remove all but filtered files
                dedip196_processor.send(f'Clean Up Unfiltered {sub_run_name}', silent=False)
                dedip196_processor.receive(silent=False)
                dedip196_processor.receive(silent=False)  # Wait for cleanup to finish

        dedip196_processor.send('Finished')
        sedip28_processor.send('Finished')


def run_m3_filtering_max_hv_stats():
    config = Config()
    dedip196_ip, dedip196_port = config.dedip196_processor_info['ip'], config.dedip196_processor_info['port']
    with Client(dedip196_ip, dedip196_port) as processor_client:
        processor_client.send('Connected to run_processor')
        processor_client.receive()
        processor_client.send_json(config.dedip196_processor_info)
        processor_client.receive()
        processor_client.send_json({'included_detectors': config.included_detectors})
        processor_client.receive()
        processor_client.send_json({'detectors': config.detectors})
        processor_client.receive()

        processor_client.send('Filter By M3 max_hv_stats')
        processor_client.receive()

        processor_client.send('Finished')


def run_m3_tracking_current_config_subrun():
    config = Config()
    subrun_name = 'evening_test'
    sedip28_ip, sedip28_port = config.sedip28_processor_info['ip'], config.sedip28_processor_info['port']
    with Client(sedip28_ip, sedip28_port) as processor_client:
        processor_client.send('Connected to run_processor')
        processor_client.receive()
        processor_client.send_json(config.sedip28_processor_info)

        processor_client.send(f'Run M3 Tracking {subrun_name}')
        processor_client.receive()


if __name__ == '__main__':
    main()
