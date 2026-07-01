#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on April 29 9:37 PM 2024
Created in PyCharm
Created as Cosmic_Bench_DAQ_Control/run_config_template.py

@author: Dylan Neff, Dylan
"""

import copy
from datetime import datetime

from run_config_base import RunConfigBase
from run_config_beam import Config as BeamConfig


class Config(RunConfigBase):
    def __init__(self, config_path=None):
        if not config_path:
            self._set_defaults()

        super().__init__(config_path)

    def _set_defaults(self, config_path=None):
        # Pull all hardware / detector settings from the beam run config so the
        # pedestal and beam configs never drift apart. Timing (latency, sample
        # period) follows the data run, but the sample count is forced to 32 below
        # (see n_samples_per_waveform override).
        beam = BeamConfig()
        self.__dict__.update(copy.deepcopy(beam.__dict__))

        # --- Pedestal-specific overrides ---
        date_time_str = datetime.now().strftime('%m-%d-%y_%H-%M-%S')
        self.run_name = f'pedestals_{date_time_str}'
        self.data_out_dir = f'{self.base_out_dir}pedestals/'
        self.run_out_dir = f'{self.data_out_dir}{self.run_name}/'

        self.power_off_hv_at_end = False
        self.start_time = None

        # dream_daq_info: inherit from beam, then force pedestal-mode settings.
        # Reuse the beam data-run .cfg template and flip Sys Action PedThrRun on
        # the fly (do_pedestal_threshold_run) instead of pointing at a separate
        # hand-maintained _ped.cfg. This guarantees the pedestal run always uses
        # the same up-to-date FEU topology as the data run. Write to the pedestal
        # output dirs, run full readout, and do not apply existing pedestals
        # while taking fresh ones.
        # Pedestals are always taken with a short waveform (32 samples) regardless
        # of the data run's n_samples_per_waveform. Longer windows (e.g. 400) yield
        # a fraction of short/malformed events (FeuDataFileReader sample_cnt !=
        # nb_of_samples) that pollute the pedestal/threshold computation; 32 samples
        # avoids this and is plenty for a baseline measurement.
        ped_n_samples = 32
        self.dream_daq_info = copy.deepcopy(beam.dream_daq_info)
        self.dream_daq_info.update({
            'run_directory': f'{self.run_out_dir}',
            'data_out_dir': f'{self.run_out_dir}',
            'zero_suppress': False,   # pedestals are always full readout
            'n_samples_per_waveform': ped_n_samples,  # always 32 for pedestals
            'do_pedestal_threshold_run': True,  # Sys Action PedThrRun -> 1
            'pedestals_dir': None,    # taking fresh pedestals -> don't apply existing ones
            'pedestals': None,
        })

        # processor / hv info point at the pedestal output dir
        self.processor_info = copy.deepcopy(beam.processor_info)
        self.processor_info['run_dir'] = self.run_out_dir

        self.hv_info = copy.deepcopy(beam.hv_info)
        self.hv_info['run_out_dir'] = self.run_out_dir

        # Single pedestal subrun. Ramp only the HV channels of the detectors
        # included in run_config_beam.py (skipping PMT scintillators, which do
        # not need bias to take FEU pedestals) instead of the whole crate.
        ped_voltage = 300  # V; adjust if a different pedestal bias is needed
        ped_hvs = {}
        for det in self.detectors:
            if det['name'] not in self.included_detectors:
                continue
            if str(det.get('det_type', '')).startswith('scintillator'):
                continue  # PMTs: no HV needed for pedestals
            hv_channels = det.get('hv_channels')
            if not isinstance(hv_channels, dict):
                continue  # detectors with no CAEN HV
            for slot, channel in hv_channels.values():
                ped_hvs.setdefault(str(slot), {})[str(channel)] = ped_voltage

        self.sub_runs = [
            {
                'sub_run_name': 'pedestals',
                'run_time': 10.0 / 60,  # Minutes
                'hvs': ped_hvs,
            }
        ]


if __name__ == '__main__':
    out_run_dir = 'config/json_run_configs/'

    config_name = 'run_config_pedestals.json'

    config = Config()

    config.write_to_file(f'{out_run_dir}{config_name}')

    print('donzo')
