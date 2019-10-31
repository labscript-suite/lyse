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

from __future__ import division, unicode_literals, print_function, absolute_import
from labscript_utils import PY2
if PY2:
    str = unicode
    
import labscript_utils.h5_lock, h5py
import pandas
import os
from numpy import *
import tzlocal
import labscript_utils.shared_drive
from labscript_utils.dict_diff import dict_diff
from labscript_utils.connections import _ensure_str
from labscript_utils.properties import get_attributes
import runmanager


def asdatetime(timestr):
    if isinstance(timestr, bytes):
        timestr = timestr.decode('utf-8')
    tz = tzlocal.get_localzone().zone
    return pandas.Timestamp(timestr, tz=tz)

def get_nested_dict_from_shot(filepath):
    row = runmanager.get_shot_globals(filepath)
    with h5py.File(filepath,'r') as h5_file:
        if 'results' in h5_file:
            for groupname in h5_file['results']:
                resultsgroup = h5_file['results'][groupname]
                row[groupname] = get_attributes(resultsgroup)
        if 'images' in h5_file:
            for orientation in h5_file['images'].keys():
                if isinstance(h5_file['images'][orientation], h5py.Group):
                    row[orientation] = get_attributes(h5_file['images'][orientation])
                    for label in h5_file['images'][orientation]:
                        row[orientation][label] = {}
                        group = h5_file['images'][orientation][label]
                        for image in group:
                            row[orientation][label][image] = {}
                            for key, val in get_attributes(group[image]).items():
                                if not isinstance(val, h5py.Reference):
                                    row[orientation][label][image][key] = val
        row['filepath'] = _ensure_str(filepath)
        row['agnostic_path'] = labscript_utils.shared_drive.path_to_agnostic(filepath)
        seq_id = _ensure_str(h5_file.attrs['sequence_id'])
        row['sequence'] = asdatetime(seq_id.split('_')[0])
        try:
            row['sequence_index'] = h5_file.attrs['sequence_index']
        except KeyError:
            row['sequence_index'] = None
        if 'script' in h5_file: 
            row['labscript'] = _ensure_str(h5_file['script'].attrs['name'])
        try:
            row['run time'] = asdatetime(_ensure_str(h5_file.attrs['run time']))
        except KeyError:
            row['run time'] = float('nan')
        try:    
            row['run number'] = h5_file.attrs['run number']
        except KeyError:
            row['run number'] = float('nan')
        try:
            row['run repeat'] = h5_file.attrs['run repeat']
        except KeyError:
            row['run repeat'] = 0
        return row
            
def flatten_dict(dictionary, keys=tuple()):
    """Takes a nested dictionary whose keys are strings, and returns a
    flat dictionary whose keys are tuples of strings, each element of
    which is the key for one level of the hierarchy."""
    result = {}
    for name in dictionary:
        if isinstance(dictionary[name],dict):
            flat = flatten_dict(dictionary[name],keys=keys + (name,))
            result.update(flat)
        else:
            result[keys + (name,)] = dictionary[name]
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

def flat_dict_to_flat_series(dictionary):
    result = {}
    for key in dictionary:
        if len(key) > 1:
            result[key] = dictionary[key]
        else:
            result[key[0]] = dictionary[key]
    keys = list(result.keys())
    keys.sort(key = lambda item: 
        (len(item),) + item if isinstance(item, tuple) else (1,item))
    return pandas.Series(result,index=keys)  
          
def get_dataframe_from_shot(filepath):
    nested_dict = get_nested_dict_from_shot(filepath)
    flat_dict =  flatten_dict(nested_dict)
    df = flat_dict_to_hierarchical_dataframe(flat_dict)
    return df
    
def get_dataframe_from_shots(filepaths):
    return concat_with_padding(*[get_dataframe_from_shot(filepath) for filepath in filepaths])

def get_series_from_shot(filepath):
    nested_dict = get_nested_dict_from_shot(filepath)
    flat_dict =  flatten_dict(nested_dict)
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

def concat_with_padding(*dataframes):
    """Concatenates dataframes with MultiIndex column labels,
    padding shallower hierarchies such that the MultiIndexes have
    the same nlevels."""
    dataframes = list(dataframes)
    max_nlevels = max(df.columns.nlevels for df in dataframes)
    # Remove empty dataframes (these don't concat since pandas 0.18) 
    dataframes = [df for df in dataframes if not df.empty]
    for i, df in enumerate(dataframes):
        if df.columns.nlevels < max_nlevels:
            dataframes[i] = pad_columns(df, max_nlevels)
    return pandas.concat(dataframes, ignore_index=True)
    
def replace_with_padding(df, row, index):
    if df.columns.nlevels < row.columns.nlevels:
        df = pad_columns(df, row.columns.nlevels)
    elif df.columns.nlevels > row.columns.nlevels:
        row = pad_columns(row, df.columns.nlevels)

    # Change the index of the row object to equal that of where it is to be
    # inserted:
    row.index = pandas.Int64Index([index])

    # Replace the target row in the dataframe by dropping, appending, then
    # sorting by index:
    df = df.drop([index])
    df = df.append(row)
    df = df.sort_index()
    return df
    

    
