#####################################################################
#                                                                   #
# /dataframe_utilities.py                                           #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

import h5_lock, h5py
import pandas
import os
from numpy import *
import dateutil
from timezones import localtz

import shared_drive

# asdatetime = dateutil.parser.parse

# def asdatetime(timestr):
#     return localtz().localize(dateutil.parser.parse(timestr))

def asdatetime(timestr):
    # tz = localtz().zone
    tz = 'Australia/Melbourne'
    # tz = None
    return pandas.Timestamp(timestr, tz=tz)

class Fields(object):
    """A workaraound for the fact that numpy.void objects cannot be
    correctly unpickled (a bug in numpy) and therefore cannot be sent
    to other processes over the network. This class implements the same
    functionality mostly. Basically the thing you get back looks like a
    tuple but can be indexed with either names of the fields or integers,
    much like a single row of a numpy structured array. Whenever this
    module encounters a numpy.void type when reading attributes from a
    HDF5 file, it converts it to one of these."""

    def __init__(self, data):
        self.data_by_name = {}
        self.data_by_index = tuple(data)
        self.dtype = data.dtype
        for name in data.dtype.names:
            self.data_by_name[name] = data[name]
            
    def __getitem__(self, key):
        if isinstance(key,int):
            return self.data_by_index[key]
        else:
            return self.data_by_name[key]
            
    def __repr__(self):
        return str(self.data_by_index)
        

def get_nested_dict_from_shot(filepath):
    with h5py.File(filepath,'r') as h5_file:
        row = dict(h5_file['globals'].attrs)
        if 'results' in h5_file:
            for groupname in h5_file['results']:
                resultsgroup = h5_file['results'][groupname]
                row[groupname] = dict(resultsgroup.attrs)
        if 'images' in h5_file:
            for orientation in h5_file['images'].keys():
                row[orientation] = dict(h5_file['images'][orientation].attrs)
                for label in h5_file['images'][orientation]:
                    row[orientation][label] = {}
                    group = h5_file['images'][orientation][label]
                    for image in group:
                        row[orientation][label][image] = dict(
                            group[image].attrs)
        row['filepath'] = filepath
        row['agnostic_path'] = shared_drive.path_to_local(filepath)
        row['sequence'] = asdatetime(h5_file.attrs['sequence_id'].split('_')[0])        
        if 'script' in h5_file: 
            row['labscript'] = h5_file['script'].attrs['name']
        try:
            row['run time'] = asdatetime(h5_file.attrs['run time'])
        except KeyError:
            row['run time'] = float('nan')
        try:    
            row['run number'] = h5_file.attrs['run number']
        except KeyError:
            # ignore:
            pass
        try:
            row['individual id'] = h5_file.attrs['individual id']
            row['generation'] = h5_file.attrs['generation']
        except KeyError:
            pass
        return row
            
def flatten_dict(dictionary, keys=tuple()):
    """Takes a nested dictionary whose keys are strings, and returns a
    flat dictionary whose keys are tuples of strings, each element of
    which is the key for one level of the hierarchy."""
    result = {}
    for name in dictionary:
        if isinstance(dictionary[name],dict):
            flat = flatten_dict(dictionary[name],keys=keys + (str(name),))
            result.update(flat)
        else:
            result[keys + (str(name),)] = dictionary[name]
    return result
            
def flat_dict_to_hierarchical_dataframe(dictionary):
    """Make all the keys tuples of the same length"""
    max_tuple_length = 2 # Must have at least two levels to make a MultiIndex
    for key in dictionary:
        max_tuple_length = max(max_tuple_length,len(key))
    result = {}
    for key in dictionary:
        newkey = key[:]
        while len(newkey) < max_tuple_length:
            newkey += ('',)
        result[newkey] = dictionary[key]    
    index = pandas.MultiIndex.from_tuples(sorted(result.keys()))
    return pandas.DataFrame([result],columns=index)  

def workaround_empty_string_bug(dictionary):
    # It doesn't look like this function does anything, but it does. It
    # converts numpy empty strings to python empty strings. This is
    # to workaround the fact that h5py returns empty stings as a numpy
    # datatype which numpy itself actually can'y handle. Numpy never uses
    # length zero strings, only length one or greater. So by replacing
    # all empty strings with ordinary python ones, numpy will convert them
    # (when it needs to) to a datatype it can handle.
    for key, value in dictionary.items():
        if isinstance(value,str) and value == '':
            dictionary[key] = ''
            
def workaround_numpy_void_bug(dictionary):
    # numpy.void objects undergo data corruption when pickled and
    # unpickled.  h5py returns numpy.void objects for attributes
    # which are its 'compound' datatype.  We'll convert any we find to our
    # home-cooked Fields class (defined above), which provides mostly
    # the same functionality. This will be removed if and when numpy fix their bug.
    for key, value in dictionary.items():
        if isinstance(value, void):
            dictionary[key] = Fields(value)

def do_workarounds(dictionary):
    workaround_empty_string_bug(dictionary)
    #workaround_numpy_void_bug(dictionary)
    
def flat_dict_to_flat_series(dictionary):
    max_tuple_length = 2 # Must have at least two levels to make a MultiIndex
    result = {}
    for key in dictionary:
        if len(key) > 1:
            result[key] = dictionary[key]
        else:
            result[key[0]] = dictionary[key]
    keys = result.keys()
    keys.sort(key = lambda item: 
        (len(item),) + item if isinstance(item, tuple) else (1,item))
    return pandas.Series(result,index=keys)  
          
def get_dataframe_from_shot(filepath):
    nested_dict = get_nested_dict_from_shot(filepath)
    flat_dict =  flatten_dict(nested_dict)
    do_workarounds(flat_dict)
    df = flat_dict_to_hierarchical_dataframe(flat_dict)
    return df
    
def get_series_from_shot(filepath):
    nested_dict = get_nested_dict_from_shot(filepath)
    flat_dict =  flatten_dict(nested_dict)
    do_workarounds(flat_dict)
    s = flat_dict_to_flat_series(flat_dict)
    return s
    
def pad_columns(df, n):
    """Add depth to hiererchical column labels with empty strings"""
    if df.columns.nlevels == n:
        return df
    new_columns = []
    data = {}
    for column in df.columns:
        new_column = column + ('',)*(n-len(column))
        new_columns.append(new_column)
        data[new_column] = df[column]
    index = pandas.MultiIndex.from_tuples(new_columns)
    return pandas.DataFrame(data,columns = index)

def concat_with_padding(df1, df2):
    """Concatenates two dataframes with MultiIndex column labels,
    padding the shallower hierarchy such that the two MultiIndexes have
    the same nlevels."""
    if df1.columns.nlevels < df2.columns.nlevels:
        df1 = pad_columns(df1, df2.columns.nlevels)
    elif df1.columns.nlevels > df2.columns.nlevels:
        df2 = pad_columns(df2, df1.columns.nlevels)
    return df1.append(df2, ignore_index=True)
    
def replace_with_padding(df,row,index):
    if df.columns.nlevels < row.columns.nlevels:
        df = pad_columns(df, row.columns.nlevels)
    elif df.columns.nlevels > row.columns.nlevels:
        row = pad_columns(row, df.columns.nlevels)
    # Wow, changing the index of a single row dataframe is a pain in
    # the neck:
    row = pandas.DataFrame(row.ix[0],columns=[index]).T
    # Wow, replacing a row of a dataframe is a pain in the neck:
    df = df.drop([index])
    df = df.append(row)
    df = df.sort()
    return df
    
def dict_diff(dict1, dict2):
    """Return the difference between two dictionaries as a dictionary of key: [val1, val2] pairs.
    Keys unique to either dictionary are included as key: [val1, '-'] or key: ['-', val2]."""
    diff_keys = []
    common_keys = intersect1d(dict1.keys(), dict2.keys())
    for key in common_keys:
        if iterable(dict1[key]):
            if any(dict1[key] != dict2[key]):
                diff_keys.append(key)
        else:
            if dict1[key] != dict2[key]:
                diff_keys.append(key)

    dict1_unique = [key for key in dict1.keys() if key not in common_keys]    
    dict2_unique = [key for key in dict2.keys() if key not in common_keys]
                
    diff = {}
    for key in diff_keys:
        diff[key] = [dict1[key], dict2[key]]
    
    for key in dict1_unique:
        diff[key] = [dict1[key], '-']
        
    for key in dict2_unique:
        diff[key] = ['-', dict2[key]]       

    return diff
    
