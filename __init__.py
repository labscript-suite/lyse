spinning_top = False

import figure_manager

from dataframe_utilities import get_series_from_shot as _get_singleshot
from dataframe_utilities import dict_diff
import os
import urllib
import urllib2
import socket
import pickle as pickle
import inspect
import sys

import h5_lock, h5py
import pandas
from pylab import array, ndarray
import types

from subproc_utils import zmq_get

def data(filepath=None, host='localhost', timeout=5):
    if filepath is not None:
        return _get_singleshot(filepath)
    else:
        port = 42519
        df = zmq_get(port, host, 'get dataframe', timeout)
        df = df.convert_objects()
        try:
            df.set_index(['sequence','run time'], inplace=True, drop=False)
        except KeyError:
            # Empty dataframe?
            pass
        df.sort_index(inplace=True)
        return df
        
def globals_diff(run1, run2, group=None):
    return dict_diff(run1.get_globals(group), run2.get_globals(group))
 
class Run(object):
    def __init__(self,h5_path,no_write=False):
        self.no_write = no_write
        self.h5_path = h5_path
        if not self.no_write:
            with h5py.File(h5_path) as h5_file:
                if not 'results' in h5_file:
                     h5_file.create_group('results')
                     
        try:
            if not self.no_write:
                # The group were this run's results will be stored in the h5 file
                # will be the name of the python script which is instantiating
                # this Run object:
                frame = inspect.currentframe()
                __file__ = frame.f_back.f_locals['__file__']
                self.group = os.path.basename(__file__).split('.py')[0]
                with h5py.File(h5_path) as h5_file:
                    if not self.group in h5_file['results']:
                         h5_file['results'].create_group(self.group)
        except KeyError:
            # sys.stderr.write('Warning: to write results, call '
            # 'Run.set_group(groupname), specifying the name of the group '
            # 'you would like to save results to. This normally comes from '
            # 'the filename of your script, but since you\'re in interactive '
            # 'mode, there is no scipt name. Opening in read only mode for '
            # 'the moment.\n')
            self.no_write = True
            
    def set_group(self, groupname):
        self.group = groupname
        with h5py.File(self.h5_path) as h5_file:
            if not self.group in h5_file['results']:
                 h5_file['results'].create_group(self.group)
        self.no_write = False

    def trace_names(self):
        with h5py.File(self.h5_path) as h5_file:
            try:
                return h5_file['data']['traces'].keys()
            except KeyError:
                return []

    def get_trace(self,name):
        with h5py.File(self.h5_path) as h5_file:
            if not name in h5_file['data']['traces']:
                raise Exception('The trace \'%s\' doesn not exist'%name)
            trace = h5_file['data']['traces'][name]
            return array(trace['t'],dtype=float),array(trace['values'],dtype=float)         

    def get_result_array(self,group,name):
        with h5py.File(self.h5_path) as h5_file:
            if not group in h5_file['results']:
                raise Exception('The result group \'%s\' doesn not exist'%group)
            if not name in h5_file['results'][group]:
                raise Exception('The result array \'%s\' doesn not exist'%name)
            return array(h5_file['results'][group][name])
            
    def save_result(self,name,value):
        if self.no_write:
            raise Exception('This run is read-only. '
                            'You can\'t save results to runs through a '
                            'Sequence object. Per-run analysis should be done '
                            'in single-shot analysis routines, in which a '
                            'single Run object is used')
        with h5py.File(self.h5_path,'a') as h5_file:
            h5_file['results'][self.group].attrs[name] = value
        
    def save_result_array(self,name,data):
        if self.no_write:
            raise Exception('This run is read-only. '
                            'You can\'t save results to runs through a '
                            'Sequence object. Per-run analysis should be done '
                            'in single-shot analysis routines, in which a '
                            'single Run object is used')
        with h5py.File(self.h5_path,'a') as h5_file:
            if name in h5_file['results'][self.group]:
                # Overwrite if dataset already exists:
                del h5_file['results'][self.group][name]
            h5_file['results'][self.group].create_dataset(name,data=data)

    def get_traces(self,*names):
        traces = []
        for name in names:
            traces.extend(self.get_trace(name))
        return traces
             
    def get_result_arrays(self,group,*names):
        results = []
        for name in names:
            results.append(self.get_result_array(group,name))
        return results
        
    def save_results(self,*args):
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names,values):
            print 'saving %s ='%name, value
            self.save_result(name,value)
        
    def save_result_arrays(self, *args):
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names,values):
            self.save_result_array(name,value)
    
    def get_image(self,orientation,label,image):
        with h5py.File(self.h5_path) as h5_file:
            if not 'images' in h5_file:
                raise Exception('File does not contain any images')
            if not orientation in h5_file['images']:
                raise Exception('File does not contain any images with orientation \'%s\''%orientation)
            if not label in h5_file['images'][orientation]:
                raise Exception('File does not contain any images with label \'%s\''%label)
            if not image in h5_file['images'][orientation][label]:
                raise Exception('Image \'%s\' not found in file'%image)
            return array(h5_file['images'][orientation][label][image])
    
    def get_images(self,orientation,label, *images):
        results = []
        for image in images:
            results.append(self.get_image(orientation,label,image))
        return results
    
    def get_image_attributes(self,orientation):
        with h5py.File(self.h5_path) as h5_file:
            if not 'images' in h5_file:
                raise Exception('File does not contain any images')
            if not orientation in h5_file['images']:
                raise Exception('File does not contain any images with orientation \'%s\''%orientation)
            return dict(h5_file['images'][orientation].attrs)
        
    def get_globals(self,group=None):
        if not group:
            with h5py.File(self.h5_path) as h5_file:
                return dict(h5_file['globals'].attrs)
        else:
            try:
                with h5py.File(self.h5_path) as h5_file:
                    return dict(h5_file['globals'][group].attrs)
            except KeyError:
                return {}

    def get_globals_raw(self,group=None):
        globals_dict = {}
        def append_globals(name, obj):
            if not 'units' in name:
                temp_dict = dict(obj.attrs)
                for key, val in temp_dict.items():
                    globals_dict[key] = val
        with h5py.File(self.h5_path) as h5_file:
            h5_file['globals'].visititems(append_globals)
        return globals_dict
        
    def iterable_globals(self,group=None):
        raw_globals = self.get_globals_raw(group)
        print raw_globals.items()
        iterable_globals = {}
        for global_name, expression in raw_globals.items():
            print expression
            # try:
                # sandbox = {}
                # exec('from pylab import *',sandbox,sandbox)
                # exec('from runmanager.functions import *',sandbox,sandbox)
                # value = eval(expression,sandbox)
            # except Exception as e:
                # raise Exception('Error parsing global \'%s\': '%global_name + str(e))
            # if isinstance(value,types.GeneratorType):
               # print global_name + ' is iterable.'
               # iterable_globals[global_name] = [tuple(value)]
            # elif isinstance(value, ndarray) or  isinstance(value, list):
               # print global_name + ' is iterable.'            
               # iterable_globals[global_name] = value
            # else:
                # print global_name + ' is not iterable.'
            return raw_globals
                   
    def get_units(self,group=None):
        units_dict = {}
        def append_units(name, obj):
            if 'units' in name:
                temp_dict = dict(obj.attrs)
                for key, val in temp_dict.items():
                    units_dict[key] = val
        with h5py.File(self.h5_path) as h5_file:
            h5_file['globals'].visititems(append_units)
        return units_dict

    def globals_groups(self):
        with h5py.File(self.h5_path) as h5_file:
            try:
                return h5_file['globals'].keys()
            except KeyError:
                return []   
                
    def globals_diff(self, other_run, group=None):
        return globals_diff(self, other_run, group)            
    
        
class Sequence(Run):
    def __init__(self,h5_path,run_paths):
        if isinstance(run_paths, pandas.DataFrame):
            run_paths = run_paths['filepath']
        self.h5_path = h5_path
        self.no_write = False
        with h5py.File(h5_path) as h5_file:
            if not 'results' in h5_file:
                 h5_file.create_group('results')
                 
        self.runs = {path: Run(path,no_write=True) for path in run_paths}
        
        # The group were the results will be stored in the h5 file will
        # be the name of the python script which is instantiating this
        # Sequence object:
        frame = inspect.currentframe()
        try:
            __file__ = frame.f_back.f_locals['__file__']
            self.group = os.path.basename(__file__).split('.py')[0]
            with h5py.File(h5_path) as h5_file:
                if not self.group in h5_file['results']:
                     h5_file['results'].create_group(self.group)
        except KeyError:
            sys.stderr.write('Warning: to write results, call '
            'Sequence.set_group(groupname), specifying the name of the group '
            'you would like to save results to. This normally comes from '
            'the filename of your script, but since you\'re in interactive '
            'mode, there is no scipt name. Opening in read only mode for '
            'the moment.\n')
            self.no_write = True
        
    def get_trace(self,*args):
        return {path:run.get_trace(*args) for run,path in self.runs.items()}
        
    def get_result_array(self,*args):
        return {path:run.get_result_array(*args) for run,path in self.runs.items()}
         
    def get_traces(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
             
    def get_result_arrays(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
     
    def get_image(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')     
