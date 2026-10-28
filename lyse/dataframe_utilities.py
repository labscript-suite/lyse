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
"""Lyse dataframe utilities
"""

import warnings

import labscript_utils.h5_lock, h5py
import numpy as np
import pandas
import tzlocal

# Labscript imports
import labscript_utils.shared_drive
from labscript_utils.connections import _ensure_str
from labscript_utils.properties import get_attributes
from labscript_utils.shot_utils import get_shot_globals
from labscript_utils.labconfig import LabConfig

# lyse imports
from lyse.utils import LABCONFIG

def rangeindex_to_multiindex(df, inplace):
    if isinstance(df.index, pandas.MultiIndex):
        # The dataframe has already been converted.
        return df
    try:
        padding = ('',)*(df.columns.nlevels - 1)
        try:
            integer_indexing = LABCONFIG.getboolean('lyse', 'integer_indexing')
        except (LabConfig.NoOptionError, LabConfig.NoSectionError):
            integer_indexing = False
        if integer_indexing:
            out = df.set_index(['sequence_index', 'run number', 'run repeat'], inplace=inplace, drop=False)
            # out is None if inplace is True, and is the new dataframe is inplace is False.
            if not inplace:
                df = out
        else:
            out = df.set_index([('sequence',) + padding,('run time',) + padding], inplace=inplace, drop=False)
            if not inplace:
                df = out
            df.index.names = ['sequence', 'run time']
    except KeyError:
        # Empty DataFrame or index column not found, so fall back to RangeIndex instead
        pass
    df.sort_index(inplace=True)
    return df

def resolve_column_names(columns, names):
    """Return the labels of the columns that are named by names, in the order of names
    and without repeats. columns is a MultiIndex of column labels, and each name is a
    string or a tuple of strings. A name selects the column with the label that is the
    name followed by empty strings, or if there is no such column, every column with a
    label that begins with the name. Raises KeyError if a name selects no column."""
    labels = []
    missing = []
    for name in names:
        key = (name,) if isinstance(name, str) else tuple(name)
        padded_key = key + ('',) * (columns.nlevels - len(key))
        if padded_key in columns:
            matches = [padded_key]
        else:
            matches = [label for label in columns if label[:len(key)] == key]
        if not matches:
            missing.append(name)
        labels.extend(matches)
    if missing:
        raise KeyError('The dataframe has no columns named ' + ', '.join(map(repr, missing)))
    return list(dict.fromkeys(labels))

def last_sequences(df, n_sequences):
    """Return a boolean array that selects the rows of the dataframe from its n_sequences
    most recent sequences. Shots are from the same sequence if they have the same
    sequence time, labscript and sequence index. The sequence index counts the
    sequences compiled each day, and distinguishes sequences compiled within the same
    second. Sequences are ordered by these three values."""
    padding = ('',) * (df.columns.nlevels - 1)
    keys = [(name,) + padding for name in ['sequence', 'labscript', 'sequence_index']
            if (name,) + padding in df.columns]
    if df.empty or n_sequences == 0:
        return np.zeros(len(df), dtype=bool)
    sequence_numbers = df.groupby(keys, sort=True, dropna=False).ngroup()
    return (sequence_numbers > sequence_numbers.max() - n_sequences).to_numpy()

def asdatetime(timestr):
    """Return the time timestr as a Timestamp in the local time zone. If timestr has no
    UTC offset, it is a local time. A local time in the hour that repeats when daylight
    saving time ends is taken to be in daylight saving time, and a local time in the
    hour that is skipped when it begins is moved forward to the end of that hour."""
    if isinstance(timestr, bytes):
        timestr = timestr.decode('utf-8')
    tz = tzlocal.get_localzone()
    timestamp = pandas.Timestamp(timestr)
    if timestamp.tz is not None:
        return timestamp.tz_convert(tz)
    return timestamp.tz_localize(tz, ambiguous=True, nonexistent='shift_forward')

def get_nested_dict_from_shot(filepath, warn=warnings.warn):
    """Returns the globals, results and attributes of the shot as a nested dictionary.
    warn is called with a message for a results group named like a global, or a global
    or group named like a reserved column."""
    row = get_shot_globals(filepath)
    with h5py.File(filepath,'r') as h5_file:
        # Attributes of the groups under 'data' and 'results' share one namespace in the
        # dataframe, keyed by group name. A results attribute overrides a data attribute
        # of the same name, and a group replaces a global of the same name.
        groups = {}
        for toplevel in ('data', 'results'):
            if toplevel in h5_file:
                for groupname, group in h5_file[toplevel].items():
                    groups.setdefault(groupname, {}).update(get_attributes(group))
        for groupname in groups:
            if groupname in row:
                warn(f"{filepath}: the group '{groupname}' has the same name as a global; "
                     "the global is not included in the dataframe")
        row.update(groups)
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
        shot = {}
        shot['filepath'] = _ensure_str(filepath)
        shot['agnostic_path'] = labscript_utils.shared_drive.path_to_agnostic(filepath)
        seq_id = _ensure_str(h5_file.attrs['sequence_id'])
        shot['sequence'] = asdatetime(seq_id.split('_')[0])
        try:
            shot['sequence_index'] = h5_file.attrs['sequence_index']
        except KeyError:
            shot['sequence_index'] = None
        if 'script' in h5_file: 
            shot['labscript'] = _ensure_str(h5_file['script'].attrs['name'])
        try:
            shot['run time'] = asdatetime(_ensure_str(h5_file.attrs['run time']))
        except KeyError:
            shot['run time'] = float('nan')
        try:    
            shot['run number'] = h5_file.attrs['run number']
        except KeyError:
            shot['run number'] = float('nan')
        try:
            shot['run repeat'] = h5_file.attrs['run repeat']
        except KeyError:
            shot['run repeat'] = 0
        try:
            shot['n_runs'] = h5_file.attrs['n_runs']
        except KeyError:
            shot['n_runs'] = float('nan')
        for name in shot:
            if name in row:
                warn(f"{filepath}: '{name}' is a reserved column name; the global or group "
                     "of that name is not included in the dataframe")
        row.update(shot)
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
          
def get_dataframe_from_shot(filepath, warn=warnings.warn):
    nested_dict = get_nested_dict_from_shot(filepath, warn)
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
