from dataframe_utilities import get_dataframe_from_shot as _get_singleshot
import urllib
import urllib2
import socket
import pickle

import h5py
import pandas

def data(filepath=None, host='localhost'):
    if filepath is not None:
        return _get_singleshot(filepath)
    else:
        port = 42519
        # Workaround to force python not to use IPv6 for the request:
        address  = socket.gethostbyname(host)
        response = urllib2.urlopen('http://%s:%d'%(address,port), timeout=2).read()
        df = pickle.loads(response)
        return df
 
class Run(object):
    def __init__(self,h5_path):
        self.no_write = False
        self.h5_path = h5_path
        with h5py.File(h5_path) as h5_file:
            self.globals = dict(h5_file['globals'].attrs)
            if not 'results' in h5_file:
                 h5_file.create_group('results')
                 
    def get_trace(self,name):
        with h5py.File(self.h5_path) as h5_file:
            if not name in h5_file['data']['traces']:
                raise Exception('The trace \'%s\' doesn not exist'%name)
            trace = h5_file['data']['traces'][name]
            return trace['t'],trace['values']
           
    def get_result_array(self,group,name):
        with h5py.File(self.h5_path) as h5_file:
            if not group in h5_file['results']:
                raise Exception('The result group \'%s\' doesn not exist'%group)
            if not name in h5_file['results'][group]:
                raise Exception('The result array \'%s\' doesn not exist'%name)
            return array(h5_file['results'][group][name])
            
    def save_result(self,group,name,value):
        if self.no_write:
            raise Exception('This run is read-only. '
                            'You can\'t save results to runs through a '
                            'Sequence object. Per-run analysis should be done '
                            'in single-shot analysis routines, in which a '
                            'single Run object is used')
        with h5py.File(self.h5_path,'a') as h5_file:
            if not group in h5_file['results']:
                 h5_file['results'].create_group(group)
            h5_file['results'][group].attrs[name] = value
        
    def save_result_array(self,group,name,data):
        if self.no_write:
            raise Exception('This run is read-only. '
                            'You can\'t save results to runs through a '
                            'Sequence object. Per-run analysis should be done '
                            'in single-shot analysis routines, in which a '
                            'single Run object is used')
        with h5py.File(self.h5_path,'a') as h5_file:
            if not group in h5_file['results']:
                 h5_file['results'].create_group(group)
            if name in h5_file['results'][group]:
                # Overwrite if dataset already exists:
                del h5_file['results'][group][name]
            h5_file['results'][group].create_dataset(name,data=data)

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
        
    def save_results(self,group,*args):
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names,values):
            print name, value
            self.save_result(group,name,value)
        
    def save_result_arrays(self,group, *args):
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names,values):
            self.save_result_array(group,name,value)
    
    def get_image(self,orientation,label,image):
        pass

class Sequence(Run):
    def __init__(self,h5_path,run_paths):
        self.h5_path = h5_path
        self.runs = {path: Run(path,no_write=True) for path in run_paths}
        self.no_write = False
        for run in self.runs.values():
            run.no_write = True
        with h5py.File(h5_path) as h5_file:
            if not 'results' in h5_file:
                 h5_file.create_group('results')
        
    def get_trace(self,*args):
        return {path:run.get_trace(*args) for run,path in self.runs.items()}
        
    def get_result_array(self,*args):
        return {path:run.get_result_array(*args) for run,path in self.runs.items()}
         
    def get_traces(self,*args):
        raise NotImplementedError
             
    def get_result_arrays(self,*args):
        raise NotImplementedError
                
