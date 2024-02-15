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
"""Lyse analysis API
"""

from lyse.dataframe_utilities import get_series_from_shot as _get_singleshot
from labscript_utils.dict_diff import dict_diff
import os
import pickle as pickle
from pathlib import Path
import sys
import threading
import functools
import contextlib

import labscript_utils.h5_lock, h5py
from labscript_utils.labconfig import LabConfig
import pandas
from numpy import array, ndarray, where
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
    
    This function allows for either extracting information from a run's hdf5
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
        filepath (str, optional): The path to a run's hdf5 file. If a value
            other than `None` is provided, then this function will return a
            pandas series containing data associated with the run. In particular
            it will contain the globals, singleshot results, multishot results,
            etc. that would appear in the run's row in the Lyse dataframe, but
            the values will be read from the file rather than extracted from the
            lyse dataframe. If `filepath` is `None`, then this function will
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

def globals_diff(run1, run2, group=None):
    """Take a diff of the globals between two runs.

    Uses the :obj:`labscript-utils:dict_diff` function.

    Args:
        run1 (:obj:`Run`): First Run to compare.
        run2 (:obj:`Run`): Second Run to compare.
        group (str, optional): When `None` (default), compare all groups.
            Otherwise, only compare globals in `group`.

    Returns:
        dict: Dictionary of differences between globals in the form `key:[val1,val2]`
        pairs. Keys unique to either dictionary are returned as `key:[val1,'-']` or
        `key:['-',val2]`.
    """
    return dict_diff(run1.get_globals(group), run2.get_globals(group))
 
def open_file(mode):
    """Decorator for lyse functions to allow using previously opened file with context manager.
    
    If multiple read/write operations happen on a Run in a single shot,
    opening the h5file once via the context manager `open`
    can speed up the analysis execution time by limiting the number of times
    the file must be opened/closed.

    Note that an opened :class:`h5py:h5py.File` can only be in modes `'r'` or `'r+'`.
    All other mode opening options differ in file creation logic only.
    In particular, all mode opening options except `'r'` are considered
    `'r+'` once the file is open.

    Args:
        mode (str): which :class:`h5py:h5py.File` mode to open the h5 file with.
            Must be 'r', 'a', 'r+', 'w', 'w-', or 'x'.
            Lyse typically only uses 'r' and 'r+'.

    Returns:
        Decorator for :class:`~.Run` methods that need to read/write the shot file

    Raises:
        PermissionError: If the Run is set as read-only but a write mode is requested
    """
    def decorator_open_file(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):

            if self.no_write and mode != 'r':
                msg = f'Cannot perform operation {func.__name__:s}; this run is read-only'
                raise PermissionError(msg)

            if self.h5_file is None:
                with self.open(mode):
                    return func(self, *args, **kwargs)
            else:
                if (self.h5_file.mode == 'r') and (mode != 'r'):
                    msg = (f"Cannot perform operation {func.__name__:s}; "
                        + f"requested mode {mode:s} not compatible with "
                        + f"h5_file's mode {self.h5_file.mode:s}")
                    raise PermissionError(msg)
                else:
                    return func(self, *args, **kwargs)
            
        return wrapper
    return decorator_open_file

class Run(object):
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
        self.__h5_file = None
        self.__group = None
        if not self.no_write:
            self._create_group_if_not_exists(h5_path, '/', 'results')
                     
        # The group where this run's results will be stored in the h5 file will be the
        # name of the python script which is instantiating this Run object. If the user
        # is running interactively or in an unusual environment such that the __main__
        # module isn't in sys.modules or doesn't have a non-None __file__ attribute,
        # then self.group will not be set.
        main_module_path = getattr(sys.modules.get('__main__'), '__file__', None)
        if not self.no_write and main_module_path is not None:
            self.set_group(Path(main_module_path).stem)

    @property
    def h5_path(self):
        """str: The value provided for `h5_path` during instantiation."""
        return self.__h5_path

    @property
    def no_write(self):
        """bool: The value provided for `no_write` during instantiation."""
        return self.__no_write
    
    @property
    def h5_file(self):
        """h5py.File: opened h5py file handle for the shot"""
        return self.__h5_file
    
    @contextlib.contextmanager
    def open(self, mode):
        """Context manager to open the Run's h5 file for successive reads/writes.

        Supports all h5 modes, though only `'r'` and `'r+'`/`'a'` are typically
        used within lyse itself.

        Args:
            mode (str): which :class:`h5py:h5py.File` mode to open the h5 file with.
                Must be 'r', 'a', 'r+', 'w', 'w-', or 'x'.
                Lyse typically only uses 'r' and 'r+'.

        Yields:
            :class:`~.Run`: Handle to opened `Run` object.

        Raises:
            PermissionError: If the Run is set as read-only but a write mode is requested

        Examples:
            
            Trivial example that selectively opens the file during analysis.
            For better performance, it would be better to combine
            these two openings into one.

            >>> from lyse import *
            >>> shot = Run(path)
            >>> with shot.open('r'):
            >>>     # shot processing that requires reads/writes
            >>>     _, vals = shot.get_trace('my_trace')
            >>> with shot.open('r+'):
            >>>     results = vals**2
            >>>     shot.save_result_array('my_result', results)
            >>>     _, vals2 = shot.get_trace('my_other_trace')
            >>>     shot.save_result('my_result2', vals2.mean())
            >>> # Shot processing that doesn't require h5 reads/writes
            >>> print(f'Max of results is {results.max():.3f}')
            Max of results is 5.003

            Open and create the shot handle for the whole analysis
            in a single line.

            >>> from lyse import *
            >>> with Run(path).open('r+') as shot:
            >>>     # shot processing that requires reads/writes
            >>>     t, vals = shot.get_trace('my_trace')
            >>>     results = vals**2
            >>>     shot.save_result_array('my_result', results)
            >>>     # Shot processing that doesn't require h5 reads/writes
            >>>     # could be outside the context
            >>>     print(f'Max of results is {results.max():.3f}')
            Max of results is 5.003

        """

        # ensure no_write is respected
        if self.no_write and mode != 'r':
            msg = 'Cannot open file with a write mode; this run is read-only'
            raise PermissionError(msg)
            
        # check if file is already open, do nothing if modes compatible
        if self.__h5_file is not None:
            # ensure no-write is respected
            if (self.__h5_file.mode == 'r') and (mode != 'r'):
                msg = 'Cannot open file with a write mode; this run is read-only'
                raise PermissionError(msg)
            yield self
            return
        
        with h5py.File(self.h5_path, mode) as f:
            self.__h5_file = f
            try:
                yield self
            finally:
                self.__h5_file = None

    @property
    def group(self):
        """str: The group in the hdf5 file in which results are saved by default.
        
        When a `Run` instance is created from within a lyse singleshot or
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
            if groupname not in h5_file[location]:
                create_group = True
        if create_group:
            if self.no_write:
                msg = "Cannot create group; this run is read-only."
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

    @open_file('r')
    def trace_names(self):
        """Return a list of all saved data traces in Run.

        Raises:
            KeyError: If the group `'/data/traces/'` does not yet exist.

        Returns:
            list: List of keys in the h5 file's `'/data/traces/'` group.
        """
        try:
            return list(self.h5_file['data']['traces'].keys())
        except KeyError:
            return []

    @open_file('r')                
    def get_attrs(self, group):
        """Returns all attributes of the specified group as a dictionary.

        Args:
            group (str): Group for which attributes are desired.

        Raises:
            Exception: If the `group` does not exist.

        Returns:
            dict: Dictionary of attributes.
        """
        if group not in self.h5_file:
            raise Exception('The group \'%s\' does not exist'%group)
        return get_attributes(self.h5_file[group])

    @open_file('r')
    def get_trace(self, name, raw_data=False):
        """Return the saved data trace `name`.
        
        Args:
            name (str): Name of saved data trace to get.
            raw_data (bool, optional): option to return the h5_data directly 
                without interpreting it as a 2-D time trace.

        Raises:
            Exception: If `name` trace does not exist.

        Returns:
            :obj:`numpy:numpy.ndarray`: Returns 2-D timetrace of times `'t'`
            and values `'values'`.
        """
        if name not in self.h5_file['data']['traces']:
            raise Exception('The trace \'%s\' does not exist'%name)
        trace = self.h5_file['data']['traces'][name]
        
        if raw_data:
            data = trace[()]
        else:
            data = array(trace['t'],dtype=float),array(trace['values'],dtype=float)  
        
        return data

    @open_file('r')
    def get_wait(self, name):
        """Returns the wait parameters: label, timeout, duration, and time out status.

        Args:
            name (str): Name of the wait to get.

        Raises:
            KeyError if `name` wait does not exist.

        Returns:
            tuple: Tuple of the wait parameters.
        """
        if not 'data' in self.h5_file:
            raise Exception('The shot has no data group')
        name=name.encode()
        if name not in self.h5_file['data']['waits']['label']:
            raise Exception('The wait \'%s\' does not exist'%name.decode())
        name_index, =where(self.h5_file['data']['waits']['label']==name)[0]
        return self.h5_file['data']['waits'][name_index]

    @open_file('r')
    def get_waits(self):
        """Returns the parameters of all waits in the experiment.

        Raises:
            Exception: If the experiment has no waits.

        Returns:
            :obj:`numpy:numpy.ndarray`: Returns 2D structured numpy array of the waits and their parameters.
        """
        if 'data' not in self.h5_file:
            raise Exception('The shot has no data group')
        if 'waits' not in self.h5_file['data']:
            raise Exception('The shot has no waits')
        return self.h5_file['data']['waits'][()]

    @open_file('r')        
    def get_result_array(self, group, name):
        """Returns saved results data.

        Args:
            group (str): Group to look in for the array. Typically the name of
                the analysis script that created it.
            name (str): Name of the results array to return.

        Raises:
            Exception: If `group` or `name` do not already exist.

        Returns:
            :obj:`numpy:numpy.ndarray`: Numpy array of the saved data.
        """
        if group not in self.h5_file['results']:
            raise Exception('The result group \'%s\' does not exist'%group)
        if name not in self.h5_file['results'][group]:
            raise Exception('The result array \'%s\' does not exist'%name)
        return array(self.h5_file['results'][group][name])

    @open_file('r')            
    def get_result(self, group, name):
        """Retrieve result from prior calculation.

        Args:
            group (str): Group to look in for the result. Typically the name of
                the analysis script that created it.
            name (str): Name of the result.

        Raises:
            Exception: If `group` or `name` do not already exist.

        Returns:
            : Result with appropriate type, as determined by 
            :obj:`labscript-utils:labscript_utils.properties.get_attribute`.
        """
        if group not in self.h5_file['results']:
            raise Exception('The result group \'%s\' does not exist'%group)
        if name not in self.h5_file['results'][group].attrs.keys():
            raise Exception('The result \'%s\' does not exist'%name)
        return get_attribute(self.h5_file['results'][group], name)

    @open_file('r')            
    def get_results(self, group, *names):
        """Return multiple results from the same group.

        Iteratively calls get_result(group,name) for each name provided.
        
        Args:
            group (str): Group to look in for the results. Typically the name of
                            the analysis script that created it.
            *names (str): Names of results to retrieve.

        Returns:
            list: List of the results, in the same order as specified by names.
            If `names` does not preserve order, return order is not guaranteed.
        """
        results = []
        for name in names:
            results.append(self.get_result(group,name))
        return results

    @open_file('r+')            
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
        if not group:
            if self.group is None:
                msg = """Cannot save result; no default group set. Either
                    specify a value for this method's optional group
                    argument, or set a default value using the set_group()
                    method."""
                raise ValueError(dedent(msg))
            # Save to analysis results group by default
            group = 'results/' + self.group
        elif group not in self.h5_file:
            # Create the group if it doesn't exist
            self.h5_file.create_group(group) 
        if name in self.h5_file[group].attrs and not overwrite:
            msg = """Cannot save result; group '{group}' already has
                attribute '{name}' and overwrite is set to False. Set
                overwrite=True to overwrite the existing value.""".format(
                    group=group,
                    name=name,
                )
            raise PermissionError(dedent(msg))
        set_attributes(self.h5_file[group], {name: value})
            
        if spinning_top:
            if self.h5_path not in _updated_data:
                _updated_data[self.h5_path] = {}
            if group.startswith('results'):
                toplevel = group.replace('results/', '', 1)
                _updated_data[self.h5_path][toplevel, name] = value

    @open_file('r+')
    def save_result_array(self, name, data, group=None, 
                          overwrite=True, keep_attrs=False, **kwargs):
        """Save an array of data to the hdf5 h5 file.

        With the default argument values this method saves to `self.group` in
        the `'/results'` group and overwrites any existing value without keeping
        the dataset's previous attributes. Additional keyword arguments are
        passed directly to :obj:`h5py:h5py.Group.create_dataset`.

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
            **kwargs: Optional kwargs passed directly to h5_file.create_dataset()

        Raises:
            PermissionError: A `PermissionError` is raised if `self.no_write` is
                `True` because saving the result would edit the file.
            ValueError: A `ValueError` is raised if `self.group` is `None` and
                no value is provided for `group` because the method then doesn't
                know where to save the result.
            PermissionError: A `PermissionError` is raised if a dataset with
                name `name` already exists but `overwrite` is set to `False`.
        """
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
        elif group not in self.h5_file:
            # Create the group if it doesn't exist
            self.h5_file.create_group(group) 
        if name in self.h5_file[group]:
            if overwrite:
                # Overwrite if dataset already exists
                if keep_attrs:
                    attrs = dict(self.h5_file[group][name].attrs)
                del self.h5_file[group][name]
            else:
                msg = """Cannot save result; group '{group}' already has
                    dataset '{name}' and overwrite is set to False. Set
                    overwrite=True to overwrite the existing
                    value.""".format(
                        group=group,
                        name=name,
                    )
                raise PermissionError(dedent(msg))
        self.h5_file[group].create_dataset(name, data=data, **kwargs)
        for key, val in attrs.items():
            self.h5_file[group][name].attrs[key] = val

    @open_file('r')
    def get_traces(self, *names,):
        """Retrieve multiple data traces.

        Iteratively calls :obj:`get_trace`.

        Args:
            *names (str): Names of traces to retrieve

        Returns:
            list: List of numpy arrays.
        """
        traces = []
        for name in names:
            traces.extend(self.get_trace(name))
        return traces

    @open_file('r')             
    def get_result_arrays(self, group, *names):
        """Retrieve multiple result arrays from the same group.

        Iteratively calls :obj:`self.get_result_array(group,name) <get_result_array>`
        with default arguments.

        Args:
            group (str): Group to obtain the results from.
            *names (str): Result names to retrieve.

        Returns:
            list: List of results.
        """
        results = []
        for name in names:
            results.append(self.get_result_array(group, name))
        return results

    @open_file('r+')        
    def save_results(self, *args, **kwargs):
        """Save multiple results to the hdf5 file.

        This method iteratively calls 
        :obj:`self.save_result() <save_result>` on multiple results.
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
            >>> run = Run('path/to/an/hdf5/file.h5')  # doctest: +SKIP
            >>> a = 5
            >>> b = 2.48
            >>> run.save_results('result', a, 'other_result', b, overwrite=False)  # doctest: +SKIP
        """
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names, values):
            self.save_result(name, value, **kwargs)

    @open_file('r+')            
    def save_results_dict(self, results_dict, uncertainties=False, **kwargs):
        """Save results dictionary.

        Iteratively calls :obj:`self.save_result(key,value) <save_result>` on
        the provided dictionary. If uncertainties is `True`, `value` is a two-element
        list where the second element is the uncertainty in the result and saved with
        to the same key with `u_` prepended.

        Args:
            results_dict (dict): Dictionary of results to save. If uncertainties is
                `False`, form is `{name:value}`. If `True`, for is `{name,[value,uncertainty]}`.
            uncertainties (bool, optional): Marks if uncertainties are provided.
            **kwargs: Extra arguments provided to :obj:`save_result`.
        """
        for name, value in results_dict.items():
            if not uncertainties:
                self.save_result(name, value, **kwargs)
            else:
                self.save_result(name, value[0], **kwargs)
                self.save_result('u_' + name, value[1], **kwargs)

    @open_file('r+')
    def save_result_arrays(self, *args, **kwargs):
        """Save multiple result arrays.

        Iteratively calls :obj:`save_result_array() <save_result_array>` on multiple data sets. 
        Assumes arguments are ordered such that each dataset to be saved is 
        preceded by the name to save it as. 
        All keyword arguments are passed to each call of save_result_array().

        Args:
            *args: Ordered arguments such that each dataset to be saved is
                preceded by the name to save it as.
            **kwargs: Passed through to `save_result_array` as kwargs.
        """
        names = args[::2]
        values = args[1::2]
        for name, value in zip(names, values):
            self.save_result_array(name, value, **kwargs)

    @open_file('r')    
    def get_image(self, orientation, label, image):
        """Get previously saved image from the h5 file.

        h5 path to saved image is `/images/orientation/label/image`

        Args:
            orientation (str): Orientation label for saved image.
            label (str): Label of saved image.
            image (str): Identifier of saved image.

        Raises:
            Exception: If the image or paths do not exist.

        Returns:
            :obj:`numpy:numpy.ndarray`: 2-D image array.
        """
        if 'images' not in self.h5_file:
            raise Exception('File does not contain any images')
        if orientation not in self.h5_file['images']:
            raise Exception('File does not contain any images with orientation \'%s\''%orientation)
        if label not in self.h5_file['images'][orientation]:
            raise Exception('File does not contain any images with label \'%s\''%label)
        if image not in self.h5_file['images'][orientation][label]:
            raise Exception('Image \'%s\' not found in file'%image)
        return array(self.h5_file['images'][orientation][label][image])

    @open_file('r')    
    def get_images(self, orientation, label, *images):
        """Get multiple saved images from orientation and label.

        Iteratively calls :obj:`self.get_image(orientation,label,image) <get_image>` for
        each image argument.

        Args:
            orientation (str): Orientation label of saved images.
            label (str): Label of saved images.
            *images (str): Collection of images to return

        Returns:
            :obj:`list` of :obj:`numpy:numpy.ndarray`: List of 2-D images.
        """
        results = []
        for image in images:
            results.append(self.get_image(orientation,label,image))
        return results

    @open_file('r')
    def get_images_dict(self, orientation, label, *images):
        """Get multiple saved images from orientation and label.

        Iteratively calls :obj:`self.get_image(orientation,label,image) <get_image>` for
        each image argument.

        Args:
            orientation (str): Orientation label of saved images.
            label (str): Label of saved images.
            *images (str): Collection of images to return

        Returns:
            :obj:`dict` of :obj:`numpy:numpy.ndarray`: Dictionary of 2-D images.
        """
        results = self.get_images(orientation,label, *images)

        return {k:v for k,v in zip(images, results)}

    @open_file('r')
    def get_all_image_labels(self):
        """Return all existing images labels in the h5 file.

        Returns:
            dict: Dictionary of the form `{orientation:[label1,label2]}`
        """
        images_list = {}
        for orientation in self.h5_file['/images'].keys():
            images_list[orientation] = list(self.h5_file['/images'][orientation].keys())               
        return images_list

    @open_file('r')    
    def get_image_attributes(self, orientation):
        """Return the attributes of a saved orientation image group.

        Args:
            orientation (str): Orientation label to get attributes of.

        Raises:
            Exception: If images or orientation group do not exist.
        Returns:
            dict: Dictionary of attributes and their values.
        """
        if 'images' not in self.h5_file:
            raise Exception('File does not contain any images')
        if orientation not in self.h5_file['images']:
            raise Exception('File does not contain any images with orientation \'%s\''%orientation)
        return get_attributes(self.h5_file['images'][orientation])

    @open_file('r')
    def get_globals(self, group=None):
        """Get globals from the shot.

        Args:
            group (str, optional): If `None`, return all global variables.
                If defined, only return globals from `group`.

        Returns:
            dict: Dictionary of globals and their values.
        """
        if not group:
            return dict(self.h5_file['globals'].attrs)
        else:
            try:
                return dict(self.h5_file['globals'][group].attrs)
            except KeyError:
                return {}

    @open_file('r')
    def get_globals_raw(self, group=None):
        """Get the raw global values from the shot.

        Args:
            group (str, optional): If `None`, return all global variables.
                If defined, only return globals from `group`.

        Returns:
            dict: Dictionary of raw globals and their values
        """
        globals_dict = {}
        if group == None:
            for obj in self.h5_file['globals'].values():
                temp_dict = dict(obj.attrs)
                for key, val in temp_dict.items():
                    globals_dict[key] = val
        else:
            globals_dict = dict(self.h5_file['globals'][group].attrs)
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

    @open_file('r')            
    def get_globals_expansion(self):
        """Get the expansion type of each global.

        This will skip global variables that do not have
        an expansion.

        Args:
            h5_file (h5py.File, optional): Allows passing in a handle
                to an already opened h5 file. If None, will open in
                read only mode and close after operation.

        Returns:
            dict: Dcitionary of globals with their expansion type.
        """
        expansion_dict = {}
        def append_expansion(name, obj):
            if 'expansion' in name:
                temp_dict = dict(obj.attrs)
                for key, val in temp_dict.items():
                    if val:
                        expansion_dict[key] = val
        
        self.h5_file['globals'].visititems(append_expansion)
        return expansion_dict

    @open_file('r')                   
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
            self.h5_file[path].visititems(append_units)
        except KeyError:
            pass
        return units

    @open_file('r')
    def globals_groups(self):
        """Get names of all the globals groups.

        Returns:
            list: List of global group names.
        """
        try:
            return list(self.h5_file['globals'].keys())
        except KeyError:
            return []
                
    def globals_diff(self, other_run, group=None):
        """Take a diff between this run and another run.

        This calls :obj:`globals_diff(self, other_run, group) <globals_diff>`.

        Args:
            other_run (:obj:`Run`): Run to compare to.
            group (str, optional): When `None` (default), compare all globals.
                Otherwise only compare globals in `group`. 

        Returns:
            dict: Dictionary of different globals.
        """
        return globals_diff(self, other_run, group)            
    
        
class Sequence(Run):
    def __init__(self, h5_path, run_paths, no_write=False):
        """Generic results storage that is not associated with a specific Run.

        This is typically used to save results from a multi-shot analysis to
        an independent h5 file.

        Args:
            h5_path (str): Path to h5 file to save to. If file does not exist,
                will try to create it assuming `no_write=False`. If file exists,
                opens a handle to it.
            run_paths (:obj:`list` or :obj:`pandas:pandas.DataFrame`): List of
                runs to associate with the sequence. If a dataframe is supplied,
                will introspect the runs from the `'filepath'` data.
            no_write (bool, optional): If `True`, opens file in read-only mode.

        Raises:
            PermissionError: If trying to create a file in read-only mode.
        """

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

        if isinstance(run_paths, pandas.DataFrame):
            run_paths = run_paths['filepath']      
        self.runs = {path: Run(path,no_write=True) for path in run_paths}

    def get_trace(self,*args):
        """Get the named trace from each run in the sequence.

        Args:
            *args (str): Name of trace. Passed directly to :obj:`get_trace`.

        Return:
            dict: Dictonary of path:trace pairs for each run.
        """
        return {path:run.get_trace(*args) for path,run in self.runs.items()}
        
    def get_result_array(self,*args):
        """Get the specified result array from each run in the sequence.

        Args:
            *args (str): Passed directly to :obj:`get_result_array`. Should be
                `group` and `name` to result to obtain.

        Return:
            dict: Dictionary of path:result pairs for each run.
        """
        return {path:run.get_result_array(*args) for path,run in self.runs.items()}
         
    def get_traces(self,*args):
        """Not implemented!

        Attention:
            Not implemented, but could be.
        """
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
             
    def get_result_arrays(self,*args):
        """Not implemented!

        Attention:
            Not implemented, but could be.
        """
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')
     
    def get_image(self,*args):
        """Not implemented!

        Attention:
            Not implemented, but could be.
        """
        raise NotImplementedError('If you want to use this feature please ask me to implement it! -Chris')     


def figure_to_clipboard(figure=None, **kwargs):
    """Copy a matplotlib figure to the clipboard as a png. 

    If figure is None,
    the current figure will be copied. Copying the figure is implemented by
    calling figure.savefig() and then copying the image data from the
    resulting file. If bbox_inches keyword arg is not provided,
    bbox_inches='tight' will be used.

    Args:
        figure (:obj:`matplotlib:matplotlib.figure`, optional): 
            Figure to copy to the clipboard. If `None`, copies the current figure.
        **kwargs: Passed to `figure.savefig()` as kwargs.
    """
    
    import matplotlib.pyplot as plt
    from zprocess import start_daemon
    import tempfile

    if 'bbox_inches' not in kwargs:
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
