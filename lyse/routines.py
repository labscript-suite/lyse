#####################################################################
#                                                                   #
# /routines.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

"""
analysis GUI code
"""

import os
import sys
import time
import logging
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor

# Labscript imports
import zprocess
from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets

# qt imports
from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils import inmain_decorator, UiLoader, DisconnectContextManager

import lyse.widgets
import lyse.utils
import lyse.utils.gui

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
    
    def __init__(self, app, container, exp_config, filebox, from_filebox, to_filebox, output_box_port, multishot=False):
        self.app = app
        self.multishot = multishot
        self.filebox = filebox
        self.exp_config = exp_config
        self.from_filebox = from_filebox
        self.to_filebox = to_filebox
        self.output_box_port = output_box_port
        
        self.logger = logging.getLogger('lyse.RoutineBox.%s'%('multishot' if multishot else 'singleshot'))
        self.logger.info('starting')
        
        loader = UiLoader()
        loader.registerCustomWidget(lyse.widgets.TreeView)
        self.ui = loader.load(os.path.join(lyse.utils.LYSE_DIR, 'user_interface/routinebox.ui'))
        container.addWidget(self.ui)
        self.logger.info('UI loaded')

        if multishot:
            self.ui.groupBox.setTitle('Multishot routines')
        else:
            self.ui.groupBox.setTitle('Singleshot routines')

        self.model = QtGui.QStandardItemModel()
        self.header = HorizontalHeaderViewWithWidgets(self.model)
        self.ui.treeView.setHeader(self.header)
        self.ui.treeView.setModel(self.model)
        self.ui.treeView.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        
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

        self.logger.info('starting analysis loop')
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
        self.add_routines([(routine_file, QtCore.Qt.Checked) for routine_file in routine_files])

    def add_routines(self, routine_files, clear_existing=False):
        """Add routines to the routine box, where routine_files is a list of
        tuples containing the filepath and whether the routine is enabled or
        not when it is added. if clear_existing == True, then any existing
        analysis routines will be cleared before the new ones are added."""
        if clear_existing:
            for routine in self.routines[:]:
                routine.remove()
                self.routines.remove(routine)

        filepaths = []
        checkstates = {}
        for filepath, checked in routine_files:
            if filepath in [routine.filepath for routine in self.routines] or filepath in filepaths:
                self.app.output_box.output('Warning: Ignoring duplicate analysis routine %s\n'%filepath, red=True)
                continue
            filepaths.append(filepath)
            checkstates[filepath] = checked

        # Start the worker processes. The first child process of the process tree also
        # creates the broker and heartbeat server shared by all children, so the first
        # worker is started before the others, which are started concurrently.
        worker_handles = {}
        errors = {}

        def start(filepath):
            try:
                worker_handles[filepath] = start_worker(self.app.process_tree, filepath,
                                                        self.output_box_port)
            except Exception:
                errors[filepath] = sys.exc_info()

        if filepaths:
            start(filepaths[0])
        if len(filepaths) > 1:
            with ThreadPoolExecutor(max_workers=len(filepaths) - 1) as executor:
                for filepath in filepaths[1:]:
                    executor.submit(start, filepath)

        for filepath in filepaths:
            if filepath in errors:
                continue
            self.logger.info('adding routine for %s', filepath)
            routine = AnalysisRoutine(self.app, filepath, self.model, self.output_box_port,
                                      QtCore.Qt.CheckState(checkstates[filepath]),
                                      worker_handles=worker_handles[filepath])
            self.routines.append(routine)
        self.update_select_all_checkstate()

        for exc_info in errors.values():
            zprocess.raise_exception_in_thread(exc_info)
        
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
            lyse.utils.gui.error_dialog(self.app, "No editor specified in the labconfig.")
            return
        if '{file}' in editor_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            editor_args = [arg if arg != '{file}' else routine_filepath for arg in editor_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            editor_args = [routine_filepath] + editor_args.split()
        try:
            subprocess.Popen([editor_path] + editor_args)
        except Exception as e:
            lyse.utils.gui.error_dialog(self.app, "Unable to launch text editor specified in %s. Error was: %s" %
                         (self.exp_config.config_path, str(e)))
                         
    def on_remove_selection(self):
        self.remove_selection()

    def remove_selection(self, confirm=True):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        if not selected_rows:
            return
        if confirm and not lyse.utils.gui.question_dialog(self.app, "Remove %d routines?" % len(selected_rows)):
            return
        name_items = [self.model.item(row, self.COL_NAME) for row in selected_rows]
        filepaths = [item.data(self.ROLE_FULLPATH) for item in name_items]
        for routine in self.routines[:]:
            if routine.filepath in filepaths:
                routine.remove()
                self.routines.remove(routine)
                self.logger.info('removing routine for %s', routine.filepath)
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
        menu.exec(QtGui.QCursor.pos())
        
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
        
    def abort(self):
        """Interrupt the routine that is running, if any."""
        for routine in self.routines:
            routine.abort()

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
            self.logger.info('got a file to process: %s', filepath)
            self.do_analysis(filepath)
    
    def _start_next_routine(self):
        """Set the status of the first enabled routine that has not run to working, and
        return it. Return None if there is no such routine. Call from the main
        thread."""
        for routine in self.routines:
            if routine.enabled() and not routine.done:
                routine.set_status('working')
                return routine
        return None

    @inmain_decorator()
    def start_analysis(self):
        """Clear the status of every routine and start the first enabled one. Return
        it, or None if no routine is enabled."""
        for routine in self.routines:
            routine.set_status('clear')
        return self._start_next_routine()

    @inmain_decorator()
    def end_routine(self, routine, success):
        """Set the status of a routine that has run. If it succeeded, start the next
        enabled routine that has not run. Return the next routine, or None, and the
        percentage of enabled routines that are done."""
        if not success:
            routine.set_status('error')
            return None, None
        routine.set_status('done')
        enabled_routines = [r for r in self.routines if r.enabled()]
        if enabled_routines:
            n_done = len([r for r in enabled_routines if r.done])
            status_percent = 100 * n_done / len(enabled_routines)
        else:
            # All routines got deleted mid-analysis, we're done here:
            status_percent = 100.0
        return self._start_next_routine(), status_percent

    def do_analysis(self, filepath):
        """Run all analysis routines once on the given filepath,
        which is a shot file if we are a singleshot routine box"""
        error = False
        updated_data = {}
        routine = self.start_analysis()
        while routine is not None:
            self.logger.info('running analysis routine %s', routine.shortname)
            success, updated_data = routine.do_analysis(filepath)
            routine, status_percent = self.end_routine(routine, success)
            if not success:
                self.logger.debug('failure')
                error = True
                break
            self.logger.debug('success')
            self.to_filebox.put(['progress', status_percent, updated_data])
        if error:
            self.to_filebox.put(['error', None, updated_data])
        else:
            self.to_filebox.put(['done', 100.0, {}])
        self.logger.debug('completed analysis of %s', filepath)
            
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


def start_worker(process_tree, filepath, output_box_port):
    """Start a worker process for the analysis routine at filepath. Returns the queue to
    the worker, the queue from the worker and the worker's Popen object. Blocks until the
    worker has connected."""
    logger = logging.getLogger(f'lyse.AnalysisRoutine.{os.path.basename(filepath)}')
    logger.info('starting worker')
    worker_path = os.path.join(lyse.utils.LYSE_DIR, 'analysis_subprocess.py')
    to_worker, from_worker, worker = process_tree.subprocess(
        worker_path,
        output_redirection_port=output_box_port,
        startup_timeout=30,
    )
    # Tell the worker what script it will be executing:
    to_worker.put(filepath)
    logger.info('analysis_subprocess started')
    return to_worker, from_worker, worker


class AnalysisRoutine(object):

    # Duration in seconds for which abort() waits for the routine to stop before
    # restarting its worker.
    ABORT_TIMEOUT = 5

    def __init__(self, app, filepath, model, output_box_port, checked=QtCore.Qt.Checked,
                 worker_handles=None):
        """worker_handles is the return value of start_worker() for this routine. If it is
        None, the worker is started here."""
        self.app = app
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
        self.working = False
        # Incremented each time the routine starts running, so that abort() can tell
        # whether the run it interrupted is still the one running:
        self.run_number = 0

        self.logger = logging.getLogger(f'lyse.AnalysisRoutine.{self.shortname}')

        if worker_handles is None:
            worker_handles = self.start_worker()
        self.to_worker, self.from_worker, self.worker = worker_handles
        
        # Make a row to put into the model:
        active_item =  QtGui.QStandardItem()
        active_item.setCheckable(True)
        active_item.setCheckState(checked)
        info_item = QtGui.QStandardItem()
        name_item = QtGui.QStandardItem(self.shortname)
        name_item.setToolTip(self.filepath)
        name_item.setData(self.filepath, self.ROLE_FULLPATH)
        self.model.appendRow([active_item, info_item, name_item])
            
        self.exiting = False
        
    def start_worker(self):
        return start_worker(self.app.process_tree, self.filepath, self.output_box_port)
        
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
        self.working = status == 'working'
        if self.working:
            self.run_number += 1
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

    def abort(self):
        """Interrupt the routine if it is running. If it has not stopped after
        ABORT_TIMEOUT seconds, restart its worker."""
        if not self.working or self.exiting:
            return
        try:
            self.to_worker.put(['abort', None], timeout=1)
        except TimeoutError:
            # The worker is not receiving; the restart below replaces it.
            pass
        run_number = self.run_number
        QtCore.QTimer.singleShot(
            self.ABORT_TIMEOUT * 1000, lambda: self.check_aborted(run_number)
        )

    def check_aborted(self, run_number):
        if self.working and self.run_number == run_number and not self.exiting:
            self.app.output_box.output(
                '%s did not stop, restarting its worker\n' % self.shortname, red=True
            )
            self.restart()

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
        self.to_worker.put(['quit', None])
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
                self.app.output_box.output('%s worker not responding.\n'%self.shortname)
                timeout_time = time.time() + 2
                QtCore.QTimer.singleShot(50,
                    lambda: self.check_child_exited(worker, timeout_time, kill=True, restart=restart))
                return
            else:
                worker.kill()
                self.app.output_box.output('%s worker killed\n'%self.shortname, red=True)
        elif kill:
            self.app.output_box.output('%s worker terminated\n'%self.shortname, red=True)
        else:
            self.app.output_box.output('%s worker exited cleanly\n'%self.shortname)
        
        # if analysis was running notify analysisloop that analysis has failed
        self.from_worker.put(('error', {}))

        if restart:
            self.to_worker, self.from_worker, self.worker = self.start_worker()
            self.app.output_box.output('%s worker restarted\n'%self.shortname)
        self.exiting = False