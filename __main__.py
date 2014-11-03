from __future__ import division, unicode_literals, print_function, absolute_import  # Ease the transition to Python 3

# stdlib imports

import os
import sys
import socket
import Queue
import logging
import threading
import signal

# Turn on our error catching for all subsequent imports
import labscript_utils.excepthook


# 3rd party imports:

import numpy as np
import labscript_utils.h5_lock
import h5py
import pandas
import sip

# Have to set PyQt API via sip before importing PyQt:
API_NAMES = ["QDate", "QDateTime", "QString", "QTextStream", "QTime", "QUrl", "QVariant"]
API_VERSION = 2
for name in API_NAMES:
    sip.setapi(name, API_VERSION)

from PyQt4 import QtCore, QtGui
from PyQt4.QtCore import pyqtSignal as Signal
from PyQt4.QtCore import pyqtSlot as Slot

try:
    from labscript_utils import check_version
except ImportError:
    raise ImportError('Require labscript_utils > 2.1.0')

check_version('labscript_utils', '2.1', '3')
check_version('qtutils', '1.5.4', '2')
check_version('zprocess', '1.1.6', '2')

import zprocess.locking
from zprocess import ZMQServer
from zmq import ZMQError

from labscript_utils.labconfig import LabConfig, config_prefix
from labscript_utils.setup_logging import setup_logging
from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets
import labscript_utils.shared_drive as shared_drive
import lyse

from lyse.dataframe_utilities import (concat_with_padding,
                                      get_dataframe_from_shot,
                                      replace_with_padding)

from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager
from qtutils.outputbox import OutputBox
from qtutils.auto_scroll_to_end import set_auto_scroll_to_end
import qtutils.icons

# Set working directory to lyse folder, resolving symlinks
lyse_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(lyse_dir)

# Set a meaningful name for zprocess.locking's client id:
zprocess.locking.set_client_process_name('lyse')


def set_win_appusermodel(window_id):
    from labscript_utils.winshell import set_appusermodel, appids, app_descriptions
    icon_path = os.path.abspath('lyse.ico')
    executable = sys.executable.lower()
    if not executable.endswith('w.exe'):
        executable = executable.replace('.exe', 'w.exe')
    relaunch_command = executable + ' ' + os.path.abspath(__file__.replace('.pyc', '.py'))
    relaunch_display_name = app_descriptions['lyse']
    set_appusermodel(window_id, appids['lyse'], icon_path, relaunch_command, relaunch_display_name)


@inmain_decorator()
def error_dialog(message):
    QtGui.QMessageBox.warning(app.ui, 'lyse', message)


@inmain_decorator()
def question_dialog(message):
    reply = QtGui.QMessageBox.question(app.ui, 'lyse', message,
                                       QtGui.QMessageBox.Yes | QtGui.QMessageBox.No)
    return (reply == QtGui.QMessageBox.Yes)


def scientific_notation(x, sigfigs=4):
    """Returns a unicode string of the float f in scientific notation"""

    times = u'\u00d7'
    thinspace = u'\u2009'
    sups = {u'-': u'\u207b',
            u'0': u'\u2070',
            u'1': u'\xb9',
            u'2': u'\xb2',
            u'3': u'\xb3',
            u'4': u'\u2074',
            u'5': u'\u2075',
            u'6': u'\u2076',
            u'7': u'\u2077',
            u'8': u'\u2078',
            u'9': u'\u2079'}

    if not isinstance(x, float):
        raise TypeError('x must be floating point number')
    if np.isnan(x) or np.isinf(x):
        return str(x)
    if x != 0:
        exponent = int(np.floor(np.log10(np.abs(x))))
        # Only multiples of 10^3
        exponent = int(np.floor(exponent / 3) * 3)
    else:
        exponent = 0
    significand = x / 10 ** exponent
    pre_decimal, post_decimal = divmod(significand, 1)
    digits = sigfigs - len(str(int(pre_decimal)))
    significand = round(significand, digits)
    result = str(significand)
    if exponent:
        superscript = ''.join(sups.get(char, char) for char in str(exponent))
        result += thinspace + times + thinspace + '10' + superscript
    return result


class WebServer(ZMQServer):

    def handler(self, request_data):
        logger.info('WebServer request: %s' % str(request_data))
        if request_data == 'hello':
            return 'hello'
        elif request_data == 'get dataframe':
            return app.filebox.shots_model.dataframe
        elif isinstance(request_data, dict):
            if 'filepath' in request_data:
                h5_filepath = labscript_utils.shared_drive.path_to_local(request_data['filepath'])
                app.filebox.incoming_queue.put(h5_filepath)
                return 'added successfully'
        return ("error: operation not supported. Recognised requests are:\n "
                "'get dataframe'\n 'hello'\n {'filepath': <some_h5_filepath>}")


class LyseMainWindow(QtGui.QMainWindow):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)

    def event(self, event):
        result = QtGui.QMainWindow.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result


class RoutineBox(object):

    def __init__(self, container, exp_config, filebox, from_filebox, to_filebox, output_box_port, multishot=False):
        self.multishot = multishot
        self.filebox = filebox
        self.exp_config = exp_config
        self.from_filebox = from_filebox
        self.to_filebox = to_filebox

        loader = UiLoader()
        self.ui = loader.load('routinebox.ui')
        container.addWidget(self.ui)

        if multishot:
            self.ui.groupBox.setTitle('Multishot routines')

        else:
            self.ui.groupBox.setTitle('Singleshot routines')

        self.model = UneditableModel()
        self.header = HorizontalHeaderViewWithWidgets(self.model)
        self.select_all_checkbox = QtGui.QCheckBox()
        self.select_all_checkbox.setTristate(False)
        self.ui.treeView.setHeader(self.header)
        self.header.setStretchLastSection(True)
        self.ui.treeView.setModel(self.model)

        self.ui.treeView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        # Make the actions for the context menu:
        self.action_set_selected_visible = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'set selected routines active',  self.ui)
        self.action_set_selected_hidden = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'set selected routines inactive',  self.ui)

        self.connect_signals()

    def connect_signals(self):
        pass


class EditColumnsDialog(QtGui.QDialog):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)
    close_signal = Signal()

    def __init__(self):
        QtGui.QDialog.__init__(self, None, QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowTitleHint)

    def event(self, event):
        result = QtGui.QDialog.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result

    def closeEvent(self, event):
        self.close_signal.emit()
        event.ignore()


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
        self.ui = loader.load('edit_columns.ui', EditColumnsDialog())

        self.model = UneditableModel()
        self.header = HorizontalHeaderViewWithWidgets(self.model)
        self.select_all_checkbox = QtGui.QCheckBox()
        self.select_all_checkbox.setTristate(False)
        self.ui.treeView.setHeader(self.header)
        self.proxy_model = QtGui.QSortFilterProxyModel()
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
        self.action_set_selected_visible = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'Show selected columns',  self.ui)
        self.action_set_selected_hidden = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'Hide selected columns',  self.ui)

        self.connect_signals()
        self.populate_model(column_names, self.columns_visible)

    def connect_signals(self):
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)
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
        # Which indices in self.columns_visible the row numbers correspond to
        self.column_indices = {}
        for column_index, name in sorted(column_names.items(), key=lambda s: s[1]):
            if not isinstance(name, tuple):
                # one of our special columns, ignore:
                continue
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
            name_item.setData(name_as_string, self.ROLE_SORT_DATA)
            self.model.appendRow([visible_item, name_item])
            self.column_indices[self.model.rowCount() - 1] = column_index

        self.ui.treeView.resizeColumnToContents(self.COL_VISIBLE)
        self.ui.treeView.resizeColumnToContents(self.COL_NAME)
        self.update_select_all_checkstate()
        self.ui.treeView.sortByColumn(self.COL_NAME, QtCore.Qt.AscendingOrder)

    def on_treeView_context_menu_requested(self, point):
        menu = QtGui.QMenu(self.ui)
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
        for row in range(self.model.rowCount()):
            visible_item = self.model.item(row, self.COL_VISIBLE)
            self.update_visible_state(visible_item, state)
        self.do_sort()
        self.select_all_checkbox.setTristate(False)
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
        self.columns_visible = columns_visible.copy()
        self.column_names = column_names.copy()
        # If editing is cancelled, any new columns should be set to visible.
        # So we will add any new columns to our old_columns_visible dict,
        # with them set to visible.
        for index in self.columns_visible:
            if index not in self.old_columns_visible:
                self.old_columns_visible[index] = True
        self.populate_model(column_names, self.columns_visible)

    def show(self):
        self.old_columns_visible = self.columns_visible.copy()
        self.ui.show()
        for column in range(2):
            self.ui.treeView.resizeColumnToContents(column)

    def close(self):
        self.columns_visible = self.old_columns_visible.copy()
        self.filebox.set_columns_visible(self.columns_visible)
        self.populate_model(self.column_names, self.columns_visible)
        self.ui.hide()

    def cancel(self):
        self.ui.close()

    def make_it_so(self):
        self.ui.hide()


class ItemDelegate(QtGui.QStyledItemDelegate):

    """An item delegate with a fixed height and a progress bar in one column"""
    EXTRA_ROW_HEIGHT = 2

    def __init__(self, view, model, col_status, role_status_percent):
        self.view = view
        self.model = model
        self.COL_STATUS = col_status
        self.ROLE_STATUS_PERCENT = role_status_percent
        QtGui.QStyledItemDelegate.__init__(self)

    def sizeHint(self, *args):
        fontmetrics = QtGui.QFontMetrics(self.view.font())
        text_height = fontmetrics.height()
        row_height = text_height + self.EXTRA_ROW_HEIGHT
        size = QtGui.QStyledItemDelegate.sizeHint(self, *args)
        return QtCore.QSize(size.width(), row_height)

    def paint(self, painter, option, index):
        if index.column() == self.COL_STATUS:
            status_percent = self.model.data(index, self.ROLE_STATUS_PERCENT)
            if status_percent == 100:
                # Render as a normal item - this shows whatever icon is set instead of a progress bar.
                return QtGui.QStyledItemDelegate.paint(self, painter, option, index)
            else:
                # Method of rendering a progress bar into the view copied from
                # Qt's 'network-torrent' example:
                # http://qt-project.org/doc/qt-4.8/network-torrent-torrentclient-cpp.html

                # Set up a QStyleOptionProgressBar to precisely mimic the
                # environment of a progress bar.
                progress_bar_option = QtGui.QStyleOptionProgressBarV2()
                progress_bar_option.state = QtGui.QStyle.State_Enabled
                progress_bar_option.direction = qapplication.layoutDirection()
                progress_bar_option.rect = option.rect
                progress_bar_option.fontMetrics = qapplication.fontMetrics()
                progress_bar_option.minimum = 0
                progress_bar_option.maximum = 100
                progress_bar_option.textAlignment = QtCore.Qt.AlignCenter
                progress_bar_option.textVisible = True

                # Set the progress and text values of the style option.
                progress_bar_option.progress = status_percent
                progress_bar_option.text = '%d%%' % status_percent

                # Draw the progress bar onto the view.
                qapplication.style().drawControl(QtGui.QStyle.CE_ProgressBar, progress_bar_option, painter)
        else:
            return QtGui.QStyledItemDelegate.paint(self, painter, option, index)


class UneditableModel(QtGui.QStandardItemModel):

    def flags(self, index):
        """Return flags as normal except that the ItemIsEditable
        flag is always False"""
        result = QtGui.QStandardItemModel.flags(self, index)
        return result & ~QtCore.Qt.ItemIsEditable


class DataFrameModel(QtCore.QObject):

    COL_ACTIVE = 0
    COL_STATUS = 1
    COL_FILEPATH = 2

    ROLE_STATUS_PERCENT = QtCore.Qt.UserRole + 1

    columns_changed = Signal()

    def __init__(self, view):
        QtCore.QObject.__init__(self)
        self._view = view
        self._model = UneditableModel()

        headerview_style = """
                           QHeaderView {
                             font-size: 8pt;
                             color: black;
                           }
                           QHeaderView::section{
                             font-size: 8pt;
                             color: black;
                           }
                           """
        self._header = HorizontalHeaderViewWithWidgets(self._model)
        self._vertheader = QtGui.QHeaderView(QtCore.Qt.Vertical)
        self._vertheader.setResizeMode(QtGui.QHeaderView.Fixed)
        self._vertheader.setStyleSheet(headerview_style)
        self._header.setStyleSheet(headerview_style)

        self._vertheader.setHighlightSections(False)
        self._view.setModel(self._model)
        self._view.setHorizontalHeader(self._header)
        self._view.setVerticalHeader(self._vertheader)
        self._delegate = ItemDelegate(self._view, self._model, self.COL_STATUS, self.ROLE_STATUS_PERCENT)
        self._view.setItemDelegate(self._delegate)
        self._view.setSelectionBehavior(QtGui.QTableView.SelectRows)
        self._view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

        # This dataframe will contain all the scalar data
        # from the shot files that are currently open:
        index = pandas.MultiIndex.from_tuples([('filepath', '')])
        self.dataframe = pandas.DataFrame({'filepath': []}, columns=index)
        # How many levels the dataframe's multiindex has:
        self.nlevels = self.dataframe.columns.nlevels

        active_item = QtGui.QStandardItem()
        active_item.setToolTip('whether or not single-shot analysis routines will be run on each shot')
        self._model.setHorizontalHeaderItem(self.COL_ACTIVE, active_item)
        self.select_all_checkbox = QtGui.QCheckBox()
        self.select_all_checkbox.setToolTip('whether or not single-shot analysis routines will be run on each shot')
        self.select_all_checkbox.setCheckState(QtCore.Qt.Checked)
        self.select_all_checkbox.setTristate(False)
        self._header.setWidget(self.COL_ACTIVE, self.select_all_checkbox)

        status_item = QtGui.QStandardItem('% done')
        status_item.setToolTip('status/progress of single-shot analysis')
        self._model.setHorizontalHeaderItem(self.COL_STATUS, status_item)

        filepath_item = QtGui.QStandardItem('filepath')
        filepath_item.setToolTip('filepath')
        self._model.setHorizontalHeaderItem(self.COL_FILEPATH, filepath_item)

        self._view.resizeColumnToContents(self.COL_ACTIVE)
        self._view.setColumnWidth(self.COL_STATUS, 70)
        self._view.setColumnWidth(self.COL_FILEPATH, 100)

        # Column indices to names and vice versa for fast lookup:
        self.column_indices = {'__active': self.COL_ACTIVE,
                               '__status': self.COL_STATUS, ('filepath', ''): self.COL_FILEPATH}
        self.column_names = {self.COL_ACTIVE: '__active',
                             self.COL_STATUS: '__status', self.COL_FILEPATH: ('filepath', '')}
        self.columns_visible = {self.COL_ACTIVE: True, self.COL_STATUS: True, self.COL_FILEPATH: True}

        # Make the actions for the context menu:
        self.action_set_selected_active = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'Set selected shots active',  self._view)
        self.action_set_selected_inactive = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'Set selected shots inactive',  self._view)
        self.action_remove_selected = QtGui.QAction(
            QtGui.QIcon(':qtutils/fugue/minus'), 'Remove selected shots',  self._view)

        self.connect_signals()

    def connect_signals(self):
        self._model.itemChanged.connect(self.on_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.model_item_changed_disconnected = DisconnectContextManager(
            self._model.itemChanged, self.on_model_item_changed)
        self.select_all_checkbox.stateChanged.connect(self.on_select_all_state_changed)
        self.select_all_checkbox_state_changed_disconnected = DisconnectContextManager(
            self.select_all_checkbox.stateChanged, self.on_select_all_state_changed)
        self._view.customContextMenuRequested.connect(self.on_view_context_menu_requested)
        self.action_set_selected_active.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Checked))
        self.action_set_selected_inactive.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Unchecked))
        self.action_remove_selected.triggered.connect(self.on_remove_selection)

    def on_select_all_state_changed(self, state):
        with self.model_item_changed_disconnected:
            for row in range(self._model.rowCount()):
                active_item = self._model.item(row, self.COL_ACTIVE)
                active_item.setCheckState(state)
            self.select_all_checkbox.setTristate(False)

    def update_select_all_checkstate(self):
        with self.select_all_checkbox_state_changed_disconnected:
            all_states = []
            for row in range(self._model.rowCount()):
                active_item = self._model.item(row, self.COL_ACTIVE)
                all_states.append(active_item.checkState())
            if all(state == QtCore.Qt.Checked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Checked)
            elif all(state == QtCore.Qt.Unchecked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Unchecked)
            else:
                self.select_all_checkbox.setCheckState(QtCore.Qt.PartiallyChecked)

    def on_model_item_changed(self, item):
        if item.column() == self.COL_ACTIVE:
            self.update_select_all_checkstate()

    def get_model_row_by_filepath(self, filepath):
        filepath_colname = ('filepath',) + ('',) * (self.nlevels - 1)
        possible_items = self._model.findItems(filepath, column=self.column_indices[filepath_colname])
        if len(possible_items) > 1:
            raise LookupError('Multiple items found')
        elif not possible_items:
            raise LookupError('No item found')
        item = possible_items[0]
        index = item.index()
        return index.row()

    def on_remove_selection(self):
        selected_indexes = self._view.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        if not question_dialog("Remove %d shots?" % len(selected_rows)):
            return
        # Remove from DataFrame first:
        self.dataframe = self.dataframe.drop(selected_rows)
        self.dataframe.index = pandas.Index(range(len(self.dataframe)))
        # Delete one at a time from Qt model, since indexes change with each deletion:
        while selected_rows:
            some_row = selected_rows.pop()
            self._model.removeRow(some_row)
            selected_indexes = self._view.selectedIndexes()
            selected_rows = set(index.row() for index in selected_indexes)
        self.update_select_all_checkstate()

    def on_view_context_menu_requested(self, point):
        menu = QtGui.QMenu(self._view)
        menu.addAction(self.action_set_selected_active)
        menu.addAction(self.action_set_selected_inactive)
        menu.addAction(self.action_remove_selected)
        menu.exec_(QtGui.QCursor.pos())

    def on_set_selected_triggered(self, active):
        selected_indexes = self._view.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        for row in selected_rows:
            active_item = self._model.item(row, self.COL_ACTIVE)
            active_item.setCheckState(active)
        self.update_select_all_checkstate()

    def set_columns_visible(self, columns_visible):
        self.columns_visible = columns_visible
        for column_index, visible in columns_visible.items():
            self._view.setColumnHidden(column_index, not visible)

    def update_column_levels(self):
        """Pads the keys and values of our lists of column names so that
        they still match those in the dataframe after the number of
        levels in its multiindex has increased"""
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

    def update_row(self, filepath, dataframe_already_updated=False):
        """"Updates a row in the dataframe and Qt model
        to the data in the HDF5 file for that shot"""
        # Update the row in the dataframe first:
        df_row_index = np.where(self.dataframe['filepath'].values == filepath)
        df_row_index = df_row_index[0][0]
        if not dataframe_already_updated:
            new_row_data = get_dataframe_from_shot(filepath)
            self.dataframe = replace_with_padding(self.dataframe, new_row_data, df_row_index)
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
            self.columns_visible[column_number] = True
            column_name_as_string = '\n'.join(column_name).strip()
            header_item = QtGui.QStandardItem(column_name_as_string)
            header_item.setToolTip(column_name_as_string)
            self._model.setHorizontalHeaderItem(column_number, header_item)

        # Update the data in the Qt model:
        model_row_number = self.get_model_row_by_filepath(filepath)
        dataframe_row = dict(self.dataframe.ix[df_row_index])
        for column_number, column_name in self.column_names.items():
            if not isinstance(column_name, tuple):
                # One of our special columns, does not correspond to a column in the dataframe:
                continue
            item = self._model.item(model_row_number, column_number)
            if item is None:
                # This is the first time we've written a value to this part of the model:
                item = QtGui.QStandardItem('NaN')
                item.setData(QtCore.Qt.AlignCenter, QtCore.Qt.TextAlignmentRole)
                self._model.setItem(model_row_number, column_number, item)
            # value = self.dataframe[column_name].values[df_row_index][0]
            value = dataframe_row[column_name]
            if isinstance(value, float):
                value_str = scientific_notation(value)
            else:
                value_str = str(value)
            lines = value_str.splitlines()
            if len(lines) > 1:
                short_value_str = lines[0] + ' ...'
            else:
                short_value_str = value_str
            item.setText(short_value_str)
            item.setToolTip(repr(value))
            vert_header_item = QtGui.QStandardItem(os.path.splitext(os.path.basename(filepath))[0])
            self._model.setVerticalHeaderItem(model_row_number, vert_header_item)

        for i, column_name in enumerate(sorted(new_column_names)):
            # Resize any new columns to fit contents:
            column_number = new_columns_start + i
            self._view.resizeColumnToContents(column_number)

        if new_column_names:
            self.columns_changed.emit()

    def new_row(self, filepath):
        active_item = QtGui.QStandardItem()
        active_item.setCheckable(True)
        active_item.setCheckState(QtCore.Qt.Checked)
        status_item = QtGui.QStandardItem()
        status_item.setData(0, self.ROLE_STATUS_PERCENT)
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/tick'))
        name_item = QtGui.QStandardItem(filepath)
        return [active_item, status_item, name_item]

    def add_file(self, filepath):
        if filepath in self.dataframe['filepath'].values:
            # Ignore duplicates:
            app.output_box.output('Adding duplicate shot %s ignored\n' % filepath, red=True)
            return
        # Add the new row to the model:
        self._model.appendRow(self.new_row(filepath))
        self._view.resizeRowToContents(self._model.rowCount() - 1)
        # Add the new row to the dataframe.
        new_row_data = get_dataframe_from_shot(filepath)
        self.dataframe = concat_with_padding(self.dataframe, new_row_data)
        self.update_column_levels()
        self.update_row(filepath, dataframe_already_updated=True)


class FileBox(object):

    def __init__(self, container, exp_config, to_singleshot, from_singleshot, to_multishot, from_multishot):

        self.exp_config = exp_config
        self.to_singleshot = to_singleshot
        self.to_multishot = to_multishot
        self.from_singleshot = from_singleshot
        self.from_multishot = from_multishot

        self.logger = logging.getLogger('LYSE.FileBox')
        self.logger.info('starting')

        loader = UiLoader()
        self.ui = loader.load('filebox.ui')
        container.addWidget(self.ui)
        self.shots_model = DataFrameModel(self.ui.tableView)
        set_auto_scroll_to_end(self.ui.tableView.verticalScrollBar())
        self.edit_columns_dialog = EditColumns(self, self.shots_model.column_names, self.shots_model.columns_visible)

        self.last_opened_shots_folder = self.exp_config.get('paths', 'experiment_shot_storage')

        self.connect_signals()

        self.analysis_loop_paused = False

        # An Event to let the analysis thread know to check for new shots,
        # rather than polling with time.sleep() in between:
        self.new_shots = threading.Event()

        # The folder that the 'add shots' dialog will open to:
        self.current_folder = self.exp_config.get('paths', 'experiment_shot_storage')

        # Whether the last scroll to the bottom of the treeview has been processed:
        self.scrolled = True

        # A queue for storing incoming files from the ZMQ server so
        # the server can keep receiving files even if analysis is slow
        # or paused:
        self.incoming_queue = Queue.Queue()

        # Start the thread to handle incoming files, and store them in
        # a buffer if processing is paused:
        self.incoming = threading.Thread(target=self.incoming_buffer_loop)
        self.incoming.daemon = True
        self.incoming.start()

        #self.analysis = threading.Thread(target = self.analysis_loop)
        #self.analysis.daemon = True
        # self.analysis.start()

        #self.adjustment.set_value(self.adjustment.upper - self.adjustment.page_size)

    def connect_signals(self):
        self.ui.pushButton_edit_columns.clicked.connect(self.on_edit_columns_clicked)
        self.shots_model.columns_changed.connect(self.on_columns_changed)
        self.ui.toolButton_add_shots.clicked.connect(self.on_add_shot_files_clicked)
        self.ui.toolButton_remove_shots.clicked.connect(self.shots_model.on_remove_selection)

    def on_edit_columns_clicked(self):
        self.edit_columns_dialog.show()

    def on_columns_changed(self):
        column_names = self.shots_model.column_names
        columns_visible = self.shots_model.columns_visible
        self.edit_columns_dialog.update_columns(column_names, columns_visible)

    def on_add_shot_files_clicked(self):
        shot_files = QtGui.QFileDialog.getOpenFileNames(self.ui,
                                                        'Select shot files',
                                                        self.last_opened_shots_folder,
                                                        "HDF5 files (*.h5)")
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

    def set_columns_visible(self, columns_visible):
        self.shots_model.set_columns_visible(columns_visible)

    def incoming_buffer_loop(self):
        """We use a queue as a buffer for incoming shots. We don't want to hang and not
        respond to a client submitting shots, so we just let shots pile up here until we can get to them.
        The downside to this is that we can't return errors to the cleint if the shot cannot be added,
        but the suggested workflow is to handle errors here anyway. A client running shots shouldn't stop
        the experiment on account of errors from the analyis stage, so what's the point of passing errors to it?
        We'll just raise errors here and the user can decide what to do with them."""
        logger = logging.getLogger('LYSE.FileBox.incoming')
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        while True:
            filepath = self.incoming_queue.get()
            if not isinstance(filepath, unicode) or isinstance(filepath, str):
                raise AssertionError(str(type(filepath)) + ' is not str or unicode')
            self.logger.info('adding %s' % filepath)
            inmain(self.shots_model.add_file, filepath)

        # Let analysis thread know to check for new files:
        with self.new_shots:
            self.new_shots.set()


class Lyse(object):

    def __init__(self):
        loader = UiLoader()
        self.ui = loader.load('main.ui', LyseMainWindow())

        self.connect_signals()

        self.setup_config()
        self.port = int(self.exp_config.get('ports', 'lyse'))

        # The singleshot routinebox will be connected to the filebox
        # by queues:
        to_singleshot = Queue.Queue()
        from_singleshot = Queue.Queue()

        # So will the multishot routinebox:
        to_multishot = Queue.Queue()
        from_multishot = Queue.Queue()

        self.output_box = OutputBox(self.ui.verticalLayout_output_box)
        self.singleshot_routinebox = RoutineBox(self.ui.verticalLayout_singleshot_routinebox, self.exp_config,
                                                self, to_singleshot, from_singleshot, self.output_box.port)
        self.multishot_routinebox = RoutineBox(self.ui.verticalLayout_multishot_routinebox, self.exp_config,
                                               self, to_multishot, from_multishot, self.output_box.port, multishot=True)
        self.filebox = FileBox(self.ui.verticalLayout_filebox, self.exp_config,
                               to_singleshot, from_singleshot, to_multishot, from_multishot)

        self.ui.resize(1600, 900)

        # Set the splitters to appropriate fractions of their maximum size:
        self.ui.splitter_horizontal.setSizes([1000, 600])
        self.ui.splitter_vertical.setSizes([300, 600])
        self.ui.show()
        # self.ui.showMaximized()

    def setup_config(self):
        config_path = os.path.join(config_prefix, '%s.ini' % socket.gethostname())
        required_config_params = {"DEFAULT": ["experiment_name"],
                                  "programs": ["text_editor",
                                               "text_editor_arguments",
                                               "hdf5_viewer",
                                               "hdf5_viewer_arguments"],
                                  "paths": ["shared_drive",
                                            "experiment_shot_storage",
                                            "analysislib"],
                                  "ports": ["lyse"]
                                  }
        self.exp_config = LabConfig(config_path, required_config_params)

    def connect_signals(self):
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)

    def destroy(self, *args):
        raise NotImplementedError
        # gtk.main_quit()
        # The routine boxes have subprocesses that need to be quit:
        # self.singleshot_routinebox.destroy()
        # self.multishot_routinebox.destroy()

    # TESTING ONLY REMOVE IN PRODUCTION
    def submit_dummy_shots(self):
        for i in range(12):
            path = os.path.abspath(os.path.join('test_shots', '20141021T135341_connectiontable_%02d.h5' % i))
            print(path)
            print(zprocess.zmq_get(self.port, data={'filepath': path}))


if __name__ == "__main__":
    logger = setup_logging('lyse')
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    qapplication = QtGui.QApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)
    app = Lyse()
    # Start the web server:
    server = WebServer(app.port)

    # TEST
    app.submit_dummy_shots()

    # Let the interpreter run every 500ms so it sees Ctrl-C interrupts:
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())
    # Do not run qapplication.exec_() whilst waiting for keyboard input if
    # we hop into interactive mode.
    QtCore.pyqtRemoveInputHook()

    qapplication.exec_()
    server.shutdown()
