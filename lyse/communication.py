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

# 3rd party imports:
import numpy as np

# Labscript imports
from labscript_utils.ls_zprocess import ZMQServer
import labscript_utils.shared_drive as shared_drive

# qt imports
from qtutils import inmain_decorator

# Lyse imports
from lyse.dataframe_utilities import rangeindex_to_multiindex

class WebServer(ZMQServer):

    def __init__(self, app, *args, **kwargs):
        self.app = app
        super().__init__(*args, **kwargs)

    def handler(self, request_data):
        self.app.logger.info('WebServer request: %s' % str(request_data))
        if request_data == 'hello':
            return 'hello'
        elif isinstance(request_data, tuple) and request_data[0]=='get dataframe' and len(request_data)==3:
            _, n_sequences, filter_kwargs = request_data
            df = self._retrieve_dataframe()
            df = rangeindex_to_multiindex(df, inplace=True)
            # Return only a subset of the dataframe if instructed to do so.
            if n_sequences is not None:
                df = self._extract_n_sequences_from_df(df, n_sequences)
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
    def _copy_dataframe(self):
        df = self.app.filebox.shots_model.dataframe.copy(deep=True)
        return df

    def _retrieve_dataframe(self):
        # infer_objects() picks fixed datatypes for columns that are compatible with
        # fixed datatypes, dramatically speeding up pickling. It is called here
        # rather than when updating the dataframe as calling it during updating may
        # call it needlessly often, whereas it only needs to be called prior to
        # sending the dataframe to a client requesting it, as we're doing now.
        df = self._copy_dataframe()
        df.infer_objects()
        return df

    def _extract_n_sequences_from_df(self, df, n_sequences):
        # If the dataframe is empty, just return it, otherwise accessing columns
        # below will raise a KeyError.
        if df.empty:
            return df

        # Get a list of all unique sequences, each corresponding to one call to
        # engage in runmanager. Each sequence may contain multiple runs. The
        # below creates strings to identify sequences. To be from the same
        # sequence, two shots have to have the same value for 'sequence' (which
        # makes sure that the time when engage was called are the same to within
        # 1 second), 'labscript' (must have been generated from the same
        # labscript), and 'sequence_index' (a counter which keeps track of how
        # many times engage has been called and resets to 0 at the start of each
        # day). Typically just the value for sequence, is enough. However it
        # only records time down to the second, so if engage() is called twice
        # quickly then two different sequences can end up with the same value
        # there.
        sequences = [str(sequence) for sequence in df['sequence']]
        labscripts = [str(labscript) for labscript in df['labscript']]
        sequence_indices = [str(index) for index in df['sequence_index']]
        # Combine into one string.
        criteria = zip(sequences, labscripts, sequence_indices)
        indentity_strings = [seq + script + ind for seq, script, ind in criteria]

        # Find the distinct values, maintaining their ordering.
        unique_identities = np.intersect1d(indentity_strings, indentity_strings)

        # Slice the DataFrame so that only the last n_sequences sequences
        # remain. Note that slicing unique_identities just returns all of its
        # entries if n_sequences is greater than its length; it doesn't raise an
        # error.
        if n_sequences == 0:
            identities_included = []
        else:
            identities_included = unique_identities[-n_sequences:]
        df_subset = df[[id in identities_included for id in indentity_strings]]

        return df_subset