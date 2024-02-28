#####################################################################
#                                                                   #
# /__main__.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

import os
import logging
import threading
import subprocess

# Labscript imports
from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets

# qt imports
from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils import UiLoader, DisconnectContextManager

from lyse import LYSE_DIR
import lyse.utils

class UneditableModel(QtGui.QStandardItemModel):

    def flags(self, index):
        """Return flags as normal except that the ItemIsEditable
        flag is always False"""
        result = QtGui.QStandardItemModel.flags(self, index)
        return result & ~QtCore.Qt.ItemIsEditable

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
    
    def __init__(self, app, container, exp_config, filebox, from_filebox, to_filebox, output_box_port, multishot=False):
        self.app = app
        self.multishot = multishot
        self.filebox = filebox
        self.exp_config = exp_config
        self.from_filebox = from_filebox
        self.to_filebox = to_filebox
        self.output_box_port = output_box_port
        
        self.logger = logging.getLogger('lyse.RoutineBox.%s'%('multishot' if multishot else 'singleshot'))  
        
        loader = UiLoader()
        loader.registerCustomWidget(TreeView)
        self.ui = loader.load(os.path.join(LYSE_DIR, 'user_interface/routinebox.ui'))
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

        # Queue the files to be opened:
        for filepath, checked in routine_files:
            if filepath in [routine.filepath for routine in self.routines]:
                self.app.output_box.output('Warning: Ignoring duplicate analysis routine %s\n'%filepath, red=True)
                continue
            routine = lyse.analysis.AnalysisRoutine(self.app, filepath, self.model, self.output_box_port, checked)
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
            lyse.utils.error_dialog(self.app, "No editor specified in the labconfig.")
        if '{file}' in editor_args:
            # Split the args on spaces into a list, replacing {file} with the labscript file
            editor_args = [arg if arg != '{file}' else routine_filepath for arg in editor_args.split()]
        else:
            # Otherwise if {file} isn't already in there, append it to the other args:
            editor_args = [routine_filepath] + editor_args.split()
        try:
            subprocess.Popen([editor_path] + editor_args)
        except Exception as e:
            lyse.utils.error_dialog(self.app, "Unable to launch text editor specified in %s. Error was: %s" %
                         (self.exp_config.config_path, str(e)))
                         
    def on_remove_selection(self):
        self.remove_selection()

    def remove_selection(self, confirm=True):
        selected_indexes = self.ui.treeView.selectedIndexes()
        selected_rows = set(index.row() for index in selected_indexes)
        if not selected_rows:
            return
        if confirm and not lyse.utils.question_dialog(self.app, "Remove %d routines?" % len(selected_rows)):
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
