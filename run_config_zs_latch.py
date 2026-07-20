#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""run_config_zs_latch.py — one long doubles sub-run for the FEU counter latch-read (2026-07-19).
Doubles+PS, k8, IPD10, n32, HV 550/700. Single 5-min sub-run so we can latch+peek the trigger
counters (accepted / close-drop / fifo-drop / max FIFO occupancy) several times live."""
from run_config_beam import Config as BeamConfig
ZS_PED_SET='zs_k8_tracer_from_07-18-26_14-06-43'
class Config(BeamConfig):
    def _set_defaults(self, config_path=None):
        super()._set_defaults(config_path)
        self.run_name='zs_latch'; self.run_out_dir=f'{self.data_out_dir}{self.run_name}/'
        self.dream_daq_info['run_directory']=f'/home/mx17/july_dream/dream_run/{self.run_name}/'
        self.dream_daq_info['data_out_dir']=f'{self.run_out_dir}'; self.processor_info['run_dir']=f'{self.run_out_dir}'
        self.hv_info['run_out_dir']=self.run_out_dir; self.resume=False
        self.trigger='FEU counter latch-read: doubles+PS, k8, IPD10, n32, HV 550/700.'
        self.dream_daq_info.update({'daq_config_template_path':f'{self.base_out_dir}dream_config/Tcm_Mx17_July_ZS.cfg',
            'zero_suppress':True,'common_noise_subtraction':True,'pedestal_subtraction':False,'zs_type':'tpc',
            'zs_check_sample':4,'inter_packet_delay':10,'pedestals_dir':f'{self.base_out_dir}pedestals/',
            'pedestals':ZS_PED_SET,'latency':34,'n_samples_per_waveform':32})
        hv={'5':{'1':550,'2':550,'3':550,'4':550},'9':{'0':700,'1':700,'2':700,'3':700}}
        self.sub_runs=[{'sub_run_name':'latch_dbl_n32','run_time':5,'post_pause_s':0,'inter_packet_delay':10,
            'pedestals':ZS_PED_SET,'hvs':{k:dict(v) for k,v in hv.items()}}]
        scint_hvs={}
        for det in self.detectors:
            if det['name'] not in self.included_detectors: continue
            if not str(det.get('det_type','')).startswith('scintillator'): continue
            hc,sp=det.get('hv_channels'),det.get('hv_setpoint')
            if not isinstance(hc,dict) or sp is None: continue
            for slot,ch in hc.values(): scint_hvs.setdefault(str(slot),{})[str(ch)]=sp
        for sr in self.sub_runs:
            for slot,chans in scint_hvs.items(): sr['hvs'].setdefault(slot,{}).update(chans)
if __name__=='__main__':
    Config().write_to_file('config/json_run_configs/run_config_zs_latch.json'); print('built')
