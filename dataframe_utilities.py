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

import labscript_utils.h5_lock, h5py
import pandas
import os
from numpy import *
import tzlocal
import labscript_utils.shared_drive

import runmanager

def asdatetime(timestr):
    tz = tzlocal.get_localzone().zone
    return pandas.Timestamp(timestr, tz=tz)

def get_nested_dict_from_shot(filepath):
    row = runmanager.get_shot_globals(filepath)
    with h5py.File(filepath,'r') as h5_file:
        if 'results' in h5_file:
            for groupname in h5_file['results']:
                resultsgroup = h5_file['results'][groupname]
                row[groupname] = dict(resultsgroup.attrs)
        if 'images' in h5_file:
            for orientation in h5_file['images'].keys():
                if isinstance(h5_file['images'][orientation], h5py.Group):
                    row[orientation] = dict(h5_file['images'][orientation].attrs)
                    for label in h5_file['images'][orientation]:
                        row[orientation][label] = {}
                        group = h5_file['images'][orientation][label]
                        for image in group:
                            row[orientation][label][image] = {}
                            for key, val in group[image].attrs.items():
                                if not isinstance(val, h5py.Reference):
                                    row[orientation][label][image][key] = val
        row['filepath'] = filepath
        row['agnostic_path'] = labscript_utils.shared_drive.path_to_agnostic(filepath)
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
    # Remove empty dataframes (these don't concat since pandas 0.18) 
    dataframes = [df for df in dataframes if not df.empty]
    max_nlevels = max(df.columns.nlevels for df in dataframes)
    for i, df in enumerate(dataframes):
        if df.columns.nlevels < max_nlevels:
            dataframes[i] = pad_columns(df, max_nlevels)
    return pandas.concat(dataframes, ignore_index=True)
    
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
    
