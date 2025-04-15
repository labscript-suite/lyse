#####################################################################
#                                                                   #
# /filebpx.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Lyse GUI dataframe filebox widget
"""

import os
import sys
import logging
import threading
import subprocess
import time
import traceback
import queue

# 3rd party imports:
import numpy as np
import labscript_utils.h5_lock, h5py # use patched h5py
import pandas

# Labscript imports
import zprocess
from labscript_utils.labconfig import LabConfig
from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets


# qt imports
from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils import inmain_decorator, UiLoader, DisconnectContextManager
from qtutils.auto_scroll_to_end import set_auto_scroll_to_end
import qtutils.icons

# Lyse imports
from lyse.dataframe_utilities import concat_with_padding, get_dataframe_from_shot, replace_with_padding
import lyse.utils
import lyse.widgets


class EditColumns(object):
    ROLE_SORT_DATA = QtCore.Qt.UserRole + 1
    COL_VISIBLE = 0
    COL_NAME = 1

    def __init__(self, filebox, column_names, columns_visible):
        self.filebox = filebox
        self.column_names = column_names.copy()
        self.columns_visible = columns_visible.copy()
        self.old_columns_visible = columns_visible.copy()

        loader = UiLoader()
        self.ui = loader.load(os.path.join(lyse.utils.LYSE_DIR, 'user_interface/edit_columns.ui'), lyse.widgets.EditColumnsDialog())

        self.model = lyse.widgets.UneditableModel()
        self.header = HorizontalHeaderViewWithWidgets(self.model)
        self.select_all_checkbox = QtWidgets.QCheckBox()
        self.select_all_checkbox.setTristate(False)
        self.ui.treeView.setHeader(self.header)
        self.proxy_model = QtCore.QSortFilterProxyModel()
        self.proxy_model.setSourceModel(self.model)
        self.proxy_model.setFilterCaseSensitivity(QtCore.Qt.CaseInsensitive)
        self.proxy_model.setFilterKeyColumn(self.COL_NAME)
        self.ui.treeView.setSortingEnabled(True)
        self.header.setStretchLastSection(True)
        self.proxy_model.setSortRole(self.ROLE_SORT_DATA)
        self.ui.treeView.setModel(self.proxy_model)
        self.ui.setWindowModality(QtCore.Qt.ApplicationModal)

        self.ui.treeView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        # Make the actions for the context menu:
        self.action_set_selected_visible = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'Show selected columns',  self.ui)
        self.action_set_selected_hidden = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'Hide selected columns',  self.ui)

        self.connect_signals()
        self.populate_model(column_names, self.columns_visible)

    def connect_signals(self):
        self.ui.close_signal.connect(self.close)
        self.ui.lineEdit_filter.textEdited.connect(self.on_filter_text_edited)
        self.ui.pushButton_make_it_so.clicked.connect(self.make_it_so)
        self.ui.pushButton_cancel.clicked.connect(self.cancel)
        self.model.itemChanged.connect(self.on_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.model_item_changed_disconnected = DisconnectContextManager(
            self.model.itemChanged, self.on_model_item_changed)
        self.select_all_checkbox.stateChanged.connect(self.on_select_all_state_changed)
        self.select_all_checkbox_state_changed_disconnected = DisconnectContextManager(
            self.select_all_checkbox.stateChanged, self.on_select_all_state_changed)
        self.ui.treeView.customContextMenuRequested.connect(self.on_treeView_context_menu_requested)
        self.action_set_selected_visible.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Checked))
        self.action_set_selected_hidden.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Unchecked))

    def populate_model(self, column_names, columns_visible):
        self.model.clear()
        self.model.setHorizontalHeaderLabels(['', 'Name'])
        self.header.setWidget(self.COL_VISIBLE, self.select_all_checkbox)
        self.ui.treeView.resizeColumnToContents(self.COL_VISIBLE)
        # Which indices in self.columns_visible the row numbers correspond to
        self.column_indices = {}

        # Remove our special columns from the dict of column names by keeping only tuples:
        column_names = {i: name for i, name in column_names.items() if isinstance(name, tuple)}

        # Sort the column names as comma separated values, converting to lower case:
        sortkey = lambda item: ', '.join(item[1]).lower().strip(', ')

        for column_index, name in sorted(column_names.items(), key=sortkey):
            visible = columns_visible[column_index]
            visible_item = QtGui.QStandardItem()
            visible_item.setCheckable(True)
            if visible:
                visible_item.setCheckState(QtCore.Qt.Checked)
                visible_item.setData(QtCore.Qt.Checked, self.ROLE_SORT_DATA)
            else:
                visible_item.setCheckState(QtCore.Qt.Unchecked)
                visible_item.setData(QtCore.Qt.Unchecked, self.ROLE_SORT_DATA)
            name_as_string = ', '.join(name).strip(', ')
            name_item = QtGui.QStandardItem(name_as_string)
            name_item.setData(sortkey((column_index, name)), self.ROLE_SORT_DATA)
            self.model.appendRow([visible_item, name_item])
            self.column_indices[self.model.rowCount() - 1] = column_index

        self.ui.treeView.resizeColumnToContents(self.COL_NAME)
        self.update_select_all_checkstate()
        self.ui.treeView.sortByColumn(self.COL_NAME, QtCore.Qt.AscendingOrder)

    def on_treeView_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui)
        menu.addAction(self.action_set_selected_visible)
        menu.addAction(self.action_set_selected_hidden)
        menu.exec_(QtGui.QCursor.pos())

    def on_set_selected_triggered(self, visible):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(self.proxy_model.mapToSource(index).row() for index in selected_indexes)
        for row in selected_rows:
            visible_item = self.model.item(row, self.COL_VISIBLE)
            self.update_visible_state(visible_item, visible)
        self.update_select_all_checkstate()
        self.do_sort()
        self.filebox.set_columns_visible(self.columns_visible)

    def on_filter_text_edited(self, text):
        self.proxy_model.setFilterWildcard(text)

    def on_select_all_state_changed(self, state):
        with self.select_all_checkbox_state_changed_disconnected:
            # Do not allow a switch *to* a partially checked state:
            self.select_all_checkbox.setTristate(False)
        state = self.select_all_checkbox.checkState()
        for row in range(self.model.rowCount()):
            visible_item = self.model.item(row, self.COL_VISIBLE)
            self.update_visible_state(visible_item, state)
        self.do_sort()
        
        self.filebox.set_columns_visible(self.columns_visible)

    def update_visible_state(self, item, state):
        assert item.column() == self.COL_VISIBLE, "unexpected column"
        row = item.row()
        with self.model_item_changed_disconnected:
            item.setCheckState(state)
            item.setData(state, self.ROLE_SORT_DATA)
            if state == QtCore.Qt.Checked:
                self.columns_visible[self.column_indices[row]] = True
            else:
                self.columns_visible[self.column_indices[row]] = False

    def update_select_all_checkstate(self):
        with self.select_all_checkbox_state_changed_disconnected:
            all_states = []
            for row in range(self.model.rowCount()):
                visible_item = self.model.item(row, self.COL_VISIBLE)
                all_states.append(visible_item.checkState())
            if all(state == QtCore.Qt.Checked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Checked)
            elif all(state == QtCore.Qt.Unchecked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Unchecked)
            else:
                self.select_all_checkbox.setCheckState(QtCore.Qt.PartiallyChecked)

    def on_model_item_changed(self, item):
        state = item.checkState()
        self.update_visible_state(item, state)
        self.update_select_all_checkstate()
        self.do_sort()
        self.filebox.set_columns_visible(self.columns_visible)

    def do_sort(self):
        header = self.ui.treeView.header()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.ui.treeView.sortByColumn(sort_column, sort_order)

    def update_columns(self, column_names, columns_visible):

        # Index/name mapping may have changed. Get a mapping by *name* of
        # which columns were previously visible, so we can update our by-index
        # mapping in a moment:
        old_columns_visible_by_name = {}
        for old_column_number, visible in self.old_columns_visible.items():
            column_name = self.column_names[old_column_number]
            old_columns_visible_by_name[column_name] = visible

        self.columns_visible = columns_visible.copy()
        self.column_names = column_names.copy()

        # Update the by-index mapping of which columns were visible before editing:
        self.old_columns_visible = {}
        for index, name in self.column_names.items():
            try:
                self.old_columns_visible[index] = old_columns_visible_by_name[name]
            except KeyError:
                # A new column. If editing is cancelled, any new columns
                # should be set to visible:
                self.old_columns_visible[index] = True
        self.populate_model(column_names, self.columns_visible)

    def show(self):
        self.old_columns_visible = self.columns_visible.copy()
        self.ui.show()

    def close(self):
        self.columns_visible = self.old_columns_visible.copy()
        self.filebox.set_columns_visible(self.columns_visible)
        self.populate_model(self.column_names, self.columns_visible)
        self.ui.hide()

    def cancel(self):
        self.ui.close()

    def make_it_so(self):
        self.ui.hide()

        
class DataFrameModel(QtCore.QObject):

    COL_STATUS = 0
    COL_FILEPATH = 1

    ROLE_STATUS_PERCENT = QtCore.Qt.UserRole + 1
    ROLE_DELETED_OFF_DISK = QtCore.Qt.UserRole + 2
    
    columns_changed = Signal()

    def __init__(self, app, view, exp_config):
        QtCore.QObject.__init__(self)
        self.app = app
        self._view = view
        self.exp_config = exp_config
        self._model = lyse.widgets.UneditableModel()
        self.row_number_by_filepath = {}
        self._previous_n_digits = 0

        self._header = HorizontalHeaderViewWithWidgets(self._model)
        self._vertheader = QtWidgets.QHeaderView(QtCore.Qt.Vertical)
        self._vertheader.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)

        # Smaller font for headers:
        font = self._vertheader.font()
        font.setPointSize(10 if sys.platform == 'darwin' else 8)
        self._header.setFont(font)
        font.setFamily('Ubuntu Mono')
        self._vertheader.setFont(font)

        self._vertheader.setHighlightSections(True)
        self._vertheader.setSectionsClickable(True)
        self._view.setModel(self._model)
        self._view.setHorizontalHeader(self._header)
        self._view.setVerticalHeader(self._vertheader)
        self._delegate = lyse.widgets.ItemDelegate(self.app, self._view, self._model, self.COL_STATUS, self.ROLE_STATUS_PERCENT)
        self._view.setItemDelegate(self._delegate)
        self._view.setSelectionBehavior(QtWidgets.QTableView.SelectRows)
        self._view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # Check if integer indexing is to be used
        try:
            self.integer_indexing = self.exp_config.getboolean('lyse', 'integer_indexing')
        except (LabConfig.NoOptionError, LabConfig.NoSectionError):
            self.integer_indexing = False

        # This dataframe will contain all the scalar data
        # from the shot files that are currently open:
        index = pandas.MultiIndex.from_tuples([('filepath', '')])
        self.dataframe = pandas.DataFrame({'filepath': []}, columns=index)
        # How many levels the dataframe's multiindex has:
        self.nlevels = self.dataframe.columns.nlevels

        status_item = QtGui.QStandardItem()
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/information'))
        status_item.setToolTip('status/progress of single-shot analysis')
        self._model.setHorizontalHeaderItem(self.COL_STATUS, status_item)

        filepath_item = QtGui.QStandardItem('filepath')
        filepath_item.setToolTip('filepath')
        self._model.setHorizontalHeaderItem(self.COL_FILEPATH, filepath_item)

        self._view.setColumnWidth(self.COL_STATUS, 70)
        self._view.setColumnWidth(self.COL_FILEPATH, 100)

        # Column indices to names and vice versa for fast lookup:
        self.column_indices = {'__status': self.COL_STATUS, ('filepath', ''): self.COL_FILEPATH}
        self.column_names = {self.COL_STATUS: '__status', self.COL_FILEPATH: ('filepath', '')}
        self.columns_visible = {self.COL_STATUS: True, self.COL_FILEPATH: True}

        # Whether or not a deleted column was visible at the time it was deleted (by name):
        self.deleted_columns_visible = {}
        
        # Make the actions for the context menu:
        self.action_remove_selected = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/minus'), 'Remove selected shots',  self._view)

        self.connect_signals()

    def connect_signals(self):
        self._view.customContextMenuRequested.connect(self.on_view_context_menu_requested)
        self.action_remove_selected.triggered.connect(self.on_remove_selection)

    def on_remove_selection(self):
        self.remove_selection()

    def remove_selection(self, confirm=True):
        selection_model = self._view.selectionModel()
        selected_indexes = selection_model.selectedRows()
        selected_name_items = [self._model.itemFromIndex(index) for index in selected_indexes]
        if not selected_name_items:
            return
        if confirm and not lyse.utils.question_dialog(self.app, "Remove %d shots?" % len(selected_name_items)):
            return
        # Remove from DataFrame first:
        self.dataframe = self.dataframe.drop(index.row() for index in selected_indexes)
        self.dataframe.index = pandas.Index(range(len(self.dataframe)))
        # Delete one at a time from Qt model:
        for name_item in selected_name_items:
            row = name_item.row()
            self._model.removeRow(row)
        self.renumber_rows()

    def mark_selection_not_done(self):
        selected_indexes = self._view.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        for row in selected_rows:
            status_item = self._model.item(row, self.COL_STATUS)
            if status_item.data(self.ROLE_DELETED_OFF_DISK):
                # If the shot was previously not readable on disk, check to
                # see if it's readable now. It may have been undeleted or
                # perhaps it being unreadable before was due to a network
                # glitch or similar.
                filepath = self._model.item(row, self.COL_FILEPATH).text()
                if not os.path.exists(filepath):
                    continue
                # Shot file is accesible again:
                status_item.setData(False, self.ROLE_DELETED_OFF_DISK)
                status_item.setIcon(QtGui.QIcon(':qtutils/fugue/tick'))
                status_item.setToolTip(None)

            status_item.setData(0, self.ROLE_STATUS_PERCENT)
        
    def on_view_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self._view)
        menu.addAction(self.action_remove_selected)
        menu.exec_(QtGui.QCursor.pos())

    def on_double_click(self, index):
        filepath_item = self._model.item(index.row(), self.COL_FILEPATH)
        shot_filepath = filepath_item.text()
        
        # get path to text editor
        viewer_path = self.exp_config.get('programs', 'hdf5_viewer')
        viewer_args = self.exp_config.get('programs', 'hdf5_viewer_arguments')
        # Get the current labscript file:
        if not viewer_path:
            lyse.utils.error_dialog(self.app, "No hdf5 viewer specified in the labconfig.")
        if '{file}' in viewer_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            viewer_args = [arg if arg != '{file}' else shot_filepath for arg in viewer_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            viewer_args = [shot_filepath] + viewer_args.split()
        try:
            subprocess.Popen([viewer_path] + viewer_args)
        except Exception as e:
            lyse.utils.error_dialog(self.app, "Unable to launch hdf5 viewer specified in %s. Error was: %s" %
                         (self.exp_config.config_path, str(e)))
        
    def set_columns_visible(self, columns_visible):
        self.columns_visible = columns_visible
        for column_index, visible in columns_visible.items():
            self._view.setColumnHidden(column_index, not visible)

    def update_column_levels(self):
        """Pads the keys and values of our lists of column names so that
        they still match those in the dataframe after the number of
        levels in its multiindex has increased (the number of levels never
        decreases, given the current implementation of concat_with_padding())"""
        extra_levels = self.dataframe.columns.nlevels - self.nlevels
        if extra_levels > 0:
            self.nlevels = self.dataframe.columns.nlevels
            column_indices = {}
            column_names = {}
            for column_name in self.column_indices:
                if not isinstance(column_name, tuple):
                    # It's one of our special columns
                    new_column_name = column_name
                else:
                    new_column_name = column_name + ('',) * extra_levels
                column_index = self.column_indices[column_name]
                column_indices[new_column_name] = column_index
                column_names[column_index] = new_column_name
            self.column_indices = column_indices
            self.column_names = column_names

    @inmain_decorator()
    def mark_as_deleted_off_disk(self, filepath):
        # Confirm the shot hasn't been removed from lyse (we are in the main
        # thread so there is no race condition in checking first)
        if not filepath in self.dataframe['filepath'].values:
            # Shot has been removed from FileBox, nothing to do here:
            return

        row_number = self.row_number_by_filepath[filepath]
        status_item = self._model.item(row_number, self.COL_STATUS)
        already_marked_as_deleted = status_item.data(self.ROLE_DELETED_OFF_DISK)
        if already_marked_as_deleted:
            return
        # Icon only displays if percent completion is 100. This is also
        # important so that the shot is not picked up as analysis
        # incomplete and analysis re-attempted on it.
        status_item.setData(True, self.ROLE_DELETED_OFF_DISK)
        status_item.setData(100, self.ROLE_STATUS_PERCENT)
        status_item.setToolTip("Shot has been deleted off disk or is unreadable")
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/drive--minus'))
        self.app.output_box.output('Warning: Shot deleted from disk or no longer readable %s\n' % filepath, red=True)

    @inmain_decorator()
    def infer_objects(self):
        """Convert columns in the dataframe with dtype 'object' into compatible, more
        specific types, if possible. This improves pickling performance and ensures
        multishot analysis code does not encounter columns with dtype 'object' for
        non-mixed numerical data, which it might choke on.
        """
        self.dataframe = self.dataframe.infer_objects()

    @inmain_decorator()
    def update_row(self, filepath, dataframe_already_updated=False, new_row_data=None, updated_row_data=None):
        """"Updates a row in the dataframe and Qt model to the data in the HDF5 file for
        that shot."""
        # To speed things up block signals to the model during update
        self._model.blockSignals(True)

        # Update the row in the dataframe first:
        if (new_row_data is None) == (updated_row_data is None) and not dataframe_already_updated:
            raise ValueError('Exactly one of new_row_data or updated_row_data must be provided')

        try:
            row_number = self.row_number_by_filepath[filepath]
        except KeyError:
            # Row has been deleted, nothing to do here:
            return

        filepath_colname = ('filepath',) + ('',) * (self.nlevels - 1)
        assert filepath == self.dataframe.at[row_number, filepath_colname]

        if updated_row_data is not None and not dataframe_already_updated:
            for group, name in updated_row_data:
                column_name = (group, name) + ('',) * (self.nlevels - 2)
                value = updated_row_data[group, name]
                try:
                    self.dataframe.at[row_number, column_name] = value
                except ValueError:
                    # did the column not already exist when we tried to set an iterable?
                    if not column_name in self.dataframe.columns:
                        # create it with a non-iterable and then overwrite with the iterable value:
                        self.dataframe.at[row_number, column_name] = None
                    else:
                        # Incompatible datatype - convert the datatype of the column to
                        # 'object'
                        self.dataframe[column_name] = self.dataframe[column_name].astype('object')
                    # Now that the column exists and has dtype object, we can set the value:
                    self.dataframe.at[row_number, column_name] = value

            dataframe_already_updated = True

        if not dataframe_already_updated:
            if new_row_data is None:
                raise ValueError("If dataframe_already_updated is False, then new_row_data, as returned "
                                 "by dataframe_utils.get_dataframe_from_shot(filepath) must be provided.")
            self.dataframe = replace_with_padding(self.dataframe, new_row_data, row_number)
            self.update_column_levels()

        # Check and create necessary new columns in the Qt model:
        new_column_names = set(self.dataframe.columns) - set(self.column_names.values())
        new_columns_start = self._model.columnCount()
        self._model.insertColumns(new_columns_start, len(new_column_names))
        for i, column_name in enumerate(sorted(new_column_names)):
            # Set the header label of the new column:
            column_number = new_columns_start + i
            self.column_names[column_number] = column_name
            self.column_indices[column_name] = column_number
            if column_name in self.deleted_columns_visible:
                # Restore the former visibility of this column if we've
                # seen one with its name before:
                visible = self.deleted_columns_visible[column_name]
                self.columns_visible[column_number] = visible
                self._view.setColumnHidden(column_number, not visible)
            else:
                # new columns are visible by default:
                self.columns_visible[column_number] = True
            column_name_as_string = '\n'.join(column_name).strip()
            header_item = QtGui.QStandardItem(column_name_as_string)
            header_item.setToolTip(column_name_as_string)
            self._model.setHorizontalHeaderItem(column_number, header_item)

        # Check and remove any no-longer-needed columns in the Qt model:
        defunct_column_names = (set(self.column_names.values()) - set(self.dataframe.columns)
                                - {self.column_names[self.COL_STATUS], self.column_names[self.COL_FILEPATH]})
        defunct_column_indices = [self.column_indices[column_name] for column_name in defunct_column_names]
        for column_number in sorted(defunct_column_indices, reverse=True):
            # Remove columns from the Qt model. In reverse order so that
            # removals do not change the position of columns yet to be
            # removed.
            self._model.removeColumn(column_number)
            # Save whether or not the column was visible when it was
            # removed (so that if it is re-added the visibility will be retained):
            self.deleted_columns_visible[self.column_names[column_number]] = self.columns_visible[column_number]
            del self.column_names[column_number]
            del self.columns_visible[column_number]

        if defunct_column_indices:
            # Renumber the keys of self.columns_visible and self.column_names to reflect deletions:
            self.column_names = {newindex: name for newindex, (oldindex, name) in enumerate(sorted(self.column_names.items()))}
            self.columns_visible = {newindex: visible for newindex, (oldindex, visible) in enumerate(sorted(self.columns_visible.items()))}
            # Update the inverse mapping of self.column_names:
            self.column_indices = {name: index for index, name in self.column_names.items()}

        # Update the data in the Qt model:
        dataframe_row = self.dataframe.iloc[row_number].to_dict()
        for column_number, column_name in self.column_names.items():
            if not isinstance(column_name, tuple):
                # One of our special columns, does not correspond to a column in the dataframe:
                continue
            if updated_row_data is not None:
                # Must remove empty strings from tuple to compare with updated_row_data:
                if tuple(s for s in column_name if s) not in updated_row_data:
                    continue
            value = dataframe_row[column_name]
            if isinstance(value, float):
                value_str = lyse.utils.scientific_notation(value)
            else:
                value_str = str(value)
            lines = value_str.splitlines()
            if len(lines) > 1:
                short_value_str = lines[0] + ' ...'
            else:
                short_value_str = value_str

            item = self._model.item(row_number, column_number)
            if item is None:
                # This is the first time we've written a value to this part of the model:
                item = QtGui.QStandardItem(short_value_str)
                item.setData(QtCore.Qt.AlignCenter, QtCore.Qt.TextAlignmentRole)
                self._model.setItem(row_number, column_number, item)
            else:
                item.setText(short_value_str)
            item.setToolTip(repr(value))

        for i, column_name in enumerate(sorted(new_column_names)):
            # Resize any new columns to fit contents:
            column_number = new_columns_start + i
            self._view.resizeColumnToContents(column_number)

        if new_column_names or defunct_column_names:
            self.columns_changed.emit()

        # unblock signals to the model and tell it to update
        self._model.blockSignals(False)
        self._model.layoutChanged.emit()

    @inmain_decorator()
    def set_status_percent(self, filepath, status_percent):
        try:
            row_number = self.row_number_by_filepath[filepath]
        except KeyError:
            # Row has been deleted, nothing to do here:
            return
        status_item = self._model.item(row_number, self.COL_STATUS)
        status_item.setData(status_percent, self.ROLE_STATUS_PERCENT)

    def new_row(self, filepath, done=False):
        status_item = QtGui.QStandardItem()
        if done:
            status_item.setData(100, self.ROLE_STATUS_PERCENT)
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/tick'))
        else:
            status_item.setData(0, self.ROLE_STATUS_PERCENT)
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/tick'))
        name_item = QtGui.QStandardItem(filepath)
        return [status_item, name_item]

    def renumber_rows(self, add_from=0):
        """Add/update row indices - the rows are numbered in simple sequential
        order for easy comparison with the dataframe. add_from allows you to
        only add numbers for new rows from the given index as a performance
        optimisation, though if the number of digits changes, all rows will
        still be renumbered. add_from should not be used if rows have been
        deleted."""
        n_digits = len(str(self._model.rowCount()))
        if n_digits != self._previous_n_digits:
            # All labels must be updated:
            add_from = 0
        self._previous_n_digits = n_digits

        if add_from == 0:
            self.row_number_by_filepath = {}

        for row_number in range(add_from, self._model.rowCount()):
            vertical_header_item = self._model.verticalHeaderItem(row_number)
            row_number_str = str(row_number).rjust(n_digits)
            vert_header_text = '{}. '.format(row_number_str)
            filepath_item = self._model.item(row_number, self.COL_FILEPATH)
            filepath = filepath_item.text()
            self.row_number_by_filepath[filepath] = row_number
            if self.integer_indexing:
                header_cols = ['sequence_index', 'run number', 'run repeat']
                header_strings = []
                for col in header_cols:
                    val = self.dataframe[col].values[row_number]
                    if pandas.notna(val):
                        header_strings.append('{:04d}'.format(val))
                    else:
                        header_strings.append('----')
                vert_header_text += ' | '.join(header_strings)
            else:
                basename = os.path.splitext(os.path.basename(filepath))[0]
                vert_header_text += basename
            vertical_header_item.setText(vert_header_text)
    
    @inmain_decorator()
    def add_files(self, filepaths, new_row_data, done=False):
        """Add files to the dataframe model. New_row_data should be a
        dataframe containing the new rows."""

        to_add = []

        # Check for duplicates:
        for filepath in filepaths:
            if filepath in self.row_number_by_filepath or filepath in to_add:
                self.app.output_box.output('Warning: Ignoring duplicate shot %s\n' % filepath, red=True)
                if new_row_data is not None:
                    df_row_index = np.where(new_row_data['filepath'].values == filepath)
                    new_row_data = new_row_data.drop(df_row_index[0])
                    new_row_data.index = pandas.Index(range(len(new_row_data)))
            else:
                to_add.append(filepath)

        assert len(new_row_data) == len(to_add)

        if to_add:
            # Update the dataframe:
            self.dataframe = concat_with_padding(self.dataframe, new_row_data)
            self.update_column_levels()

        self.app.filebox.set_add_shots_progress(None, None, "updating filebox")

        for filepath in to_add:
            # Add the new rows to the Qt model:
            self._model.appendRow(self.new_row(filepath, done=done))
            vert_header_item = QtGui.QStandardItem('...loading...')
            self._model.setVerticalHeaderItem(self._model.rowCount() - 1, vert_header_item)
            self._view.resizeRowToContents(self._model.rowCount() - 1)

        self.renumber_rows(add_from=self._model.rowCount()-len(to_add))

        # Update the Qt model:
        for filepath in to_add:
            self.update_row(filepath, dataframe_already_updated=True)

        self.app.filebox.set_add_shots_progress(None, None, None)        
            

    @inmain_decorator()
    def get_first_incomplete(self):
        """Returns the filepath of the first shot in the model that has not
        been analysed"""
        for row in range(self._model.rowCount()):
            status_item = self._model.item(row, self.COL_STATUS)
            if status_item.data(self.ROLE_STATUS_PERCENT) != 100:
                filepath_item = self._model.item(row, self.COL_FILEPATH)
                return filepath_item.text()

class FileBox(object):

    def __init__(self, app, container, exp_config, to_singleshot, from_singleshot, to_multishot, from_multishot):
        self.app = app

        self.exp_config = exp_config
        self.to_singleshot = to_singleshot
        self.to_multishot = to_multishot
        self.from_singleshot = from_singleshot
        self.from_multishot = from_multishot

        self.logger = logging.getLogger('lyse.FileBox')
        self.logger.info('starting')

        loader = UiLoader()
        loader.registerCustomWidget(lyse.widgets.TableView)
        self.ui = loader.load(os.path.join(lyse.utils.LYSE_DIR, 'user_interface/filebox.ui'))
        self.ui.progressBar_add_shots.hide()
        container.addWidget(self.ui)
        self.shots_model = DataFrameModel(self.app, self.ui.tableView, self.exp_config)
        set_auto_scroll_to_end(self.ui.tableView.verticalScrollBar())
        self.edit_columns_dialog = EditColumns(self, self.shots_model.column_names, self.shots_model.columns_visible)

        self.last_opened_shots_folder = self.exp_config.get('paths', 'experiment_shot_storage')

        self.connect_signals()

        self.analysis_paused = False
        self.multishot_required = False
        
        # An Event to let the analysis thread know to check for shots that
        # need analysing, rather than using a time.sleep:
        self.analysis_pending = threading.Event()

        # The folder that the 'add shots' dialog will open to:
        self.current_folder = self.exp_config.get('paths', 'experiment_shot_storage')

        # A queue for storing incoming files from the ZMQ server so
        # the server can keep receiving files even if analysis is slow
        # or paused:
        self.incoming_queue = queue.Queue()

        # Start the thread to handle incoming files, and store them in
        # a buffer if processing is paused:
        self.incoming = threading.Thread(target=self.incoming_buffer_loop)
        self.incoming.daemon = True
        self.incoming.start()

        self.analysis = threading.Thread(target = self.analysis_loop)
        self.analysis.daemon = True
        self.analysis.start()

    def connect_signals(self):
        self.ui.pushButton_edit_columns.clicked.connect(self.on_edit_columns_clicked)
        self.shots_model.columns_changed.connect(self.on_columns_changed)
        self.ui.toolButton_add_shots.clicked.connect(self.on_add_shot_files_clicked)
        self.ui.toolButton_remove_shots.clicked.connect(self.shots_model.on_remove_selection)
        self.ui.tableView.doubleLeftClicked.connect(self.shots_model.on_double_click)
        self.ui.pushButton_analysis_running.toggled.connect(self.on_analysis_running_toggled)
        self.ui.pushButton_mark_as_not_done.clicked.connect(self.on_mark_selection_not_done_clicked)
        self.ui.pushButton_run_multishot_analysis.clicked.connect(self.on_run_multishot_analysis_clicked)
        
    def on_edit_columns_clicked(self):
        self.edit_columns_dialog.show()

    def on_columns_changed(self):
        column_names = self.shots_model.column_names
        columns_visible = self.shots_model.columns_visible
        self.edit_columns_dialog.update_columns(column_names, columns_visible)

    def on_add_shot_files_clicked(self):
        shot_files = QtWidgets.QFileDialog.getOpenFileNames(self.ui,
                                                        'Select shot files',
                                                        self.last_opened_shots_folder,
                                                        "HDF5 files (*.h5)")
        if type(shot_files) is tuple:
            shot_files, _ = shot_files

        if not shot_files:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        shot_files = [os.path.abspath(shot_file) for shot_file in shot_files]

        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_shots_folder = os.path.dirname(shot_files[0])
        # Queue the files to be opened:
        for filepath in shot_files:
            self.incoming_queue.put(filepath)

    def on_analysis_running_toggled(self, pressed):
        if pressed:
            self.analysis_paused = True
            self.ui.pushButton_analysis_running.setIcon(QtGui.QIcon(':qtutils/fugue/control'))
            self.ui.pushButton_analysis_running.setText('Analysis paused')
        else:
            self.analysis_paused = False
            self.ui.pushButton_analysis_running.setIcon(QtGui.QIcon(':qtutils/fugue/control'))
            self.ui.pushButton_analysis_running.setText('Analysis running')
            self.analysis_pending.set()
     
    def on_mark_selection_not_done_clicked(self):
        self.shots_model.mark_selection_not_done()
        # Let the analysis loop know to look for these shots:
        self.analysis_pending.set()
        
    def on_run_multishot_analysis_clicked(self):
        self.multishot_required = True
        self.analysis_pending.set()
        
    def set_columns_visible(self, columns_visible):
        self.shots_model.set_columns_visible(columns_visible)

    @inmain_decorator()
    def set_add_shots_progress(self, completed, total, message):
        self.ui.progressBar_add_shots.setFormat("Adding shots: [{}] %v/%m (%p%)".format(message))
        if completed == total and message is None:
            self.ui.progressBar_add_shots.hide()
        else:
            if total is not None:
                self.ui.progressBar_add_shots.setMaximum(total)
            if completed is not None:
                self.ui.progressBar_add_shots.setValue(completed)
            if self.ui.progressBar_add_shots.isHidden():
                self.ui.progressBar_add_shots.show()
        if completed is None and total is None and message is not None:
            # Ensure a repaint when only the message changes:
            self.ui.progressBar_add_shots.repaint()

    def incoming_buffer_loop(self):
        """We use a queue as a buffer for incoming shots. We don't want to hang and not
        respond to a client submitting shots, so we just let shots pile up here until we can get to them.
        The downside to this is that we can't return errors to the client if the shot cannot be added,
        but the suggested workflow is to handle errors here anyway. A client running shots shouldn't stop
        the experiment on account of errors from the analyis stage, so what's the point of passing errors to it?
        We'll just raise errors here and the user can decide what to do with them."""
        logger = logging.getLogger('lyse.FileBox.incoming')
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        n_shots_added = 0
        while True:
            try:
                filepaths = []
                filepath = self.incoming_queue.get()
                filepaths.append(filepath)
                if self.incoming_queue.qsize() == 0:
                    # Wait momentarily in case more arrive so we can batch process them:
                    time.sleep(0.1)
                # Batch process to decrease number of dataframe concatenations:
                batch_size = len(self.shots_model.dataframe) // 3 + 1 
                while True:
                    try:
                        filepath = self.incoming_queue.get(False)
                    except queue.Empty:
                        break
                    else:
                        filepaths.append(filepath)
                        if len(filepaths) >= batch_size:
                            break
                logger.info('adding:\n%s' % '\n'.join(filepaths))
                if n_shots_added == 0:
                    total_shots = self.incoming_queue.qsize() + len(filepaths)
                    self.set_add_shots_progress(1, total_shots, "reading shot files")

                # Remove duplicates from the list (preserving order) in case the
                # client sent the same filepath multiple times:
                filepaths = sorted(set(filepaths), key=filepaths.index) # Inefficient but readable
                # We open the HDF5 files here outside the GUI thread so as not to hang the GUI:
                dataframes = []
                indices_of_files_not_found = []
                for i, filepath in enumerate(filepaths):
                    try:
                        dataframe = get_dataframe_from_shot(filepath)
                        dataframes.append(dataframe)
                    except IOError:
                        self.app.output_box.output('Warning: Ignoring shot file not found or not readable %s\n' % filepath, red=True)
                        indices_of_files_not_found.append(i)
                    n_shots_added += 1
                    shots_remaining = self.incoming_queue.qsize()
                    total_shots = n_shots_added + shots_remaining + len(filepaths) - (i + 1)
                    self.set_add_shots_progress(n_shots_added, total_shots, "reading shot files")
                self.set_add_shots_progress(n_shots_added, total_shots, "concatenating dataframes")
                if dataframes:
                    new_row_data = concat_with_padding(*dataframes)
                else:
                    new_row_data = None

                # Do not add the shots that were not found on disk. Reverse
                # loop so that removing an item doesn't change the indices of
                # subsequent removals:
                for i in reversed(indices_of_files_not_found):
                    del filepaths[i]
                if filepaths:
                    self.shots_model.add_files(filepaths, new_row_data)
                    # Let the analysis loop know to look for new shots:
                    self.analysis_pending.set()
                if shots_remaining == 0:
                    self.set_add_shots_progress(n_shots_added, total_shots, None)
                    n_shots_added = 0 # reset our counter for the next batch
                
            except Exception:
                # Keep this incoming loop running at all costs, but make the
                # otherwise uncaught exception visible to the user:
                zprocess.raise_exception_in_thread(sys.exc_info())

    def analysis_loop(self):
        logger = logging.getLogger('lyse.FileBox.analysis_loop')
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        while True:
            try:
                self.analysis_pending.wait()
                self.analysis_pending.clear()
                at_least_one_shot_analysed = False
                while True:
                    if not self.analysis_paused:
                        # Find the first shot that has not finished being analysed:
                        filepath = self.shots_model.get_first_incomplete()
                        if filepath is not None:
                            logger.info('analysing: %s'%filepath)
                            self.do_singleshot_analysis(filepath)
                            at_least_one_shot_analysed = True
                        if filepath is None and at_least_one_shot_analysed:
                            self.multishot_required = True
                        if filepath is None:
                            break
                        if self.multishot_required:
                            logger.info('doing multishot analysis')
                            self.do_multishot_analysis()
                    else:
                        logger.info('analysis is paused')
                        break
                if self.multishot_required:
                    logger.info('doing multishot analysis')
                    self.do_multishot_analysis()
            except Exception:
                etype, value, tb = sys.exc_info()
                orig_exception = ''.join(traceback.format_exception_only(etype, value))
                message = ('Analysis loop encountered unexpected exception. ' +
                           'This is a bug and should be reported. The analysis ' +
                           'loop is continuing, but lyse may be in an inconsistent state. '
                           'Restart lyse, or continue at your own risk. '
                           'Original exception was:\n\n' + orig_exception)
                # Raise the exception in a thread so we can keep running
                zprocess.raise_exception_in_thread((RuntimeError, RuntimeError(message), tb))
                self.pause_analysis()
            
   
    @inmain_decorator()
    def pause_analysis(self):
        # This automatically triggers the slot that sets self.analysis_paused
        self.ui.pushButton_analysis_running.setChecked(True)
        
    def do_singleshot_analysis(self, filepath):
        # Check the shot file exists before sending it to the singleshot
        # routinebox. This does not guarantee it won't have been deleted by
        # the time the routinebox starts running analysis on it, but by
        # detecting it now we can most of the time avoid the user code
        # coughing exceptions due to the file not existing. Which would also
        # not be a problem, but this way we avoid polluting the outputbox with
        # more errors than necessary.
        if not os.path.exists(filepath):
            self.shots_model.mark_as_deleted_off_disk(filepath)
            return
        self.to_singleshot.put(filepath)
        while True:
            signal, status_percent, updated_data = self.from_singleshot.get()
            for file in updated_data:
                # Update the data for all the rows with new data:
                self.shots_model.update_row(file, updated_row_data=updated_data[file])
            # Update the status percent for the the row on which analysis is actually
            # running:
            if status_percent is not None:
                self.shots_model.set_status_percent(filepath, status_percent)
            if signal == 'done':
                return
            if signal == 'error':
                if not os.path.exists(filepath):
                    # Do not pause if the file has been deleted. An error is
                    # no surprise there:
                    self.shots_model.mark_as_deleted_off_disk(filepath)
                else:
                    self.pause_analysis()
                return
            if signal == 'progress':
                continue
            raise ValueError('invalid signal %s' % str(signal))
                        
    def do_multishot_analysis(self):
        self.to_multishot.put(None)
        while True:
            signal, _, updated_data = self.from_multishot.get()
            for file in updated_data:
                self.shots_model.update_row(file, updated_row_data=updated_data[file])
            if signal == 'done':
                self.multishot_required = False
                return
            elif signal == 'error':
                self.pause_analysis()
                return
            