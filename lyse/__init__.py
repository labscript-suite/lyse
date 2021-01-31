#####################################################################
#                                                                   #
# /__init__.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

from lyse.dataframe_utilities import get_series_from_shot as _get_singleshot
from labscript_utils.dict_diff import dict_diff
import copy
import os
import socket
import pickle as pickle
import inspect
import sys
import threading
import warnings

import labscript_utils.h5_lock, h5py
from labscript_utils.labconfig import LabConfig
import pandas
from numpy import array, ndarray
import types

from .__version__ import __version__

from labscript_utils import dedent
from labscript_utils.ls_zprocess import zmq_get

from labscript_utils.properties import get_attributes, get_attribute, set_attributes
LYSE_DIR = os.path.dirname(os.path.realpath(__file__))

# If running stand-alone, and not from within lyse, the below two variables
# will be as follows. Otherwise lyse will override them with spinning_top =
# True and path <name of hdf5 file being analysed>:
spinning_top = False
# data to be sent back to the lyse GUI if running within lyse
_updated_data = {}
# dictionary of plot id's to classes to use for Plot object
_plot_classes = {}
# A fake Plot object to subclass if we are not running in the GUI
Plot=object
# An empty dictionary of plots (overwritten by the analysis worker if running within lyse)
plots = {}
# A threading.Event to delay the 
delay_event = threading.Event()
# a flag to determine whether we should wait for the delay event
_delay_flag = False

# get port that lyse is using for communication
try:
    _labconfig = LabConfig(required_params={"ports": ["lyse"]})
    _lyse_port = int(_labconfig.get('ports', 'lyse'))
except Exception:
    _lyse_port = 42519

if len(sys.argv) > 1:
    path = sys.argv[1]
else:
    path = None


class _RoutineStorage(object):
    """An empty object that analysis routines can store data in. It will
    persist from one run of an analysis routine to the next when the routine
    is being run from within lyse. No attempt is made to store data to disk,
    so if the routine is run multiple times from the command line instead of
    from lyse, or the lyse analysis subprocess is restarted, data will not be
    retained. An alternate method should be used to store data if desired in
    these cases."""

routine_storage = _RoutineStorage()


def data(filepath=None, host='localhost', port=_lyse_port, timeout=5, n_sequences=None, filter_kwargs=None):
    """Get data from the lyse dataframe or a file.
    
    This function allows for either extracting information from a shots's hdf5
    file, or retrieving data from lyse's dataframe. If `filepath` is provided
    then data will be read from that file and returned as a pandas series. If
    `filepath` is not provided then the dataframe in lyse, or a portion of it,
    will be returned.
    
    Often only part of the lyse dataframe is needed, so the `n_sequences` and
    `filter_kwargs` arguments provide ways to restrict what parts of the lyse
    dataframe are returned. The dataframe can be quite large, so only requesting
    a small part of it can speed up the execution of `lyse.data()` noticeably.
    Setting `n_sequences` makes this function return only the rows of the lyse
    dataframe that correspond to the `n_sequences` most recent sequences, where
    one sequence corresponds to one call to engage in runmanager. Additionally,
    the `Dataframe.filter()` method can be called on the dataframe before it is
    transmitted, and the arguments specified in `filter_kwargs` are passed to
    that method.

    Args:
        filepath (str, optional): The path to a shot's hdf5 file. If a value
            other than `None` is provided, then this function will return a
            pandas series containing data associated with the shot. In particular
            it will contain the globals, singleshot results, multishot results,
            etc. that would appear in the shot's row in the Lyse dataframe, but
            the values will be read from the file rather than extracted from the
            lyse dataframe. If `filepath` is `None, then this function will
            instead return a section of the lyse dataframe. Note that if
            `filepath` is not None, then the other arguments will be ignored.
            Defaults to `None`.
        host (str, optional): The address of the computer running lyse. Defaults
            to `'localhost'`.
        port (int, optional): The port on which lyse is listening. Defaults to
            the entry for lyse's port in the labconfig, with a fallback value of
            42519 if the labconfig has no such entry.
        timeout (float, optional): The timeout, in seconds, for the
            communication with lyse. Defaults to 5.
        n_sequences (int, optional): The maximum number of sequences to include
            in the returned dataframe where one sequence corresponds to one call
            to engage in runmanager. The dataframe rows for the most recent
            `n_sequences` sequences are returned. If the dataframe contains
            fewer than `n_sequences` sequences, then all rows will be returned.
            If set to `None`, then all rows are returned. Defaults to `None`.
        filter_kwargs (dict, optional): A dictionary of keyword arguments to
            pass to the `Dataframe.filter()` method before the lyse dataframe is
            returned. For example to call `filter()` with `like='temperature'`,
            set `filter_kwargs` to `{'like':'temperature'}`. If set to `None`
            then `Dataframe.filter()` will not be called. See
            :meth:`pandas:pandas.DataFrame.filter` for more information.
            Defaults to `None`.

    Raises:
        ValueError: If `n_sequences` isn't `None` or a nonnegative integer, then
            a `ValueError` is raised. Note that no `ValueError` is raised if
            `n_sequences` is greater than the number of sequences available. In
            that case as all available sequences are returned, i.e. the entire
            lyse dataframe is returned.

    Returns:
        :obj:`pandas:pandas.DataFrame` or :obj:`pandas:pandas.Series`: If
        `filepath` is provided, then a pandas series with the data read from
        that file is returned. If `filepath` is omitted or set to `None` then
        the lyse dataframe, or a subset of it, is returned.
    """    
    if filepath is not None:
        return _get_singleshot(filepath)
    else:
        if n_sequences is not None:
            if not (type(n_sequences) is int and n_sequences >= 0):
                msg = """n_sequences must be None or an integer greater than 0 but 
                    was {n_sequences}.""".format(n_sequences=n_sequences)
                raise ValueError(dedent(msg))
        if filter_kwargs is not None:
            if type(filter_kwargs) is not dict:
                msg = """filter must be None or a dictionary but was 
                    {filter_kwargs}.""".format(filter_kwargs=filter_kwargs)
                raise ValueError(dedent(msg))

        # Allow sending 'get dataframe' (without the enclosing list) if
        # n_sequences and filter_kwargs aren't provided. This is for backwards
        # compatability in case the server is running an outdated version of
        # lyse.
        if n_sequences is None and filter_kwargs is None:
            command = 'get dataframe'
        else:
            command = ('get dataframe', n_sequences, filter_kwargs)
        df = zmq_get(port, host, command, timeout)
        if isinstance(df, str) and df.startswith('error: operation not supported'):
            # Sending a tuple for command to an outdated lyse servers causes it
            # to reply with an error message.
            msg = """The lyse server does not support n_sequences or filter_kwargs.
                Call this function without providing those arguments to communicate
                with this server, or upgrade the version of lyse running on the
                server."""
            raise ValueError(dedent(msg))
        # Ensure conversion to multiindex is done, which needs to be done here
        # if the server is running an outdated version of lyse.
        _rangeindex_to_multiindex(df, inplace=True)
        return df

def _rangeindex_to_multiindex(df, inplace):
    if isinstance(df.index, pandas.MultiIndex):
        # The dataframe has already been converted.
        return df
    try:
        padding = ('',)*(df.columns.nlevels - 1)
        try:
            integer_indexing = _labconfig.getboolean('lyse', 'integer_indexing')
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

def globals_diff(shot1, shot2, group=None):
    return dict_diff(shot1.get_globals(group), shot2.get_globals(group))
 
class Shot(object):
    """A class for saving/retrieving data to/from a shot's hdf5 file.

    This class implements methods that allow the user to retrieve data from a
    shot's hdf5 file such as images, traces, and the values of globals. It also
    provides methods for saving and retrieving results from analysis.

    Args:
        h5_path (str): The path, including file name and extension, to the hdf5
            file for a shot.
        no_write (bool, optional): Set to `True` to prevent editing the shot's
            hdf5 file. Note that doing so prohibits the ability to save results
            to the file. Defaults to `False`.
    """
    def __init__(self,h5_path,no_write=False):
        self.__h5_path = h5_path
        self.__no_write = no_write
        self.__group = None
        if not self.no_write:
            self._create_group_if_not_exists(h5_path, '/', 'results')
                     
        try:
            if not self.no_write:
                # The group where this shot's results will be stored in the h5
                # file will be the name of the python script which is
                # instantiating this Shot object. Iterate from innermost caller
                # to outermost. The name of the script will be one frame in
                # from analysis_subprocess.py.
                analysis_subprocess_path = os.path.join(
                    LYSE_DIR,
                    'analysis_subprocess.py',
                )
                group = None
                inner_frame = inspect.currentframe()
                inner_path = self._frame_to_path(inner_frame)
                inner_file_name = self._path_to_file_name(inner_path)
                while group is None:
                    # self._frame_to_path() will raise a KeyError if this loop
                    # reaches the outermost caller.
                    outer_frame = inner_frame.f_back
                    outer_path = self._frame_to_path(outer_frame)
                    outer_file_name = self._path_to_file_name(outer_path)
                    if outer_path == analysis_subprocess_path:
                        group = inner_file_name
                    inner_frame = outer_frame
                    inner_path = outer_path
                    inner_file_name = outer_file_name
                self.set_group(group)
        except KeyError:
            # sys.stderr.write('Warning: to write results, call '
            # 'Shot.set_group(groupname), specifying the name of the group '
            # 'you would like to save results to. This normally comes from '
            # 'the filename of your script, but since you\'re in interactive '
            # 'mode, there is no script name.\n')
            pass

    def _frame_to_path(self, frame):
        path = frame.f_globals['__file__']
        return path

    def _path_to_file_name(self, path):
        file_name = os.path.basename(path).split('.py')[0]
        return file_name

    @property
    def h5_path(self):
        """str: The value provided for `h5_path` during instantiation."""
        return self.__h5_path

    @property
    def no_write(self):
        """bool: The value provided for `no_write` during instantiation."""
        return self.__no_write

    @property
    def group(self):
        """str: The group in the hdf5 file in which results are saved by default.
        
        When a `Shot` instance is created from within a lyse singleshot or
        multishot routine, `group` will be set to the name of the running
        routine. If created from outside a lyse script it will be set to `None`.
        To change the default group for saving results, use the `set_group()`
        method. Note that if `self.group` is `None` and no value is provided for
        the optional `group` argument used by the `save...()` methods, a
        `ValueError` will be raised.
        
        Attempting to directly set `self.group`'s value will automatically call
        `self.set_group()`.
        """
        return self.__group

    @group.setter
    def group(self, value):
        self.set_group(value)

    def _create_group_if_not_exists(self, h5_path, location, groupname):
        """Creates a group in the HDF5 file at `location` if it does not exist.
        
        Only opens the h5 file in write mode if a group must be created.
        This ensures the last modified time of the file is only updated if
        the file is actually written to."""
        create_group = False
        with h5py.File(h5_path, 'r') as h5_file:
            if not groupname in h5_file[location]:
                create_group = True
        if create_group:
            if self.no_write:
                msg = "Cannot create group; this shot is read-only."
                raise PermissionError(msg)
            with h5py.File(h5_path, 'r+') as h5_file:
                # Catch the ValueError raised if the group was created by
                # something else between the check above and now. 
                try:
                    h5_file[location].create_group(groupname)
                except ValueError:
                    pass

    def set_group(self, groupname):
        """Set the default hdf5 file group for saving results.

        The `save...()` methods will save their results to `self.group` if an
        explicit value for their optional `group` argument is not given. This
        method updates `self.group`, making sure to create the group in the hdf5
        file if it does not already exist.

        Args:
            groupname (str): The name of the hdf5 file group in which to save
                results by default. The group will be created in the
                `'/results'` group of the hdf5 file.
        """
        self._create_group_if_not_exists(self.h5_path, '/results', groupname)
        self.__group = groupname

    def trace_names(self):
        with h5py.File(self.h5_path, 'r') as h5_file:
            try:
                return list(h5_file['data']['traces'].keys())
            except KeyError:
                return []
                
    def get_attrs(self, group):
        """Returns all attributes of the specified group as a dictionary."""
        with h5py.File(self.h5_path, 'r') as h5_file:
            if not group in h5_file:
                raise Exception('The group \'%s\' does not exist'%group)
            return get_attributes(h5_file[group])

    def get_trace(self,name):
        with h5py.File(self.h5_path, 'r') as h5_file:
            if not name in h5_file['data']['traces']:
                raise Exception('The trace \'%s\' does not exist'%name)
            trace = h5_file['data']['traces'][name]
            return array(trace['t'],dtype=float),array(trace['values'],dtype=float)         

    def get_result_array(self,group,name):
        with h5py.File(self.h5_path, 'r') as h5_file:
            if not group in h5_file['results']:
                raise Exception('The result group \'%s\' does not exist'%group)
            if not name in h5_file['results'][group]:
                raise Exception('The result array \'%s\' does not exist'%name)
            return array(h5_file['results'][group][name])
            
    def get_result(self, group, name):
        """Return 'result' in 'results/group' that was saved by 
        the save_result() method."""
        with h5py.File(self.h5_path, 'r') as h5_file:
            if not group in h5_file['results']:
                raise Exception('The result group \'%s\' does not exist'%group)
            if not name in h5_file['results'][group].attrs.keys():
                raise Exception('The result \'%s\' does not exist'%name)
            return get_attribute(h5_file['results'][group], name)
            
    def get_results(self, group, *names):
        """Iteratively call get_result(group,name) for each name provided.
        Returns a list of all results in same order as names provided."""
        results = []
        for name in names:
            results.append(self.get_result(group,name))
        return results        
            
    def save_result(self, name, value, group=None, overwrite=True):
        """Save a result to the hdf5 file.

        With the default argument values this method saves to `self.group` in
        the `'/results'` group and overwrites any existing value. Note that the
        result is saved as an attribute and overwriting attributes causes hdf5
        file size bloat.

        Args:
            name (str): The name of the result. This will be the name of the
                attribute added to the hdf5 file's group.
            value (any): The value of the result, which will be saved as the
                value of the hdf5 group's attribute set by `name`. However note
                that when saving large arrays, it is better to use the
                `self.save_result_array()` method which will store the results
                as a dataset in the hdf5 file.
            group (str, optional): The group in the hdf5 file to which the
                result will be saved as an attribute. If set to `None`, then the
                result will be saved to `self.group` in `'/results'`. Note that
                if a value is passed for `group` here then it will NOT have
                `'/result'` prepended to it which allows the caller to save
                results anywhere in the hdf5 file. This is in contrast to using
                the default group set with `self.set_group()`; when the default
                group is set with that method it WILL have `'/results'`
                prepended to it when saving results. Defaults to `None`.
            overwrite (bool, optional): Sets whether or not to overwrite the
                previous value if the attribute already exists. If set to
                `False` and the attribute already exists, a `PermissionError` is
                raised. Defaults to `True`.

        Raises:
            PermissionError: A `PermissionError` is raised if `self.no_write` is
                `True` because saving the result would edit the file.
            ValueError: A `ValueError` is raised if `self.group` is `None` and
                no value is provided for `group` because the method then doesn't
                know where to save the result.
            PermissionError: A `PermissionError` is raised if an attribute with
                name `name` already exists but `overwrite` is set to `False`.
        """
        if self.no_write:
            msg = "Cannot save result; this instance is read-only."
            raise PermissionError(msg)
        with h5py.File(self.h5_path,'a') as h5_file:
            if not group:
                if self.group is None:
                    msg = """Cannot save result; no default group set. Either
                        specify a value for this method's optional group
                        argument, or set a default value using the set_group()
                        method."""
                    raise ValueError(dedent(msg))
                # Save to analysis results group by default
                group = 'results/' + self.group
            elif not group in h5_file:
                # Create the group if it doesn't exist
                h5_file.create_group(group) 
            if name in h5_file[group].attrs and not overwrite:
                msg = """Cannot save result; group '{group}' already has
                    attribute '{name}' and overwrite is set to False. Set
                    overwrite=True to overwrite the existing value.""".format(
                        group=group,
                        name=name,
                    )
                raise PermissionError(dedent(msg))
            set_attributes(h5_file[group], {name: value})
            
        if spinning_top:
            if self.h5_path not in _updated_data:
                _updated_data[self.h5_path] = {}
            if group.startswith('results'):
                toplevel = group.replace('results/', '', 1)
                _updated_data[self.h5_path][toplevel, name] = value

    def save_result_array(self, name, data, group=None, 
                          overwrite=True, keep_attrs=False, **kwargs):
        """Save an array of data to the hdf5 h5 file.

        With the default argument values this method saves to `self.group` in
        the `'/results'` group and overwrites any existing value without keeping
        the dataset's previous attributes. Additional keyword arguments are
        passed directly to `h5py.create_dataset()`.

        Args:
            name (str): The name of the result. This will be the name of the
                dataset added to the hdf5 file.
            data (:obj:`numpy:numpy.array`): The data to save to the hdf5 file.
            group (str, optional): The group in the hdf5 file in which the
                result will be saved as a dataset. If set to `None`, then the
                result will be saved in `self.group` in `'/results'`. Note that
                if a value is passed for `group` here then it will NOT have
                `'/result'` prepended to it which allows the caller to save
                results anywhere in the hdf5 file. This is in contrast to using
                the default group set with `self.set_group()`; when the default
                group is set with that method it WILL have `'/results'`
                prepended to it when saving results. Defaults to `None`..
            overwrite (bool, optional): Sets whether or not to overwrite the
                previous value if the dataset already exists. If set to
                `False` and the dataset already exists, a `PermissionError` is
                raised. Defaults to `True`.
            keep_attrs (bool, optional): Whether or not to keep the dataset's
                attributes when overwriting it, i.e. if the dataset already
                existed. Defaults to `False`.

        Raises:
            PermissionError: A `PermissionError` is raised if `self.no_write` is
                `True` because saving the result would edit the file.
            ValueError: A `ValueError` is raised if `self.group` is `None` and
                no value is provided for `group` because the method then doesn't
                know where to save the result.
            PermissionError: A `PermissionError` is raised if a dataset with
                name `name` already exists but `overwrite` is set to `False`.
        """
        if self.no_write:
            msg = "Cannot save result; this instance is read-only."
            raise PermissionError(msg)
        with h5py.File(self.h5_path, 'a') as h5_file:
            attrs = {}
            if not group:
                if self.group is None:
                    msg = """Cannot save result; no default group set. Either
                        specify a value for this method's optional group
                        argument, or set a default value using the set_group()
                        method."""
                    raise ValueError(dedent(msg))
                # Save dataset to results group by default
                group = 'results/' + self.group
            elif not group in h5_file:
                # Create the group if it doesn't exist
                h5_file.create_group(group) 
            if name in h5_file[group]:
                if overwrite:
                    # Overwrite if dataset already exists
                    if keep_attrs:
                        attrs = dict(h5_file[group][name].attrs)
                    del h5_file[group][name]
                else:
                    msg = """Cannot save result; group '{group}' already has
                        dataset '{name}' and overwrite is set to False. Set
                        overwrite=True to overwrite the existing
                        value.""".format(
                            group=group,
                            name=name,
                        )
                    raise PermissionError(dedent(msg))
            h5_file[group].create_dataset(name, data=data, **kwargs)
            for key, val in attrs.items():
                h5_file[group][name].attrs[key] = val

    def get_traces(self, *names):
        traces = []
        for name in names:
            traces.extend(self.get_trace(name))
        return traces
             
    def get_result_arrays(self, group, *names):
        results = []
        for name in names:
            results.append(self.get_result_array(group, name))
        return results
        
    def save_results(self, *args, **kwargs):
        """Save multiple results to the hdf5 file.

        This method Iteratively call `self.save_result()` on multiple results.
        It assumes arguments are ordered such that each result to be saved is
        preceded by the name of the attribute to save it under. Keywords
        arguments are passed to each call of `self.save_result()`.

        Args:
            *args: The names and values of results to be saved. The first entry
                should be a string giving the name of the first result, and the
                second entry should be the value for that result. After that,
                an arbitrary number of additional pairs of result name strings
                and values can be included, e.g.
                `'name0', value0, 'name1', value1,...`.
            **kwargs: Keyword arguments are passed to `self.save_result()`. Note
                that the names and values of keyword arguments are NOT saved as
                results to the hdf5 file; they are only used to provide values
                for the optional arguments of `self.save_result()`.

        Examples:
            >>> shot = Shot('path/to/an/hdf5/file.h5')  # doctest: +SKIP
            >>> a = 5
            >>> b = 2.48
            >>> shot.save_results('result', a, 'other_result', b, overwrite=False)  # doctest: +SKIP
        """
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names, values):
            self.save_result(name, value, **kwargs)
            
    def save_results_dict(self, results_dict, uncertainties=False, **kwargs):
        for name, value in results_dict.items():
            if not uncertainties:
                self.save_result(name, value, **kwargs)
            else:
                self.save_result(name, value[0], **kwargs)
                self.save_result('u_' + name, value[1], **kwargs)

    def save_result_arrays(self, *args, **kwargs):
        """Iteratively call save_result_array() on multiple data sets. 
        Assumes arguments are ordered such that each dataset to be saved is 
        preceded by the name to save it as. 
        All keyword arguments are passed to each call of save_result_array()."""
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names, values):
            self.save_result_array(name, value, **kwargs)
    
    def get_image(self,orientation,label,image):
        with h5py.File(self.h5_path, 'r') as h5_file:
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
        
    def get_all_image_labels(self):
        images_list = {}
        with h5py.File(self.h5_path, 'r') as h5_file:
            for orientation in h5_file['/images'].keys():
                images_list[orientation] = list(h5_file['/images'][orientation].keys())               
        return images_list                
    
    def get_image_attributes(self, orientation):
        with h5py.File(self.h5_path, 'r') as h5_file:
            if not 'images' in h5_file:
                raise Exception('File does not contain any images')
            if not orientation in h5_file['images']:
                raise Exception('File does not contain any images with orientation \'%s\''%orientation)
            return get_attributes(h5_file['images'][orientation])

    def get_globals(self,group=None):
        if not group:
            with h5py.File(self.h5_path, 'r') as h5_file:
                return dict(h5_file['globals'].attrs)
        else:
            try:
                with h5py.File(self.h5_path, 'r') as h5_file:
                    return dict(h5_file['globals'][group].attrs)
            except KeyError:
                return {}

    def get_globals_raw(self, group=None):
        globals_dict = {}
        with h5py.File(self.h5_path, 'r') as h5_file:
            if group == None:
                for obj in h5_file['globals'].values():
                    temp_dict = dict(obj.attrs)
                    for key, val in temp_dict.items():
                        globals_dict[key] = val
            else:
                globals_dict = dict(h5_file['globals'][group].attrs)
        return globals_dict
        
    # def iterable_globals(self, group=None):
        # raw_globals = self.get_globals_raw(group)
        # print raw_globals.items()
        # iterable_globals = {}
        # for global_name, expression in raw_globals.items():
            # print expression
            # # try:
                # # sandbox = {}
                # # exec('from pylab import *',sandbox,sandbox)
                # # exec('from runmanager.functions import *',sandbox,sandbox)
                # # value = eval(expression,sandbox)
            # # except Exception as e:
                # # raise Exception('Error parsing global \'%s\': '%global_name + str(e))
            # # if isinstance(value,types.GeneratorType):
               # # print global_name + ' is iterable.'
               # # iterable_globals[global_name] = [tuple(value)]
            # # elif isinstance(value, ndarray) or  isinstance(value, list):
               # # print global_name + ' is iterable.'            
               # # iterable_globals[global_name] = value
            # # else:
                # # print global_name + ' is not iterable.'
            # return raw_globals
            
    def get_globals_expansion(self):
        expansion_dict = {}
        def append_expansion(name, obj):
            if 'expansion' in name:
                temp_dict = dict(obj.attrs)
                for key, val in temp_dict.items():
                    if val:
                        expansion_dict[key] = val
        with h5py.File(self.h5_path, 'r') as h5_file:
            h5_file['globals'].visititems(append_expansion)
        return expansion_dict
                   
    def get_units(self, group=None):
        """Get the units of globals.

        This method retrieves the values in the "Units" column of runmanager for
        this shot. The values are returned in a dictionary where the keys are
        the names of globals and the values are the corresponding units.

        Args:
            group (str, optional): The name of the globals group for which the
                units will be retrieved. Globals and units from other globals
                groups will not be included in the returned dictionary. If set
                to `None` then all globals from all globals groups will be
                returned. If `group` is set to a value that isn't the name of a
                globals group, then an empty dictionary will be returned, but no
                error will be raised. Defaults to `None`.

        Returns:
            dict: A dictionary in which each key is a string giving the name of
            a global, and each value is a string specifying the corresponding
            value in the "Units" column of runmanager. An empty dictionary will
            be returned if `group` is set to a value that isn't the name of a
            globals group.
        """
        path = 'globals'
        if group is not None:
            path = path + '/{group}'.format(group=group)
        units = {}
        # Define method that when applied to an hdf5 group adds all of its
        # globals and units to the units dict.
        def append_units(name, obj):
            if 'units' in name:
                units.update(dict(obj.attrs))
        try:
            with h5py.File(self.h5_path, 'r') as h5_file:
                h5_file[path].visititems(append_units)
        except KeyError:
            pass
        return units

    def globals_groups(self):
        with h5py.File(self.h5_path, 'r') as h5_file:
            try:
                return list(h5_file['globals'].keys())
            except KeyError:
                return []   
                
    def globals_diff(self, other_shot, group=None):
        return globals_diff(self, other_shot, group)            
    

class Run(Shot):
    def __init__(self, *args, **kwargs):
        msg = """The 'Run' class has been renamed to 'Shot' (but is otherwise 
            compatible). 'Run' will be removed in lyse v4.0. Please update your 
            analysis code to use the 'Shot' class.
            """
        warnings.warn(dedent(msg), DeprecationWarning)
        super().__init__(*args, **kwargs)

    def globals_diff(self, other_run, group=None):
        return globals_diff(self, other_run, group)  


class Sequence(Shot):
    def __init__(self, h5_path, shot_paths, no_write=False):
        # Ensure file exists without affecting its last modification time if it
        # already exists.
        try:
            with h5py.File(h5_path, 'r') as f:
                pass
        except OSError:
            if no_write:
                msg = "Cannot create the hdf5 file; this instance is read-only."
                raise PermissionError(msg)
            else:
                with h5py.File(h5_path, 'a') as f:
                    pass

        super().__init__(h5_path, no_write=no_write)

        if isinstance(shot_paths, pandas.DataFrame):
            shot_paths = shot_paths['filepath']      
        self.__shots = {path: Shot(path,no_write=True) for path in shot_paths}
        
    @property
    def runs(self):
        """This property is deprecated and will be removed in lyse v4.0
        
        Use the :attr:`shots` attribute instead.
        """
        msg = """The 'runs' attribute has been renamed to 'shots'. 'runs' will be
            removed in lyse v4.0. Please update your analysis code to use the 'shots'
            attribute.
            """
        warnings.warn(dedent(msg), DeprecationWarning)
        return copy.copy(self.__shots)

    @property
    def shots(self):
        """A dictionary containing :class:`Shot` instances.
        
        The dictionary is keyed by filepaths specified in the :code:`shot_paths`
        argument at instantiation time."""
        return copy.copy(self.__shots)

    def get_trace(self,*args):
        return {path:shot.get_trace(*args) for path,shot in self.shots.items()}
        
    def get_result_array(self,*args):
        return {path:shot.get_result_array(*args) for path,shot in self.shots.items()}
         
    def get_traces(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
             
    def get_result_arrays(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
     
    def get_image(self,*args):
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')     


def figure_to_clipboard(figure=None, **kwargs):
    """Copy a matplotlib figure to the clipboard as a png. If figure is None,
    the current figure will be copied. Copying the figure is implemented by
    calling figure.savefig() and then copying the image data from the
    resulting file. Any keyword arguments will be passed to the call to
    savefig(). If bbox_inches keyword arg is not provided,
    bbox_inches='tight' will be used"""
    
    import matplotlib.pyplot as plt
    from zprocess import start_daemon
    import tempfile

    if not 'bbox_inches' in kwargs:
        kwargs['bbox_inches'] = 'tight'
               
    if figure is None:
        figure = plt.gcf()

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        tempfile_name = f.name

    figure.savefig(tempfile_name, **kwargs)

    tempfile2clipboard = os.path.join(LYSE_DIR, 'tempfile2clipboard.py')
    start_daemon([sys.executable, tempfile2clipboard, '--delete', tempfile_name])


def register_plot_class(identifier, cls):
    if not spinning_top:
        msg = """Warning: lyse.register_plot_class has no effect on scripts not run with
            the lyse GUI.
            """
        sys.stderr.write(dedent(msg))
    _plot_classes[identifier] = cls

def get_plot_class(identifier):
    return _plot_classes.get(identifier, None)

def delay_results_return():
    global _delay_flag
    if not spinning_top:
        msg = """Warning: lyse.delay_results_return has no effect on scripts not run 
            with the lyse GUI.
            """
        sys.stderr.write(dedent(msg))
    _delay_flag = True
