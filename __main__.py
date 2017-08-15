from __future__ import division, unicode_literals, print_function, absolute_import  # Ease the transition to Python 3

# stdlib imports

import os
import sys
import socket
import Queue
import logging
import threading
import signal
import subprocess
import time


# Turn on our error catching for all subsequent imports
import labscript_utils.excepthook


# 3rd party imports:

import numpy as np
import labscript_utils.h5_lock
import h5py
import pandas

try:
    from labscript_utils import check_version
except ImportError:
    raise ImportError('Require labscript_utils > 2.1.0')

check_version('labscript_utils', '2.1', '3.0')
check_version('qtutils', '2.0.0', '3.0.0')
check_version('zprocess', '1.1.7', '3.0')

import zprocess.locking
from zprocess import ZMQServer

from labscript_utils.labconfig import LabConfig, config_prefix
from labscript_utils.setup_logging import setup_logging
from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets
import labscript_utils.shared_drive as shared_drive

from lyse.dataframe_utilities import (concat_with_padding,
                                      get_dataframe_from_shot,
                                      replace_with_padding)

from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils import inmain_decorator, UiLoader, DisconnectContextManager
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
    QtWidgets.QMessageBox.warning(app.ui, 'lyse', message)


@inmain_decorator()
def question_dialog(message):
    reply = QtWidgets.QMessageBox.question(app.ui, 'lyse', message,
                                       QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
    return (reply == QtWidgets.QMessageBox.Yes)


def scientific_notation(x, sigfigs=4, mode='eng'):
    """Returns a unicode string of the float f in scientific notation"""

    times = u'\u00d7'
    thinspace = u'\u2009'
    hairspace = u'\u200a'
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

    prefixes = {
        -24: u"y",
        -21: u"z",
        -18: u"a",
        -15: u"f",
        -12: u"p",
        -9: u"n",
        -6: u"\u03bc",
        -3: u"m",
        0: u"",
        3: u"k",
        6: u"M",
        9: u"G",
        12: u"T",
        15: u"P",
        18: u"E",
        21: u"Z",
        24: u"Y"
    }

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
        if mode == 'exponential':
            superscript = ''.join(sups.get(char, char) for char in str(exponent))
            result += thinspace + times + thinspace + '10' + superscript
        elif mode == 'eng':
            try:
                # If our number has an SI prefix then use it
                prefix = prefixes[exponent]
                result += hairspace + prefix
            except KeyError:
                # Otherwise display in scientific notation
                superscript = ''.join(sups.get(char, char) for char in str(exponent))
                result += thinspace + times + thinspace + '10' + superscript
    return result


class WebServer(ZMQServer):

    def handler(self, request_data):
        logger.info('WebServer request: %s' % str(request_data))
        if request_data == 'hello':
            return 'hello'
        elif request_data == 'get dataframe':
            # convert_objects() picks fixed datatypes for columns that are
            # compatible with fixed datatypes, dramatically speeding up
            # pickling. But we don't impose fixed datatypes earlier than now
            # because the user is free to use mixed datatypes in a column, and
            # we won't want to prevent values of a different type being added
            # in the future. All kwargs False because we don't want to coerce
            # strings to numbers or anything - just choose the correct
            # datatype for columns that are already a single datatype:
            return app.filebox.shots_model.dataframe.convert_objects(
                       convert_dates=False, convert_numeric=False, convert_timedeltas=False)
        elif isinstance(request_data, dict):
            if 'filepath' in request_data:
                h5_filepath = shared_drive.path_to_local(request_data['filepath'])
                if not (isinstance(h5_filepath, unicode) or isinstance(h5_filepath, str)):
                    raise AssertionError(str(type(h5_filepath)) + ' is not str or unicode')
                app.filebox.incoming_queue.put(h5_filepath)
                return 'added successfully'
        return ("error: operation not supported. Recognised requests are:\n "
                "'get dataframe'\n 'hello'\n {'filepath': <some_h5_filepath>}")


class LyseMainWindow(QtWidgets.QMainWindow):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)

    def event(self, event):
        result = QtWidgets.QMainWindow.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result

        
class AnalysisRoutine(object):

    def __init__(self, filepath, model, output_box_port):
        self.filepath = filepath
        self.shortname = os.path.basename(self.filepath)
        self.model = model
        self.output_box_port = output_box_port
        
        self.COL_ACTIVE = RoutineBox.COL_ACTIVE
        self.COL_STATUS = RoutineBox.COL_STATUS
        self.COL_NAME = RoutineBox.COL_NAME
        self.ROLE_FULLPATH = RoutineBox.ROLE_FULLPATH
        
        self.error = False
        self.done = False
        
        self.to_worker, self.from_worker, self.worker = self.start_worker()
        
        # Make a row to put into the model:
        active_item =  QtGui.QStandardItem()
        active_item.setCheckable(True)
        active_item.setCheckState(QtCore.Qt.Checked)
        info_item = QtGui.QStandardItem()
        name_item = QtGui.QStandardItem(self.shortname)
        name_item.setToolTip(self.filepath)
        name_item.setData(self.filepath, self.ROLE_FULLPATH)
        self.model.appendRow([active_item, info_item, name_item])
            
        self.exiting = False
        
    def start_worker(self):
        # Start a worker process for this analysis routine:
        child_handles = zprocess.subprocess_with_queues('analysis_subprocess.py', self.output_box_port)
        to_worker, from_worker, worker = child_handles
        # Tell the worker what script it with be executing:
        to_worker.put(self.filepath)
        return to_worker, from_worker, worker
        
    def do_analysis(self, filepath):
        self.to_worker.put(['analyse', filepath])
        signal, data = self.from_worker.get()
        if signal == 'error':
            return False, data
        elif signal == 'done':
            return True, data
        else:
            raise ValueError('invalid signal %s'%str(signal))
        
    @inmain_decorator()
    def set_status(self, status):
        index = self.get_row_index()
        if index is None:
            # Yelp, we've just been deleted. Nothing to do here.
            return
        status_item = self.model.item(index, self.COL_STATUS)
        if status == 'done':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/tick'))
            self.done = True
            self.error = False
        elif status == 'working':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/hourglass'))
            self.done = False
            self.error = False
        elif status == 'error':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/exclamation'))
            self.error = True
            self.done = False
        elif status == 'clear':
            status_item.setData(None, QtCore.Qt.DecorationRole)
            self.done = False
            self.error = False
        else:
            raise ValueError(status)
        
    @inmain_decorator()
    def enabled(self):
        index = self.get_row_index()
        if index is None:
            # Yelp, we've just been deleted.
            return False
        enabled_item = self.model.item(index, self.COL_ACTIVE)
        return (enabled_item.checkState() == QtCore.Qt.Checked)
        
    def get_row_index(self):
        """Returns the row index for this routine's row in the model"""
        for row in range(self.model.rowCount()):
            name_item = self.model.item(row, self.COL_NAME)
            fullpath = name_item.data(self.ROLE_FULLPATH)
            if fullpath == self.filepath:
                return row

    def restart(self):
        # TODO set status to 'restarting' or an icon or something, and gray out the item?
        self.end_child(restart=True)
        
    def remove(self):
        """End the child process and remove from the treeview"""
        self.end_child()
        index = self.get_row_index()
        if index is None:
            # Already gone
            return
        self.model.removeRow(index)
         
    def end_child(self, restart=False):
        self.to_worker.put(['quit',None])
        timeout_time = time.time() + 2
        self.exiting = True
        QtCore.QTimer.singleShot(50,
            lambda: self.check_child_exited(self.worker, timeout_time, kill=False, restart=restart))

    def check_child_exited(self, worker, timeout_time, kill=False, restart=False):
        worker.poll()
        if worker.returncode is None and time.time() < timeout_time:
            QtCore.QTimer.singleShot(50,
                lambda: self.check_child_exited(worker, timeout_time, kill, restart))
            return
        elif worker.returncode is None:
            if not kill:
                worker.terminate()
                app.output_box.output('%s worker not responding.\n'%self.shortname)
                timeout_time = time.time() + 2
                QtCore.QTimer.singleShot(50,
                    lambda: self.check_child_exited(worker, timeout_time, kill=True, restart=restart))
                return
            else:
                worker.kill()
                app.output_box.output('%s worker killed\n'%self.shortname, red=True)
        elif kill:
            app.output_box.output('%s worker terminated\n'%self.shortname, red=True)
        else:
            app.output_box.output('%s worker exited cleanly\n'%self.shortname)
        
        if restart:
            self.to_worker, self.from_worker, self.worker = self.start_worker()
            app.output_box.output('%s worker restarted\n'%self.shortname)
        self.exiting = False


class TreeView(QtWidgets.QTreeView):
    leftClicked = Signal(QtCore.QModelIndex)
    doubleLeftClicked = Signal(QtCore.QModelIndex)
    """A QTreeView that emits a custom signal leftClicked(index) after a left
    click on a valid index, and doubleLeftClicked(index) (in addition) on
    double click."""

    def __init__(self, *args):
        QtWidgets.QTreeView.__init__(self, *args)
        self._pressed_index = None
        self._double_click = False

    def mousePressEvent(self, event):
        result = QtWidgets.QTreeView.mousePressEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
        return result

    def leaveEvent(self, event):
        result = QtWidgets.QTreeView.leaveEvent(self, event)
        self._pressed_index = None
        self._double_click = False
        return result

    def mouseDoubleClickEvent(self, event):
        # Ensure our left click event occurs regardless of whether it is the
        # second click in a double click or not
        result = QtWidgets.QTreeView.mouseDoubleClickEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
            self._double_click = True
        return result

    def mouseReleaseEvent(self, event):
        result = QtWidgets.QTreeView.mouseReleaseEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid() and index == self._pressed_index:
            self.leftClicked.emit(index)
            if self._double_click:
                self.doubleLeftClicked.emit(index)
        self._pressed_index = None
        self._double_click = False
        return result

        
class RoutineBox(object):
    
    COL_ACTIVE = 0
    COL_STATUS = 1
    COL_NAME = 2
    ROLE_FULLPATH = QtCore.Qt.UserRole + 1
    # This data (stored in the name item) does not necessarily match
    # the position in the model. It will be set just
    # prior to sort() being called with this role as the sort data.
    # This is how we will reorder the model's rows instead of
    # using remove/insert.
    ROLE_SORTINDEX = QtCore.Qt.UserRole + 2
    
    def __init__(self, container, exp_config, filebox, from_filebox, to_filebox, output_box_port, multishot=False):
        self.multishot = multishot
        self.filebox = filebox
        self.exp_config = exp_config
        self.from_filebox = from_filebox
        self.to_filebox = to_filebox
        self.output_box_port = output_box_port
        
        self.logger = logging.getLogger('lyse.RoutineBox.%s'%('multishot' if multishot else 'singleshot'))  
        
        loader = UiLoader()
        loader.registerCustomWidget(TreeView)
        self.ui = loader.load('routinebox.ui')
        container.addWidget(self.ui)

        if multishot:
            self.ui.groupBox.setTitle('Multishot routines')
        else:
            self.ui.groupBox.setTitle('Singleshot routines')

        self.model = UneditableModel()
        self.header = HorizontalHeaderViewWithWidgets(self.model)
        self.ui.treeView.setHeader(self.header)
        self.ui.treeView.setModel(self.model)
        
        active_item = QtGui.QStandardItem()
        active_item.setToolTip('Whether the analysis routine should run')
        status_item = QtGui.QStandardItem()
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/information'))
        status_item.setToolTip('The status of this analyis routine\'s execution')
        name_item = QtGui.QStandardItem('name')
        name_item.setToolTip('The name of the python script for the analysis routine')

        self.select_all_checkbox = QtWidgets.QCheckBox()
        self.select_all_checkbox.setToolTip('whether the analysis routine should run')
        self.header.setWidget(self.COL_ACTIVE, self.select_all_checkbox)
        self.header.setStretchLastSection(True)
        self.select_all_checkbox.setTristate(False)
        
        self.model.setHorizontalHeaderItem(self.COL_ACTIVE, active_item)
        self.model.setHorizontalHeaderItem(self.COL_STATUS, status_item)
        self.model.setHorizontalHeaderItem(self.COL_NAME, name_item)
        self.model.setSortRole(self.ROLE_SORTINDEX)
        
        self.ui.treeView.resizeColumnToContents(self.COL_ACTIVE)
        self.ui.treeView.resizeColumnToContents(self.COL_STATUS)
        self.ui.treeView.setColumnWidth(self.COL_NAME, 200)
        
        self.ui.treeView.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        # Make the actions for the context menu:
        self.action_set_selected_active = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box'), 'set selected routines active',  self.ui)
        self.action_set_selected_inactive = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/ui-check-box-uncheck'), 'set selected routines inactive',  self.ui)
        self.action_restart_selected = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/arrow-circle'), 'restart worker process for selected routines',  self.ui)
        self.action_remove_selected = QtWidgets.QAction(
            QtGui.QIcon(':qtutils/fugue/minus'), 'Remove selected routines',  self.ui)
        self.last_opened_routine_folder = self.exp_config.get('paths', 'analysislib')
        
        self.routines = []
        
        self.connect_signals()

        self.analysis = threading.Thread(target = self.analysis_loop)
        self.analysis.daemon = True
        self.analysis.start()
        
    def connect_signals(self):
        self.ui.toolButton_add_routines.clicked.connect(self.on_add_routines_clicked)
        self.ui.toolButton_remove_routines.clicked.connect(self.on_remove_selection)
        self.model.itemChanged.connect(self.on_model_item_changed)
        self.ui.treeView.doubleLeftClicked.connect(self.on_treeview_double_left_clicked)
        # A context manager with which we can temporarily disconnect the above connection.
        self.model_item_changed_disconnected = DisconnectContextManager(
            self.model.itemChanged, self.on_model_item_changed)
        self.select_all_checkbox.stateChanged.connect(self.on_select_all_state_changed)
        self.select_all_checkbox_state_changed_disconnected = DisconnectContextManager(
            self.select_all_checkbox.stateChanged, self.on_select_all_state_changed)
        self.ui.treeView.customContextMenuRequested.connect(self.on_treeView_context_menu_requested)
        self.action_set_selected_active.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Checked))
        self.action_set_selected_inactive.triggered.connect(
            lambda: self.on_set_selected_triggered(QtCore.Qt.Unchecked))
        self.action_restart_selected.triggered.connect(self.on_restart_selected_triggered)
        self.action_remove_selected.triggered.connect(self.on_remove_selection)
        self.ui.toolButton_move_to_top.clicked.connect(self.on_move_to_top_clicked)
        self.ui.toolButton_move_up.clicked.connect(self.on_move_up_clicked)
        self.ui.toolButton_move_down.clicked.connect(self.on_move_down_clicked)
        self.ui.toolButton_move_to_bottom.clicked.connect(self.on_move_to_bottom_clicked)

    def on_add_routines_clicked(self):
        routine_files = QtWidgets.QFileDialog.getOpenFileNames(self.ui,
                                                           'Select analysis routines',
                                                           self.last_opened_routine_folder,
                                                           "Python scripts (*.py)")
        if type(routine_files) is tuple:
            routine_files, _ = routine_files

        if not routine_files:
            # User cancelled selection
            return
        # Convert to standard platform specific path, otherwise Qt likes forward slashes:
        routine_files = [os.path.abspath(routine_file) for routine_file in routine_files]

        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_routine_folder = os.path.dirname(routine_files[0])
        # Queue the files to be opened:
        for filepath in routine_files:
            if filepath in [routine.filepath for routine in self.routines]:
                app.output_box.output('Warning: Ignoring duplicate analysis routine %s\n'%filepath, red=True)
                continue
            routine = AnalysisRoutine(filepath, self.model, self.output_box_port)
            self.routines.append(routine)
        self.update_select_all_checkstate()
        
    def on_treeview_double_left_clicked(self, index):
        # If double clicking on the the name item, open
        # the routine in the specified text editor:
        if index.column() != self.COL_NAME:
            return
        name_item = self.model.item(index.row(), self.COL_NAME)
        routine_filepath = name_item.data(self.ROLE_FULLPATH)
        # get path to text editor
        editor_path = self.exp_config.get('programs', 'text_editor')
        editor_args = self.exp_config.get('programs', 'text_editor_arguments')
        # Get the current labscript file:
        if not editor_path:
            error_dialog("No editor specified in the labconfig.")
        if '{file}' in editor_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            editor_args = [arg if arg != '{file}' else routine_filepath for arg in editor_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            editor_args = [routine_filepath] + editor_args.split()
        try:
            subprocess.Popen([editor_path] + editor_args)
        except Exception as e:
            error_dialog("Unable to launch text editor specified in %s. Error was: %s" %
                         (self.exp_config.config_path, str(e)))
                         
    def on_remove_selection(self):
        self.remove_selection()

    def remove_selection(self, confirm=True):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        if not selected_rows:
            return
        if confirm and not question_dialog("Remove %d routines?" % len(selected_rows)):
            return
        name_items = [self.model.item(row, self.COL_NAME) for row in selected_rows]
        filepaths = [item.data(self.ROLE_FULLPATH) for item in name_items]
        for routine in self.routines[:]:
            if routine.filepath in filepaths:
                routine.remove()
                self.routines.remove(routine)
        self.update_select_all_checkstate()
        
    def on_model_item_changed(self, item):
        if item.column() == self.COL_ACTIVE:
            self.update_select_all_checkstate()
        
    def on_select_all_state_changed(self, state):
        with self.select_all_checkbox_state_changed_disconnected:
            # Do not allow a switch *to* a partially checked state:
            self.select_all_checkbox.setTristate(False)
        state = self.select_all_checkbox.checkState()
        with self.model_item_changed_disconnected:
            for row in range(self.model.rowCount()):
                active_item = self.model.item(row, self.COL_ACTIVE)
                active_item.setCheckState(state)
        
    def on_treeView_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui.treeView)
        menu.addAction(self.action_set_selected_active)
        menu.addAction(self.action_set_selected_inactive)
        menu.addAction(self.action_restart_selected)
        menu.addAction(self.action_remove_selected)
        menu.exec_(QtGui.QCursor.pos())
        
    def on_set_selected_triggered(self, active):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        for row in selected_rows:
            active_item = self.model.item(row, self.COL_ACTIVE)
            active_item.setCheckState(active)
        self.update_select_all_checkstate()

    def on_move_to_top_clicked(self):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        n = self.model.rowCount()
        i_selected = 0
        i_unselected = len(selected_rows)
        order = []
        for i in range(n):
            if i in selected_rows:
                order.append(i_selected)
                i_selected += 1
            else:
                order.append(i_unselected)
                i_unselected += 1
        self.reorder(order)
        
    def on_move_up_clicked(self):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        n = self.model.rowCount()
        order = []
        last_unselected_index = None
        for i in range(n):
            if i in selected_rows:
                if last_unselected_index is None:
                    order.append(i)
                else:
                    order.append(i - 1)
                    order[last_unselected_index] += 1
            else:
                last_unselected_index = i
                order.append(i)
        self.reorder(order)
        
    def on_move_down_clicked(self):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        n = self.model.rowCount()
        order = []
        last_unselected_index = None
        for i in reversed(range(n)):
            if i in selected_rows:
                if last_unselected_index is None:
                    order.insert(0, i)
                else:
                    order.insert(0, i + 1)
                    order[last_unselected_index - n] -= 1
            else:
                last_unselected_index = i
                order.insert(0, i)
        self.reorder(order)
        
    def on_move_to_bottom_clicked(self):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        n = self.model.rowCount()
        i_selected = n - len(selected_rows)
        i_unselected = 0
        order = []
        for i in range(n):
            if i in selected_rows:
                order.append(i_selected)
                i_selected += 1
            else:
                order.append(i_unselected)
                i_unselected += 1
        self.reorder(order)
        
    def on_restart_selected_triggered(self):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        name_items = [self.model.item(row, self.COL_NAME) for row in selected_rows]
        filepaths = [item.data(self.ROLE_FULLPATH) for item in name_items]
        for routine in self.routines:
            if routine.filepath in filepaths:
                routine.restart()
        self.update_select_all_checkstate()
       
    def analysis_loop(self):
        while True:
            filepath = self.from_filebox.get()
            if self.multishot:
                assert filepath is None
                # TODO: get the filepath of the output h5 file: 
                # filepath = self.filechooserentry.get_text()
            self.logger.info('got a file to process: %s'%filepath)
            self.do_analysis(filepath)
    
    def todo(self):
        """How many analysis routines are not done?"""
        return len([r for r in self.routines if r.enabled() and not r.done])
        
    def do_analysis(self, filepath):
        """Run all analysis routines once on the given filepath,
        which is a shot file if we are a singleshot routine box"""
        for routine in self.routines:
            routine.set_status('clear')
        remaining = self.todo()
        error = False
        updated_data = {}
        while remaining:
            self.logger.debug('%d routines left to do'%remaining)
            for routine in self.routines:
                if routine.enabled() and not routine.done:
                    break
            else:
                routine = None
            if routine is not None:
                self.logger.info('running analysis routine %s'%routine.shortname)
                routine.set_status('working')
                success, updated_data = routine.do_analysis(filepath)
                if success:
                    routine.set_status('done')
                    self.logger.debug('success')
                else:
                    routine.set_status('error')
                    self.logger.debug('failure')
                    error = True
                    break
            # Race conditions here, but it's only for reporting percent done
            # so it doesn't matter if it's wrong briefly:
            remaining = self.todo()
            total = len([r for r in self.routines if r.enabled()])
            done = total - remaining
            try:
                status_percent = 100*float(done)/(remaining + done)
            except ZeroDivisionError:
                # All routines got deleted mid-analysis, we're done here:
                status_percent = 100.0
            self.to_filebox.put(['progress', status_percent, updated_data])
        if error:
            self.to_filebox.put(['error', None, updated_data])
        else:
            self.to_filebox.put(['done', 100.0, {}])
        self.logger.debug('completed analysis of %s'%filepath)
            
    def reorder(self, order):
        assert len(order) == len(set(order)), 'ordering contains non-unique elements'
        # Apply the reordering to the liststore:
        for old_index, new_index in enumerate(order):
            name_item = self.model.item(old_index, self.COL_NAME)
            name_item.setData(new_index, self.ROLE_SORTINDEX)
        self.ui.treeView.sortByColumn(self.COL_NAME, QtCore.Qt.AscendingOrder)
        # Apply new order to our list of routines too:
        self.routines = [self.routines[order.index(i)] for i in range(len(order))]

    def update_select_all_checkstate(self):
        with self.select_all_checkbox_state_changed_disconnected:
            all_states = []
            for row in range(self.model.rowCount()):
                active_item = self.model.item(row, self.COL_ACTIVE)
                all_states.append(active_item.checkState())
            if all(state == QtCore.Qt.Checked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Checked)
            elif all(state == QtCore.Qt.Unchecked for state in all_states):
                self.select_all_checkbox.setCheckState(QtCore.Qt.Unchecked)
            else:
                self.select_all_checkbox.setCheckState(QtCore.Qt.PartiallyChecked)
                
   # TESTING ONLY REMOVE IN PRODUCTION
    def queue_dummy_routines(self):
        folder = os.path.abspath('test_routines')
        for filepath in ['hello.py', 'test.py']:
            routine = AnalysisRoutine(os.path.join(folder, filepath), self.model, self.output_box_port)
            self.routines.append(routine)
        self.update_select_all_checkstate()


class EditColumnsDialog(QtWidgets.QDialog):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)
    close_signal = Signal()

    def __init__(self):
        QtWidgets.QDialog.__init__(self, None, QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowTitleHint)

    def event(self, event):
        result = QtWidgets.QDialog.event(self, event)
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
        self.ui.treeView.resizeColumnToContents(self.COL_VISIBLE)
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


class ItemDelegate(QtWidgets.QStyledItemDelegate):

    """An item delegate with a fixed height and a progress bar in one column"""
    EXTRA_ROW_HEIGHT = 2

    def __init__(self, view, model, col_status, role_status_percent):
        self.view = view
        self.model = model
        self.COL_STATUS = col_status
        self.ROLE_STATUS_PERCENT = role_status_percent
        QtWidgets.QStyledItemDelegate.__init__(self)

    def sizeHint(self, *args):
        fontmetrics = QtGui.QFontMetrics(self.view.font())
        text_height = fontmetrics.height()
        row_height = text_height + self.EXTRA_ROW_HEIGHT
        size = QtWidgets.QStyledItemDelegate.sizeHint(self, *args)
        return QtCore.QSize(size.width(), row_height)

    def paint(self, painter, option, index):
        if index.column() == self.COL_STATUS:
            status_percent = self.model.data(index, self.ROLE_STATUS_PERCENT)
            if status_percent == 100:
                # Render as a normal item - this shows whatever icon is set instead of a progress bar.
                return QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
            else:
                # Method of rendering a progress bar into the view copied from
                # Qt's 'network-torrent' example:
                # http://qt-project.org/doc/qt-4.8/network-torrent-torrentclient-cpp.html

                # Set up a QStyleOptionProgressBar to precisely mimic the
                # environment of a progress bar.
                progress_bar_option = QtWidgets.QStyleOptionProgressBar()
                progress_bar_option.state = QtWidgets.QStyle.State_Enabled
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
                qapplication.style().drawControl(QtWidgets.QStyle.CE_ProgressBar, progress_bar_option, painter)
        else:
            return QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)


class UneditableModel(QtGui.QStandardItemModel):

    def flags(self, index):
        """Return flags as normal except that the ItemIsEditable
        flag is always False"""
        result = QtGui.QStandardItemModel.flags(self, index)
        return result & ~QtCore.Qt.ItemIsEditable


class TableView(QtWidgets.QTableView):
    leftClicked = Signal(QtCore.QModelIndex)
    doubleLeftClicked = Signal(QtCore.QModelIndex)
    """A QTableView that emits a custom signal leftClicked(index) after a left
    click on a valid index, and doubleLeftClicked(index) (in addition) on
    double click. Multiple inheritance of QObjects is not possible, so we
    are forced to duplicate code instead of sharing code with the extremely
    similar TreeView class in this module"""

    def __init__(self, *args):
        QtWidgets.QTableView.__init__(self, *args)
        self._pressed_index = None
        self._double_click = False

    def mousePressEvent(self, event):
        result = QtWidgets.QTableView.mousePressEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
        return result

    def leaveEvent(self, event):
        result = QtWidgets.QTableView.leaveEvent(self, event)
        self._pressed_index = None
        self._double_click = False
        return result

    def mouseDoubleClickEvent(self, event):
        # Ensure our left click event occurs regardless of whether it is the
        # second click in a double click or not
        result = QtWidgets.QTableView.mouseDoubleClickEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
            self._double_click = True
        return result

    def mouseReleaseEvent(self, event):
        result = QtWidgets.QTableView.mouseReleaseEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid() and index == self._pressed_index:
            self.leftClicked.emit(index)
            if self._double_click:
                self.doubleLeftClicked.emit(index)
        self._pressed_index = None
        self._double_click = False
        return result
        
        
class DataFrameModel(QtCore.QObject):

    COL_STATUS = 0
    COL_FILEPATH = 1

    ROLE_STATUS_PERCENT = QtCore.Qt.UserRole + 1
    ROLE_DELETED_OFF_DISK = QtCore.Qt.UserRole + 2
    
    columns_changed = Signal()

    def __init__(self, view, exp_config):
        QtCore.QObject.__init__(self)
        self._view = view
        self.exp_config = exp_config
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
        self._vertheader = QtWidgets.QHeaderView(QtCore.Qt.Vertical)
        self._vertheader.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self._vertheader.setStyleSheet(headerview_style)
        self._header.setStyleSheet(headerview_style)
        self._vertheader.setHighlightSections(True)
        self._vertheader.setSectionsClickable(True)
        self._view.setModel(self._model)
        self._view.setHorizontalHeader(self._header)
        self._view.setVerticalHeader(self._vertheader)
        self._delegate = ItemDelegate(self._view, self._model, self.COL_STATUS, self.ROLE_STATUS_PERCENT)
        self._view.setItemDelegate(self._delegate)
        self._view.setSelectionBehavior(QtWidgets.QTableView.SelectRows)
        self._view.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)

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
        self.remove_selection()

    def remove_selection(self, confirm=True):
        selection_model = self._view.selectionModel()
        selected_indexes = selection_model.selectedRows()
        selected_name_items = [self._model.itemFromIndex(index) for index in selected_indexes]
        if not selected_name_items:
            return
        if confirm and not question_dialog("Remove %d shots?" % len(selected_name_items)):
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
            error_dialog("No hdf5 viewer specified in the labconfig.")
        if '{file}' in viewer_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            viewer_args = [arg if arg != '{file}' else shot_filepath for arg in viewer_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            viewer_args = [shot_filepath] + viewer_args.split()
        try:
            subprocess.Popen([viewer_path] + viewer_args)
        except Exception as e:
            error_dialog("Unable to launch hdf5 viewer specified in %s. Error was: %s" %
                         (self.exp_config.config_path, str(e)))
        
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

    @inmain_decorator()
    def mark_as_deleted_off_disk(self, filepath):
        # Confirm the shot hasn't been removed from lyse (we are in the main
        # thread so there is no race condition in checking first)
        if not filepath in self.dataframe['filepath'].values:
            # Shot has been removed from FileBox, nothing to do here:
            return

        model_row_number = self.get_model_row_by_filepath(filepath)
        status_item = self._model.item(model_row_number, self.COL_STATUS)
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
        app.output_box.output('Warning: Shot deleted from disk or no longer readable %s\n' % filepath, red=True)

    @inmain_decorator()
    def update_row(self, filepath, dataframe_already_updated=False, status_percent=None, new_row_data=None, updated_row_data=None):
        """"Updates a row in the dataframe and Qt model
        to the data in the HDF5 file for that shot. Also sets the percent done, if specified"""
        # Update the row in the dataframe first:
        if (new_row_data is None) == (updated_row_data is None) and not dataframe_already_updated:
            raise ValueError('Exactly one of new_row_data or updated_row_data must be provided')

        df_row_index = np.where(self.dataframe['filepath'].values == filepath)
        try:
            df_row_index = df_row_index[0][0]
        except IndexError:
            # Row has been deleted, nothing to do here:
            return

        if updated_row_data is not None and not dataframe_already_updated:
            for group, name in updated_row_data:
                self.dataframe.loc[df_row_index, (group, name) + ('',) * (self.nlevels - 2)] = updated_row_data[group, name]
            dataframe_already_updated = True

        if not dataframe_already_updated:
            if new_row_data is None:
                raise ValueError("If dataframe_already_updated is False, then new_row_data, as returned "
                                 "by dataframe_utils.get_dataframe_from_shot(filepath) must be provided.")
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
        if new_column_names:
            # Update the visibility state of new columns, in case some new columns are hidden:
            self.set_columns_visible

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
        model_row_number = self.get_model_row_by_filepath(filepath)
        dataframe_row = self.dataframe.iloc[df_row_index].to_dict()
        for column_number, column_name in self.column_names.items():
            if not isinstance(column_name, tuple):
                # One of our special columns, does not correspond to a column in the dataframe:
                continue
            if updated_row_data is not None and column_name not in updated_row_data:
                continue
            item = self._model.item(model_row_number, column_number)
            if item is None:
                # This is the first time we've written a value to this part of the model:
                item = QtGui.QStandardItem('NaN')
                item.setData(QtCore.Qt.AlignCenter, QtCore.Qt.TextAlignmentRole)
                self._model.setItem(model_row_number, column_number, item)
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

        for i, column_name in enumerate(sorted(new_column_names)):
            # Resize any new columns to fit contents:
            column_number = new_columns_start + i
            self._view.resizeColumnToContents(column_number)

        if status_percent is not None:
            status_item = self._model.item(model_row_number, self.COL_STATUS)
            status_item.setData(status_percent, self.ROLE_STATUS_PERCENT)
            
        if new_column_names or defunct_column_names:
            self.columns_changed.emit()

    def new_row(self, filepath):
        status_item = QtGui.QStandardItem()
        status_item.setData(0, self.ROLE_STATUS_PERCENT)
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/tick'))
        name_item = QtGui.QStandardItem(filepath)
        return [status_item, name_item]

    def renumber_rows(self):
        """Add/update row indices - the rows are numbered in simple sequential order
        for easy comparison with the dataframe"""
        n_digits = len(str(self._model.rowCount()))
        print(self._model.rowCount())
        for row_number in range(self._model.rowCount()):
            vertical_header_item = self._model.verticalHeaderItem(row_number)
            filepath_item = self._model.item(row_number, self.COL_FILEPATH)
            filepath = filepath_item.text()
            basename = os.path.splitext(os.path.basename(filepath))[0]
            row_number_str = str(row_number).rjust(n_digits)
            vert_header_text = '{}. | {}'.format(row_number_str, basename)
            vertical_header_item.setText(vert_header_text)
    
    @inmain_decorator()
    def add_files(self, filepaths, new_row_data):
        """Add files to the dataframe model. New_row_data should be a
        dataframe containing the new rows."""

        to_add = []

        # Check for duplicates:
        for filepath in filepaths:
            if filepath in self.dataframe['filepath'].values or filepath in to_add:
                app.output_box.output('Warning: Ignoring duplicate shot %s\n' % filepath, red=True)
                if new_row_data is not None:
                    df_row_index = np.where(new_row_data['filepath'].values == filepath)
                    new_row_data = new_row_data.drop(df_row_index[0])
                    new_row_data.index = pandas.Index(range(len(new_row_data)))
            else:
                to_add.append(filepath)

        assert len(new_row_data) == len(to_add)

        for filepath in to_add:
            # Add the new rows to the model:
            self._model.appendRow(self.new_row(filepath))
            vert_header_item = QtGui.QStandardItem('...loading...')
            self._model.setVerticalHeaderItem(self._model.rowCount() - 1, vert_header_item)
            self._view.resizeRowToContents(self._model.rowCount() - 1)

        if to_add:
            self.dataframe = concat_with_padding(self.dataframe, new_row_data)
            self.update_column_levels()
            for filepath in to_add:
                self.update_row(filepath, dataframe_already_updated=True)
            self.renumber_rows()

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

    def __init__(self, container, exp_config, to_singleshot, from_singleshot, to_multishot, from_multishot):

        self.exp_config = exp_config
        self.to_singleshot = to_singleshot
        self.to_multishot = to_multishot
        self.from_singleshot = from_singleshot
        self.from_multishot = from_multishot

        self.logger = logging.getLogger('lyse.FileBox')
        self.logger.info('starting')

        loader = UiLoader()
        loader.registerCustomWidget(TableView)
        self.ui = loader.load('filebox.ui')
        self.ui.progressBar_add_shots.hide()
        container.addWidget(self.ui)
        self.shots_model = DataFrameModel(self.ui.tableView, self.exp_config)
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
        self.incoming_queue = Queue.Queue()

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
    def set_add_shots_progress(self, completed, total):
        if completed == total:
            self.ui.progressBar_add_shots.hide()
        else:
            self.ui.progressBar_add_shots.setMaximum(total)
            self.ui.progressBar_add_shots.setValue(completed)
            if self.ui.progressBar_add_shots.isHidden():
                self.ui.progressBar_add_shots.show()

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
                while True:
                    try:
                        filepath = self.incoming_queue.get(False)
                    except Queue.Empty:
                        break
                    else:
                        filepaths.append(filepath)
                        if len(filepaths) >= 5:
                            break
                logger.info('adding:\n%s' % '\n'.join(filepaths))
                if n_shots_added == 0:
                    total_shots = self.incoming_queue.qsize() + len(filepaths)
                    self.set_add_shots_progress(1, total_shots)

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
                        app.output_box.output('Warning: Ignoring shot file not found or not readable %s\n' % filepath, red=True)
                        indices_of_files_not_found.append(i)
                    n_shots_added += 1
                    shots_remaining = self.incoming_queue.qsize()
                    total_shots = n_shots_added + shots_remaining + len(filepaths) - (i + 1)
                    if i != len(filepaths) - 1:
                        # Leave the last update until after dataframe concatenation.
                        # Looks more responsive that way:
                        self.set_add_shots_progress(n_shots_added, total_shots)
                if dataframes:
                    new_row_data = concat_with_padding(*dataframes)
                else:
                    new_row_data = None
                self.set_add_shots_progress(n_shots_added, total_shots)

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
            # Update the status percent for the the row on which analysis is actually running:
            self.shots_model.update_row(filepath, status_percent=status_percent, dataframe_already_updated=True)
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
        self.exp_config = LabConfig(required_params=required_config_params)

    def connect_signals(self):
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)
    
    def on_keyPress(self, key, modifiers, is_autorepeat):
        # Keyboard shortcut to delete shots or routines depending on which
        # treeview/tableview has focus. Shift-delete to skip confirmation.
        if key == QtCore.Qt.Key_Delete and not is_autorepeat:
            confirm = modifiers != QtCore.Qt.ShiftModifier 
            if self.filebox.ui.tableView.hasFocus():
                self.filebox.shots_model.remove_selection(confirm)
            if self.singleshot_routinebox.ui.treeView.hasFocus():
                self.singleshot_routinebox.remove_selection(confirm)
            if self.multishot_routinebox.ui.treeView.hasFocus():
                self.multishot_routinebox.remove_selection(confirm)


class KeyPressQApplication(QtWidgets.QApplication):

    """A Qapplication that emits a signal keyPress(key) on keypresses"""
    keyPress = Signal(int, QtCore.Qt.KeyboardModifiers, bool)
    keyRelease = Signal(int, QtCore.Qt.KeyboardModifiers, bool)

    def notify(self, object, event):
        if event.type() == QtCore.QEvent.KeyPress and event.key():
            self.keyPress.emit(event.key(), event.modifiers(), event.isAutoRepeat())
        elif event.type() == QtCore.QEvent.KeyRelease and event.key():
            self.keyRelease.emit(event.key(), event.modifiers(), event.isAutoRepeat())
        return QtWidgets.QApplication.notify(self, object, event)


if __name__ == "__main__":
    logger = setup_logging('lyse')
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    qapplication = KeyPressQApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)
    app = Lyse()

    # Start the web server:
    server = WebServer(app.port)

    # Let the interpreter run every 500ms so it sees Ctrl-C interrupts:
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())
    # Do not run qapplication.exec_() whilst waiting for keyboard input if
    # we hop into interactive mode.
    QtCore.pyqtRemoveInputHook() # TODO remove once updating to pyqt 4.11 or whatever fixes that bug
    
    # Connect keyboard shortcuts:
    qapplication.keyPress.connect(app.on_keyPress)
    
    qapplication.exec_()
    server.shutdown()
