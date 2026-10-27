#####################################################################
#                                                                   #
# /communication.py                                                 #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Code required for interprocess communication
"""

# Labscript imports
from labscript_utils.ls_zprocess import ZMQServer
import labscript_utils.shared_drive as shared_drive

# qt imports
from qtutils import inmain_decorator

# Lyse imports
from lyse.dataframe_utilities import (rangeindex_to_multiindex, resolve_column_names,
                                      last_sequences)

# The columns that rangeindex_to_multiindex() uses as the row index, with and without
# integer indexing:
INDEX_COLUMNS = ['sequence', 'run time', 'sequence_index', 'run number', 'run repeat']

class WebServer(ZMQServer):

    def __init__(self, app, *args, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def handler(self, request_data):
        self.app.logger.info('WebServer request: %s', request_data)
        if request_data == 'hello':
            return 'hello'
        elif (isinstance(request_data, tuple) and request_data[0] == 'get dataframe'
              and len(request_data) in (3, 4)):
            # Clients send the columns argument only if it is not None, so that a
            # request without it is understood by older versions of lyse:
            n_sequences, filter_kwargs = request_data[1:3]
            columns = request_data[3] if len(request_data) == 4 else None
            try:
                df = self._retrieve_dataframe(n_sequences, columns)
            except KeyError as e:
                # A requested column is not in the dataframe. Raise the error in the
                # client:
                return e
            df = rangeindex_to_multiindex(df, inplace=True)
            if filter_kwargs is not None:
                df = df.filter(**filter_kwargs)
            return df
        elif request_data == 'get dataframe':
            # Ensure backwards compatability with clients using outdated
            # versions of lyse.
            return self._retrieve_dataframe()
        elif isinstance(request_data, dict):
            if 'filepath' in request_data:
                h5_filepath = shared_drive.path_to_local(request_data['filepath'])
                if isinstance(h5_filepath, bytes):
                    h5_filepath = h5_filepath.decode('utf8')
                if not isinstance(h5_filepath, str):
                    raise AssertionError(str(type(h5_filepath)) + ' is not str or bytes')
                self.app.filebox.incoming_queue.put(h5_filepath)
                return 'added successfully'
        elif isinstance(request_data, str):
            # Just assume it's a filepath:
            self.app.filebox.incoming_queue.put(shared_drive.path_to_local(request_data))
            return "Experiment added successfully\n"

        return ("error: operation not supported. Recognised requests are:\n "
                "'get dataframe'\n 'hello'\n {'filepath': <some_h5_filepath>}")

    @inmain_decorator(wait_for_return=True)
    def _copy_dataframe(self, n_sequences=None, columns=None):
        """Return a copy of the rows of the dataframe from the n_sequences most recent
        sequences, or all rows if n_sequences is None, and of the columns named by
        columns and the columns used as the row index, or all columns if columns is
        None. See resolve_column_names() for how columns are named."""
        df = self.app.filebox.shots_model.dataframe
        if n_sequences is not None:
            rows = last_sequences(df, n_sequences)
        if columns is not None and not df.empty:
            padding = ('',) * (df.columns.nlevels - 1)
            index_labels = [(name,) + padding for name in INDEX_COLUMNS
                            if (name,) + padding in df.columns]
            labels = resolve_column_names(df.columns, columns) + index_labels
            df = df[list(dict.fromkeys(labels))].sort_index(axis=1)
        if n_sequences is not None:
            df = df[rows]
        return df.copy(deep=True)

    def _retrieve_dataframe(self, n_sequences=None, columns=None):
        # infer_objects() picks fixed datatypes for columns that are compatible with
        # fixed datatypes, dramatically speeding up pickling. It is called here
        # rather than when updating the dataframe as calling it during updating may
        # call it needlessly often, whereas it only needs to be called prior to
        # sending the dataframe to a client requesting it, as we're doing now.
        return self._copy_dataframe(n_sequences, columns).infer_objects()
