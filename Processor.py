#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 14 16:26 2025
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/Processor

@author: Dylan Neff, dn277127
"""
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on September 15 2025
Processor system with separate decoding and tracking managers
"""

import json
import time
from pathlib import Path
from typing import Dict, Any, Optional
from threading import Event

from Client import Client
from common_functions import get_feu_num_from_fdf_file_name, get_file_num_from_fdf_file_name


class DecoderProcessorManager:
    def __init__(self, config: Dict[str, Any], output_dir: Path):
        self.process_prefix = "dedip196_processor_info"
        self.client = Client(config[self.process_prefix]["ip"], config[self.process_prefix]["port"])
        self._config = config
        self.output_dir = output_dir
        self.filtering = config.get("filtering_by_m3", False)
        self.save_fds = config.get("save_fdfs", False)

        # inner dirs
        self.raw_dirname = config.get("raw_daq_inner_dir", "raw_daq_data")
        self.decoded_dirname = config.get("decoded_root_inner_dir", "decoded_root")
        self.filtered_dirname = config.get("filtered_root_inner_dir", "filtered_root")

    def __enter__(self):
        self.client.__enter__()
        self._setup()
        return self

    def __exit__(self, *args):
        self.client.send('Finished')
        return self.client.__exit__(*args)

    def _setup(self):
        self.client.send("Connected to daq_control")
        self.client.receive()
        self.client.send_json(self._config[self.process_prefix])
        self.client.receive()
        self.client.send_json({"included_detectors": self._config["included_detectors"]})
        self.client.receive()
        self.client.send_json({"detectors": self._config["detectors"]})
        self.client.receive()

    def process_all(self):
        new_files = False
        for sub_run in sorted(self.output_dir.iterdir()):
            print(f'Processing run {sub_run.name}')
            if not sub_run.is_dir():
                continue

            raw_dir = sub_run / self.raw_dirname
            print(f'Raw dir: {raw_dir}')
            decoded_dir = sub_run / self.decoded_dirname
            filtered_dir = sub_run / self.filtered_dirname

            raw_file_nums = []
            for raw_file in sorted(raw_dir.glob("*.fdf")):
                feu_num = get_feu_num_from_fdf_file_name(raw_file.name)
                print(f'Found raw file {raw_file.name} with FEU {feu_num}')
                if feu_num == self._config["m3_feu_num"]:  # Skip M3 files
                    print(f'Skipping M3 file {raw_file.name}')
                    continue
                if '_datrun_' not in raw_file.name:
                    print(f'Skipping non-datrun file {raw_file.name}')
                    continue
                raw_file_nums.append(get_file_num_from_fdf_file_name(raw_file.name, -2))

            already_decoded_file_nums = []
            for tracked_file in sorted(decoded_dir.glob("*.root")):
                already_decoded_file_nums.append(get_file_num_from_fdf_file_name(tracked_file.name, -2))

            already_filtered_file_nums = []
            for tracked_file in sorted(filtered_dir.glob("*.root")):
                already_filtered_file_nums.append(get_file_num_from_fdf_file_name(tracked_file.name, -2))

            # Get the raw files that need processing
            print(f'Raw file_nums: {raw_file_nums}')
            print(f'Already decoded file_nums: {already_decoded_file_nums}')
            print(f'Already filtered file_nums: {already_filtered_file_nums}')
            to_process_file_nums = sorted(set(raw_file_nums) - set(already_decoded_file_nums) - set(already_filtered_file_nums))
            if not to_process_file_nums:
                print(f'No new files to process for run {sub_run.name}')
                continue

            new_files = True  # Found new files
            for file_num in to_process_file_nums:
                print(f'Processing file_num {file_num} for run {sub_run.name}')
                self._process_file(file_num, sub_run.name)
        return new_files

    def _process_file(self, file_num: int, sub_run_name: str):
        # Decode
        self.client.send(f"Decode FDFs file_num={file_num} {sub_run_name}")
        self.client.receive()  # Decoding started
        self.client.receive()  # Wait for decoding to finish

        # Filtering or copy
        if self.filtering:
            self.client.send(f"Filter By M3 file_num={file_num} {sub_run_name}")
            self.client.receive()  # Filtering started
            self.client.receive()  # Wait for filtering to finish
        else:
            self.client.send(f"Copy To Filtered file_num={file_num} {sub_run_name}")
            self.client.receive()  # Copying started
            self.client.receive()  # Wait for copying to finish

        # Cleanup
        if not self.save_fds:
            self.client.send(f"Clean Up Unfiltered file_num={file_num} {sub_run_name}")
            self.client.receive()  # Cleanup started
            self.client.receive()  # Wait for cleanup to finish


class TrackerProcessorManager:
    def __init__(self, config: Dict[str, Any], output_dir: Path):
        self.self_config_prefix = "sedip28_processor_info"
        self.client = Client(config["sedip28_processor_info"]["ip"], config["sedip28_processor_info"]["port"])
        self._config = config
        self.output_dir = output_dir
        self.tracking_dirname = config.get("m3_tracking_inner_dir", "m3_tracking_root")
        self.raw_dirname = config.get("raw_daq_inner_dir", "raw_daq_data")
        self.save_fds = config.get("save_fdfs", False)

    def __enter__(self):
        self.client.__enter__()
        self._setup()
        return self

    def __exit__(self, *args):
        self.client.send('Finished')
        return self.client.__exit__(*args)

    def _setup(self):
        self.client.send("Connected to daq_control")
        self.client.receive()
        self.client.send_json(self._config["sedip28_processor_info"])
        self.client.receive()

    def process_all(self):
        for sub_run in sorted(self.output_dir.iterdir()):
            print(f'Processing run {sub_run.name}')
            if not sub_run.is_dir():
                continue

            raw_dir = sub_run / self.raw_dirname
            tracking_dir = sub_run / self.tracking_dirname

            raw_file_nums = []
            for raw_file in sorted(raw_dir.glob("*.fdf")):
                feu_num = get_feu_num_from_fdf_file_name(raw_file.name)
                if feu_num != self._config["m3_feu_num"]:  # Skip non-M3 files
                    print(f'Skipping non-M3 file {raw_file.name}')
                    continue
                if '_datrun_' not in raw_file.name:
                    print(f'Skipping non-datrun file {raw_file.name}')
                    continue
                raw_file_nums.append(get_file_num_from_fdf_file_name(raw_file.name, -2))

            already_tracked_file_nums = []
            for tracked_file in sorted(tracking_dir.glob("*.root")):
                already_tracked_file_nums.append(get_file_num_from_fdf_file_name(tracked_file.name, -1))

            # Get the raw files that need processing
            to_process_file_nums = sorted(set(raw_file_nums) - set(already_tracked_file_nums))
            if not to_process_file_nums:
                print(f'No new M3 files to process for run {sub_run.name}')
                continue

            for file_num in to_process_file_nums:
                print(f'Processing M3 file_num {file_num} for run {sub_run.name}')
                self._process_file(file_num, sub_run.name)

    def _process_file(self, file_num: int, sub_run_name: str):
        self.client.send(f"Run M3 Tracking file_num={file_num} {sub_run_name}")
        self.client.receive()  # Tracking started
        self.client.receive()  # Wait for tracking to finish
        if not self.save_fds:
            self.client.send(f"Clean Up M3 FDFs file_num={file_num} {sub_run_name}")
            self.client.receive()  # Cleanup started
            self.client.receive()  # Wait for cleanup to finish


class Processor:
    def __init__(self, config_path: str, stop_event: Optional[Event] = None):
        self.config_path = config_path
        self.config = self._load_config(config_path)
        self.output_dir = Path(self.config["run_out_dir"])
        self.timeout = self.config['dedip196_processor_info'].get('on-the-fly_timeout', None)
        self.stop_event = stop_event or Event()

        self.decoder: Optional[DecoderProcessorManager] = None
        self.tracker: Optional[TrackerProcessorManager] = None

    def _init_processors(self):
        self.tracker = None
        self.decoder = None

        if "sedip28_processor_info" in self.config:  # Need to also clean up now
            self.tracker = TrackerProcessorManager(self.config, self.output_dir)

        if "dedip196_processor_info" in self.config:
            self.decoder = DecoderProcessorManager(self.config, self.output_dir)

    def _load_config(self, path: str) -> Dict[str, Any]:
        with open(path, "r") as f:
            return json.load(f)

    def process_all(self):
        """
        Process all files in the output directory according to the configuration.
        This includes decoding and tracking as specified in the config.
        :return:
        """
        self._init_processors()
        if self.tracker:
            print('Starting tracking processing')
            with self.tracker as trk:
                trk.process_all()
            print('Finished tracking processing\n')

        if self.decoder:
            print('Starting decoding processing')
            with self.decoder as dec:
                dec.process_all()
            print('Finished decoding processing\n')

    def process_on_the_fly(self):
        """
        Process files as they are created in the output directory.
        Wait for new files and process them accordingly.
        :return:
        """
        last_file_found = time.time()
        while not self.stop_event.is_set():
            self._init_processors()
            if self.tracker:
                print('Checking for new tracking files to process')
                with self.tracker as trk:
                    trk.process_all()
                print('Finished tracking processing\n')

            if self.decoder:
                print('Checking for new files to decode')
                with self.decoder as dec:
                    new_files = dec.process_all()
                    if new_files:
                        last_file_found = time.time()
                print('Finished decoding\n')
            print(f'Waiting to check for new files again, good time to exit...\n'
                  f'Config: {self.config_path}, Output Dir: {self.output_dir}\n')
            for _ in range(60):  # 1-minute sleep, but interruptible
                if self.stop_event.is_set():
                    print('Stop signal received. Exiting on-the-fly processing.')
                    return
                time.sleep(1)

            time_since_last_new_files = time.time() - last_file_found
            if self.timeout is not None:
                if time_since_last_new_files >= self.timeout * 60 * 60:  # Hours to seconds
                    print(f'Processing-on-the-fly timing out after {time_since_last_new_files} seconds')
                    self.stop_event.set()
