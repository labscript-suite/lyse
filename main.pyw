#!/usr/bin/env python

import cgi
import logging, logging.handlers
import os
import cPickle as pickle
import Queue
import sys
import threading
import time
import traceback
import subprocess
from BaseHTTPServer import BaseHTTPRequestHandler, HTTPServer

import gtk
import gobject
import pango
import h5py
import numpy
import pandas
import excepthook
import shared_drive
from dataframe_utilities import (concat_with_padding, 
                                 get_dataframe_from_shot, 
                                 replace_with_padding)
                                 
from analysis_routine import (AnalysisRoutine, ENABLE, SHOW_PLOTS, ERROR,
                              MULTIPLE_PLOTS, INCONSISTENT, SUCCESS)

from subproc_utils.gtk_components import OutputBox
try:
    import analysislib
    analysislib_prefix = os.path.dirname(analysislib.__file__)
except Exception:
    analysislib_prefix = None
    
shared_drive_prefix = shared_drive.get_prefix('monashbec')


if os.name == 'nt':
    # Make it not look so terrible (if icons and themes are installed):
    settings = gtk.settings_get_default()
    settings.set_string_property('gtk-icon-theme-name', 'gnome-human', '')
    settings.set_string_property('gtk-theme-name', 'Clearlooks', '')
    settings.set_string_property('gtk-font-name', 'ubuntu 9', '')
    # Have Windows 7 consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.lyse.1-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass

def setup_logging():
    logger = logging.getLogger('LYSE')
    handler = logging.handlers.RotatingFileHandler(r'C:\\pythonlib\lyse\lyse.log', maxBytes=1024*1024*50)
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    if sys.stdout.isatty():
        terminalhandler = logging.StreamHandler(sys.stdout)
        terminalhandler.setFormatter(formatter)
        terminalhandler.setLevel(logging.DEBUG)
        logger.addHandler(terminalhandler)
    else:
        # Prevent bug on windows where writing to stdout without a command
        # window causes a crash:
        sys.stdout = sys.stderr = open(os.devnull,'w')
    logger.setLevel(logging.DEBUG)
    return logger
    
logger = setup_logging()
excepthook.set_logger(logger)
logger.info('\n\n===============starting===============\n')
        
class RoutineBox(object):

    def __init__(self, container, app, from_filebox, to_filebox, to_outputbox, multishot=False):
    
        self.app = app
        
        self.type = 'multi' if multishot else 'single' 
        
        self.logger = logging.getLogger('LYSE.RoutineBox.%s'%self.type)  
        self.logger.info('starting')
        # The queues, through which the filebox will tell us what files
        # to analyse, and we will report progress back:
        self.to_filebox = to_filebox
        self.from_filebox = from_filebox
        
        # The queue through which the analysis routines will deliver their
        # stdout and stderr to the outputbox:
        self.to_outputbox = to_outputbox
        
        # This list will contain AnalysisRoutine objects. Its order
        # will be kept consistent with the order of the routines in
        # the liststore.
        self.routines = []

        # The folder path that the add-routine file browser will open to:
        self.current_folder = analysislib_prefix
        
        # Make a gtk builder, get the widgets we need, connect signals:
        builder = gtk.Builder()
        builder.add_from_file('routinebox.glade')

        self.treeview = builder.get_object('treeview')
        self.liststore = builder.get_object('liststore')
        self.enable_all = builder.get_object('enable_all')
        self.plot_all = builder.get_object('plot_all')
        self.multiplot_all = builder.get_object('multiplot_all')
        self.filechooserbutton =  builder.get_object('filechooserbutton')
        self.filefilter = builder.get_object('output_filefilter')
        toplevel = builder.get_object('toplevel')
        label = builder.get_object('label')

        builder.connect_signals(self)

        # Allow you to select multiple entries in the treeview:
        self.treeselection = self.treeview.get_selection()
        self.treeselection.set_mode(gtk.SELECTION_MULTIPLE)

        if multishot:
            label.set_markup('<b>Multi shot routines</b>')
            self.filechooserbutton.show()
            self.filefilter.add_pattern('*.h5')
            self.filefilter.set_name('HDF5 files')
        container.add(toplevel)
        toplevel.show()
        
        # Start the thread to handle requests from the FileBox to
        # process files:
        self.analysis = threading.Thread(target = self.analysis_loop)
        self.analysis.daemon = True
        self.analysis.start()
    
    def destroy(self):
        for routine in self.routines:
            routine.destroy()
            
    def todo(self):
        """How many analysis routines are not done?"""
        todo = 0
        with gtk.gdk.lock:
            for row in self.liststore:
                if row[ENABLE] and not row[SUCCESS]:
                    todo += 1
        return todo
                            
    def analysis_loop(self):
        while True:
            instruction, filepath = self.from_filebox.get()
            if self.type == 'multi':
                filepath = self.filechooserbutton.get_filename()
            self.logger.debug('got a file to process: %s'%filepath)
            # Clear the 'success' and 'error 'markers:
            with gtk.gdk.lock:
                for row in self.liststore:
                    row[SUCCESS] = False
                    row[ERROR] = False
            remaining = self.todo()
            done = 0
            error = False
            while remaining:
                self.logger.debug('%d routines left to do'%remaining)
                routine = None
                with gtk.gdk.lock:
                    for i, row in enumerate(self.liststore):
                        if row[ENABLE] and not row[SUCCESS]:
                            routine = self.routines[i]
                            break
                if routine is not None:
                    self.logger.info('running analysis routine %s'%routine.shortname)
                    success = routine.do_analysis(self.type, filepath)
                    if success:
                        self.logger.debug('success')
                        done += 1   
                    else:
                        self.logger.debug('failure')
                        error = True
                        break
                remaining = self.todo()
                completion = 100*float(done)/(remaining + done)
                self.to_filebox.put(['progress',completion])
            if error:
                self.to_filebox.put(['error', None])
            else:
                self.to_filebox.put(['done',None])
            self.logger.debug('completed analysis of %s'%filepath)
            
    def reorder(self, order):
        # Apply the reordering to the liststore:
        self.liststore.reorder(order)
        # Apply it to our list of routines:
        self.routines = [self.routines[i] for i in order]
        # Tell each routine what its new index is:
        for i, routine in enumerate(self.routines):
            routine.index = i

    def move_up(self, button):
        model, selection = self.treeselection.get_selected_rows()
        selection = [path[0] for path in selection]
        n = self.liststore.iter_n_children(None)
        order = range(n)
        for i in sorted(selection):
            if 0 < i < n and (order[i - 1] not in selection):
                order[i], order[i - 1] = order[i - 1], order[i]
        self.reorder(order)

    def move_down(self, button):
        model, selection = self.treeselection.get_selected_rows()
        selection = [path[0] for path in selection]
        n = self.liststore.iter_n_children(None)
        order = range(n)
        for i in reversed(sorted(selection)):
            if 0 <= i < n - 1 and (order[i + 1] not in selection):
                order[i], order[i + 1] = order[i + 1], order[i]
        self.reorder(order)

    def move_top(self, button):
        model, selection = self.treeselection.get_selected_rows()
        selection = [path[0] for path in selection]
        n = self.liststore.iter_n_children(None)
        order = range(n)
        for i in sorted(selection):
            while 0 < i < n and (order[i - 1] not in selection):
                # swap!
                order[i], order[i - 1] = order[i - 1], order[i]
                i -= 1
        self.reorder(order)

    def move_bottom(self, button):
        model, selection = self.treeselection.get_selected_rows()
        selection = [path[0] for path in selection]
        n = self.liststore.iter_n_children(None)
        order = range(n)
        for i in reversed(sorted(selection)):
            while 0 <= i < n - 1 and (order[i + 1] not in selection):
                # swap!
                order[i], order[i + 1] = order[i + 1], order[i]
                i += 1
        self.reorder(order)

    def delete_selection(self, button):
        model, selection = self.treeselection.get_selected_rows()
        # Have to delete one at a time, since the indices change after
        # each deletion:
        while selection:
            path = selection[0]
            iter = model.get_iter(path)
            model.remove(iter)
            selection = self.treeview.get_selection()
            model, selection = selection.get_selected_rows()
            self.logger.debug('deleting routine %s'%self.routines[path[0]].shortname)
            self.routines[path[0]].destroy()
            del self.routines[path[0]]
        # Tell the routines their new indices:
        for i, routine in enumerate(self.routines):
            routine.index = i

    def add_routine(self, button):
            dialog = gtk.FileChooserDialog(
                'Select routine(s) to add', self.app.window,
                gtk.FILE_CHOOSER_ACTION_OPEN,
                buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                         gtk.STOCK_OPEN, gtk.RESPONSE_OK))
            # Make the dialog only show python files:
            py_filefilter = gtk.FileFilter()
            py_filefilter.add_pattern('*.py')
            py_filefilter.set_name('Python scripts')
            dialog.add_filter(py_filefilter)
            # Other settings:
            dialog.set_select_multiple(True)
            dialog.set_current_folder(self.current_folder)
            dialog.set_default_response(gtk.RESPONSE_OK)
            # Run the dialog, get the files and add them to the list of
            # opened files:
            response = dialog.run()
            files = dialog.get_filenames()
            dialog.destroy()
            if response == gtk.RESPONSE_OK:
                for file_ in files:
                    if file_ not in [routine.filepath for routine in
                                     self.routines]:
                        self.logger.debug('adding routine %s'%os.path.basename(file_))
                        self.routines.append(AnalysisRoutine(file_, self))
                self.current_folder = os.path.dirname(file_)
            self.refresh_overall_checkbuttons()

    def enable_all_clicked(self, column):
        inconsistent = self.enable_all.get_inconsistent()
        active = self.enable_all.get_active()
        if inconsistent:
            self.enable_all.set_active(False)
            self.enable_all.set_inconsistent(False)
        else:
            self.enable_all.set_active(not active)
        # Set all rows to the same state:
        active = self.enable_all.get_active()
        for routine in self.routines:
            iter = self.liststore.get_iter(routine.index)
            self.liststore.set(iter, ENABLE, active)
        self.refresh_overall_checkbuttons()

    def plot_all_clicked(self, column):
        inconsistent = self.plot_all.get_inconsistent()
        active = self.plot_all.get_active()
        if inconsistent:
            self.plot_all.set_active(False)
            self.plot_all.set_inconsistent(False)
        else:
            self.plot_all.set_active(not active)
        # Set all rows to the same state, but only if they are enabled:
        active = self.plot_all.get_active()
        for routine in self.routines:
            iter = self.liststore.get_iter(routine.index)
            enabled, = self.liststore.get(iter, ENABLE)
            if enabled:
                self.liststore.set(iter, SHOW_PLOTS, active)
                self.liststore.set(iter, INCONSISTENT, False)

    def multiplot_all_clicked(self, column):
        inconsistent = self.multiplot_all.get_inconsistent()
        active = self.multiplot_all.get_active()
        if inconsistent:
            self.multiplot_all.set_active(False)
            self.multiplot_all.set_inconsistent(False)
        else:
            self.multiplot_all.set_active(not active)
        # Set all rows to the same state, but only if they are enabled:
        active = self.multiplot_all.get_active()
        for routine in self.routines:
            iter = self.liststore.get_iter(routine.index)
            enabled, = self.liststore.get(iter, ENABLE)
            if enabled:
                self.liststore.set(iter, MULTIPLE_PLOTS, active)

    def enable_toggled(self, renderer, index):
        routine = self.routines[int(index)]
        iter = self.liststore.get_iter(routine.index)
        state, = self.liststore.get(iter, ENABLE)
        self.liststore.set(iter, ENABLE, not state)
        self.refresh_overall_checkbuttons()

    def plot_show_toggled(self, renderer, index):
        routine = self.routines[int(index)]
        iter = self.liststore.get_iter(routine.index)
        state, inconsistent = self.liststore.get(iter, SHOW_PLOTS,
                                                 INCONSISTENT)
        if inconsistent:
            self.liststore.set(iter, SHOW_PLOTS, False)
            self.liststore.set(iter, INCONSISTENT, False)
        else:
            self.liststore.set(iter, SHOW_PLOTS, not state)
        self.refresh_overall_checkbuttons()

    def multiplot_toggled(self, renderer, index):
        routine = self.routines[int(index)]
        iter = self.liststore.get_iter(routine.index)
        state, = self.liststore.get(iter, MULTIPLE_PLOTS)
        self.liststore.set(iter, MULTIPLE_PLOTS, not state)
        self.refresh_overall_checkbuttons()

    def refresh_overall_checkbuttons(self):
        iter = self.liststore.get_iter_first()
        enable = []
        show_plots = []
        multiple_plots = []
        while iter:
            en, show, multi = self.liststore.get(iter, ENABLE, SHOW_PLOTS,
                                                 MULTIPLE_PLOTS)
            enable.append(en)
            show_plots.append(show)
            multiple_plots.append(multi)
            iter = self.liststore.iter_next(iter)

        if all(enable):
            self.enable_all.set_active(True)
            self.enable_all.set_inconsistent(False)
        elif not any(enable):
            self.enable_all.set_active(False)
            self.enable_all.set_inconsistent(False)
        else:
            self.enable_all.set_inconsistent(True)

        # Only count the routines that are enabled:
        show_plots = [p for i, p in enumerate(show_plots) if enable[i]]
        if all(show_plots) and show_plots:
            self.plot_all.set_active(True)
            self.plot_all.set_inconsistent(False)
        elif not any(show_plots):
            self.plot_all.set_active(False)
            self.plot_all.set_inconsistent(False)
        else:
            self.plot_all.set_inconsistent(True)

        # Only count the routines that are enabled:
        multi_plots = [p for i, p in enumerate(multiple_plots) if enable[i]]
        if all(multi_plots) and multiple_plots:
            self.multiplot_all.set_active(True)
            self.multiplot_all.set_inconsistent(False)
        elif not any(multi_plots):
            self.multiplot_all.set_active(False)
            self.multiplot_all.set_inconsistent(False)
        else:
            self.multiplot_all.set_inconsistent(True)

    def on_row_activated(self, treeview, path, column):
        index = path[0]
        routine = self.routines[int(index)]
        filepath = routine.filepath
        if os.name == 'nt':
            subprocess.Popen([r'C:\Program Files\Notepad++\notepad++', filepath])
        elif 'linux' in sys.platform:
            subprocess.Popen(['gedit',filepath])
                
class FileBox(object):
    storecolumns = ['progress_visible',
               'progress_value',
               'error_visible',
               'success_visible',
               'enable']
    
    storetypes = {'progress_visible': bool,
               'progress_value': int,
               'error_visible': bool,
               'success_visible': bool,
               'enable': bool}
               
    defaults = {'progress_visible': True,
               'progress_value': 0,
               'error_visible': False,
               'success_visible': False,
               'enable': True}
               
    def __init__(self, container, app, to_singleshot, from_singleshot, to_multishot, from_multishot):

        self.app = app
        self.to_singleshot = to_singleshot
        self.to_multishot = to_multishot
        self.from_singleshot = from_singleshot
        self.from_multishot = from_multishot
        
        self.logger = logging.getLogger('LYSE.FileBox')  
        self.logger.info('starting')
        # The folder that the add-shots dialog will open to:
        self.current_folder = os.path.join(shared_drive_prefix,'Experiments')
        
        # Make a gtk builder, get the widgets we need, connect signals:
        builder = gtk.Builder()
        builder.add_from_file('filebox.glade')

        self.treeview = builder.get_object('treeview')
        self.enable_all = builder.get_object('enable_all')
        self.not_paused_vbox = builder.get_object('not_paused')
        self.paused_vbox = builder.get_object('paused')
        self.pause_togglebutton = builder.get_object('pause_button')
        toplevel = builder.get_object('toplevel')
        scrolledwindow = builder.get_object('scrolledwindow')

        builder.connect_signals(self)
        
        # Allow you to select multiple entries in the treeview:
        self.treeselection = self.treeview.get_selection()
        self.adjustment = scrolledwindow.get_vadjustment()
        self.treeselection.set_mode(gtk.SELECTION_MULTIPLE)

        container.add(toplevel)
        toplevel.show()
        
        self.incoming_paused = False
        self.analysis_loop_paused = False
        
        # A condition to let the looping threads know when to recheck conditions
        # they're waiting on (instead of having them do time.sleep)
        self.timing_condition = threading.Condition()
        
        # Whether the last scroll to the bottom of the treeview has been processed:
        self.scrolled = True
        
        # A queue for storing incoming files from the HTTP server so
        # the server can keep receiving files even if analysis is slow
        # or paused:
        self.incoming_queue = Queue.Queue()
        
        # This dataframe will contain all the scalar data
        # from the run files that are currently open:
        index = pandas.MultiIndex.from_tuples([('filepath', '')])
        self.dataframe = pandas.DataFrame({'filepath':[]},columns=index)
        
        # This stores which column in the dataframe corresponds to which
        # column in the liststore:
        self.column_labels = {}
        
        self.liststore = gtk.ListStore(*self.storetypes.values())
        self.treeview.set_model(self.liststore)
        
        # Start the thread to handle incoming files, and store them in
        # a buffer if processing is paused:
        self.incoming = threading.Thread(target = self.incoming_buffer_loop)
        self.incoming.daemon = True
        self.incoming.start()
        
        self.analysis = threading.Thread(target = self.analysis_loop)
        self.analysis.daemon = True
        self.analysis.start()

        self.adjustment.set_value(self.adjustment.upper - self.adjustment.page_size)
        
    def incoming_buffer_loop(self):
        logger = logging.getLogger('LYSE.FileBox.incoming')  
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        
        # Whilst the queue manager is not paused, add files to the
        # filebox. If the manager is paused, let them queue up.
        while True:
            while self.incoming_paused:
                time.sleep(1)
            filepaths = self.incoming_queue.get()
            logger.info('adding some files')
            self.add_files(filepaths, marked=True)
            
    def analysis_loop(self):
        logger = logging.getLogger('LYSE.FileBox.analysis_loop')
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        with gtk.gdk.lock:
            completed_column = self.storecolumns.index('success_visible')
            progress_column = self.storecolumns.index('progress_value')
            progress_visible_column = self.storecolumns.index('progress_visible')
        next_row = None
        self.multishot_required = False
        while True:
            while self.analysis_loop_paused:
                with self.timing_condition:
                    self.timing_condition.wait()
            if next_row is None:
                print 'no next file for analysis loop'            
                with self.timing_condition:
                    self.timing_condition.wait()
            
            next_row = None     
            with gtk.gdk.lock:
                for row in self.liststore:
                    if not row[completed_column]:
                        next_row = row
                        break
                if next_row is not None:
                    # Ok, now we have a file which has not been processed yet.
                    filepath_column = self.column_labels[('filepath',)]
                    path = row[filepath_column]
            if next_row is not None:
                # Now that we've relinquished the gtk lock, when it comes
                # time to write data back to the list store, we'll have to
                # look up by filename. This is because the liststore could
                # change dramatically whilst we're doing work - even being
                # destroyed and remade.
                self.to_singleshot.put(['analyse', path])
                while True:
                    signal, data = self.from_singleshot.get()
                    print 'filebox: got progress response'
                    if signal == 'progress':
                        completion = data
                        with gtk.gdk.lock:
                            for row in self.liststore:
                                if row[filepath_column] == path:
                                    row[progress_column] = completion
                        self.update_row(path)
                    elif signal == 'done':
                        with gtk.gdk.lock:
                            for row in self.liststore:
                                if row[filepath_column] == path:
                                    row[progress_visible_column] = False
                                    row[completed_column] = True
                                    break
                        break
                    elif signal == 'error':
                        self.pause_togglebutton.set_active(True)
                        break
                # Evey time single-shot analysis is completed, even if
                # there were no single-shot routines, we trigger a new
                # multishot analysis to be done:
                self.multishot_required = True
            if self.multishot_required and next_row is None:
                self.to_multishot.put(['analyse', None])
                error = False
                while True:
                    signal, data = self.from_multishot.get()
                    print 'filebox: got progress response'
                    if signal == 'progress':
                        completion = data
                    elif signal == 'done':
                        break
                    elif signal == 'error':
                        self.pause_togglebutton.set_active(False)
                        break
                if not error:
                    self.multishot_required = False
                
    def on_pause_button_toggled(self,button):
        self.analysis_loop_paused = button.get_active()
        self.paused_vbox.set_visible(self.analysis_loop_paused)
        self.not_paused_vbox.set_visible(not self.analysis_loop_paused)
        # Let the analysis thread know to check the pause state again:
        with self.timing_condition:
            self.timing_condition.notify_all()    
            
    def scroll_to_bottom(self):
        self.adjustment.set_value(self.adjustment.upper - self.adjustment.page_size)
        self.scrolled = True
        
    def add_files(self, filepaths, marked=False):
        for i, filepath in enumerate(filepaths):
            filepath = filepath.replace('Z:',shared_drive_prefix)
            if not os.name == 'nt':
                filepath = filepath.replace('\\','/')
            # Using the lock so as to prevent modifying the dataframe
            # whilst update_liststore is mid-loop over it:
            with gtk.gdk.lock:
                print 'adding file', i
                if filepath in self.dataframe['filepath'].values:
                    # Ignore duplicates:
                    continue
                print filepath
                row = get_dataframe_from_shot(filepath)
                self.dataframe = concat_with_padding(self.dataframe,row)
        with gtk.gdk.lock:
            self.update_liststore()
        if self.scrolled:
            with gtk.gdk.lock:
                # Are we scrolled to the bottom of the TreeView?
                if self.adjustment.value == self.adjustment.upper - self.adjustment.page_size:
                    self.scrolled = False                 
                    gobject.idle_add(self.scroll_to_bottom)
        # Let waiting threads know to check for new files:
        with self.timing_condition:
            self.timing_condition.notify_all()
            
    def update_row(self, filepath):
        print 'updating row', filepath
        row = get_dataframe_from_shot(filepath)
        index = numpy.where(self.dataframe['filepath'].values == row['filepath'].values)
        try:
            index = index[0][0]
        except IndexError:
            # Shot was deleted from dataframe by user
            return
        # Update the row in the dataframe:
        self.dataframe = replace_with_padding(self.dataframe, row, index)  
        # Check if new columns need to be created: 
        with gtk.gdk.lock:
            self.update_liststore()
            # update the row in the liststore:
            for rowindex, store_row in enumerate(self.liststore):
                if store_row[self.column_labels[('filepath',)]] == filepath:
                    for label, colindex in self.column_labels.items():
                        item = self.dataframe[label].values[index]
                        if isinstance(item, str):
                            lines = item.splitlines()
                            if len(lines) > 1:
                                item = lines[0] + ' ...'
                        store_row[colindex] = item
            
    def update_liststore(self):
        print 'updating liststore!'
        types = [self.storetypes[label] for label in self.storecolumns] + [str] * len(self.column_labels)
        new_store_required = False
        # Ensure every column in the dataframe has a corresponding column
        # in the liststore:
        for label in self.dataframe.columns:
            label = tuple([item for item in label if item])
            if label not in self.column_labels:
                new_store_required = True
                index = len(self.column_labels) + len(self.storecolumns)
                self.column_labels[label] = index
                types.append(str)
                renderer = gtk.CellRendererText()
                widget = gtk.HBox()
                label = '\n'.join(label)
                heading = gtk.Label(str(label))
                heading.show()
                column = gtk.TreeViewColumn()
                column.pack_start(renderer)
                column.set_widget(heading)
                column.add_attribute(renderer, 'text', index)
                column.set_resizable(True)
                column.set_reorderable(True)
                self.treeview.append_column(column)
        if new_store_required:
            old_store = self.liststore
            self.liststore = gtk.ListStore(*types)
            self.treeview.set_model(self.liststore)
        for rowindex, row in enumerate(self.dataframe.iterrows()):
            # If there isn't a new list store, skip to the first new row and
            # append the new data:
            if rowindex < len(self.liststore) and not new_store_required:
                continue
            elif new_store_required and rowindex < len(old_store):
                print 'rebuilding liststore', rowindex
                # Copy the existing data from the old liststore:
                store_row = ['nan']*len(types)
                for colindex, item in enumerate(old_store[rowindex]):
                    store_row[colindex] = item
            else:
                # Create a new row with data from the dataframe:
                store_row = ['']*len(types)
                for colindex, label in enumerate(self.storecolumns):
                    store_row[colindex] = self.defaults[label]
                for label, colindex in self.column_labels.items():
                    item = self.dataframe[label].values[rowindex]
                    if isinstance(item, str):
                        lines = item.splitlines()
                        if len(lines) > 1:
                            item = lines[0] + ' ...'
                    store_row[colindex] = item
            self.liststore.append(store_row)
            
    def on_add_files_clicked(self, button):
        dialog = gtk.FileChooserDialog(
            'Select shot(s) to add', self.app.window,
            gtk.FILE_CHOOSER_ACTION_OPEN,
            buttons=(gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL,
                     gtk.STOCK_OPEN, gtk.RESPONSE_OK))
        # Make the dialog only show h5 files:
        h5_filefilter = gtk.FileFilter()
        h5_filefilter.add_pattern('*.h5')
        h5_filefilter.set_name('HDF5 files')
        dialog.add_filter(h5_filefilter)
        # Other settings:
        dialog.set_select_multiple(True)
        dialog.set_local_only(False)
        dialog.set_current_folder(self.current_folder)
        dialog.set_default_response(gtk.RESPONSE_OK)
        # Run the dialog, get the files and add them to the list of
        # opened files:
        response = dialog.run()
        files = dialog.get_filenames()
        dialog.destroy()
        if response == gtk.RESPONSE_OK:
            self.incoming_queue.put([f for f in files if f.endswith('.h5')])
            self.current_folder = os.path.dirname(files[0])
            
    def delete_selection(self, button):
        model, selection = self.treeselection.get_selected_rows()
        print selection
        # Delete by index from the dataframe:
        self.dataframe = self.dataframe.drop([path[0] for path in selection])
        self.dataframe.index[:] = range(len(self.dataframe))
        # Have to delete one at a time from the liststore, since the
        # indices change after each deletion:
        while selection:
            path = selection[0]
            iter = model.get_iter(path)
            model.remove(iter)
            selection = self.treeview.get_selection()
            model, selection = selection.get_selected_rows()
             
    def run_multishot_clicked(self, button):
        self.multishot_required = True
        with self.timing_condition:
            self.timing_condition.notify()
            
    def mark_as_not_done(self, button):
        model, selection = self.treeselection.get_selected_rows()
        selection = [path[0] for path in selection]
        success_column = self.storecolumns.index('success_visible')
        for i in selection:
            self.liststore[i][success_column] = False
        with self.timing_condition:
            self.timing_condition.notify()
            
    def on_edit_columns_clicked(self, button):
        visible = {}
        for column in self.treeview.get_columns():
            label = column.get_widget()
            if isinstance(label, gtk.Label):
                title = label.get_text()
                visible[title] = column.get_visible()
        dialog = EditColumns(self,visible)
    
    def set_visible_columns(self, visible):
        for column in self.treeview.get_columns():
            label = column.get_widget()
            if isinstance(label, gtk.Label):
                title = label.get_text()
                column.set_visible(visible[title])
             
class EditColumns(object):

    def __init__(self, filebox, columns):
        self.filebox = filebox
        
        builder = gtk.Builder()
        builder.add_from_file('edit_columns.glade')
        builder.connect_signals(self)
        
        self.window = builder.get_object('toplevel')
        self.treeview = builder.get_object('treeview')
        self.liststore = builder.get_object('liststore')
        self.all_visible_checkbutton = builder.get_object('all_visible_checkbutton')
        self.filter_entry = builder.get_object('filter_entry')
        
        self.filter_store = self.liststore.filter_new()
        self.treeview.set_model(self.filter_store)
        self.filter_store.set_visible_func(self.check_filter)
        names = columns.keys()
        # Sort first by number of lines, then alphabetically:
        names.sort(key=lambda s: [len(s.splitlines()),s])
        for name in names:
            self.liststore.append([name,columns[name]])
        self.update_overall_checkbutton()
        self.window.show()
    
    def check_filter(self, model, iter):
        name = self.liststore.get(iter, 0)[0]
        if isinstance(name, str):
            if self.filter_entry.get_text().lower() in name.lower():
                return True
            
    def on_filter_changed(self, entry):
        self.filter_store.refilter()
        
    def on_cancel_clicked(self, button):
        self.window.destroy()
    
    def on_ok_clicked(self,button):
        columns = {}
        for row in self.liststore:
            name, visible = row
            columns[name] = visible
        self.filebox.set_visible_columns(columns)
        self.window.destroy()
                 
    def on_visible_toggled(self,cellrenderer, index):
        filtered_index = int(index)
        real_index = self.filter_store.convert_path_to_child_path(filtered_index)
        self.liststore[real_index][1] = not self.liststore[real_index][1]
        self.update_overall_checkbutton()
        
    def on_all_visible_toggled(self,*args):
        self.all_visible_checkbutton.set_inconsistent(False)
        state = not self.all_visible_checkbutton.get_active()
        self.all_visible_checkbutton.set_active(state)
        for row in self.liststore:
            row[1] = state
        
    def update_overall_checkbutton(self):
        enabled = []
        for row in self.liststore:
            enabled.append(row[1])
        if all(enabled):
            self.all_visible_checkbutton.set_inconsistent(False)
            self.all_visible_checkbutton.set_active(True)
        elif any(enabled):
            self.all_visible_checkbutton.set_inconsistent(True)
            self.all_visible_checkbutton.set_active(False)
        else:
            self.all_visible_checkbutton.set_inconsistent(False)
            self.all_visible_checkbutton.set_active(False)
            
class RequestHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        self.send_response(200)
        self.end_headers()
        ctype, pdict = cgi.parse_header(
            self.headers.getheader('content-type'))
        length = int(self.headers.getheader('content-length'))
        postvars = cgi.parse_qs(self.rfile.read(length), keep_blank_values=1)
        if postvars:
            h5_filepath = postvars['filepath'][0]
            if h5_filepath == 'hello':
                self.wfile.write('hello')
            else:
                app.filebox.incoming_queue.put([h5_filepath])
                self.wfile.write('added successfully')
        else:
            self.wfile.write(pickle.dumps(app.filebox.dataframe))
        self.wfile.close()
    
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(pickle.dumps(app.filebox.dataframe))
        self.wfile.close()

               
class AnalysisApp(object):
    port = 42519

    def __init__(self):
        # Make a gtk builder, get the widgets we need, connect signals:
        builder = gtk.Builder()
        builder.add_from_file('main_interface.glade')

        self.window = builder.get_object('window')
        singleshot_container = builder.get_object('singleshot_container')
        multishot_container = builder.get_object('multishot_container')
        filebox_container = builder.get_object('filebox_container')
        outputbox_container = builder.get_object('outputbox_container')
        
        self.window.connect('destroy', self.destroy)
        # All running analysis routines will have their output streams
        # redirected to the outputbox via this queue:
        to_outputbox = Queue.Queue()
        
        # The singleshot routinebox will be connected to the filebox
        # by queues:
        to_singleshot = Queue.Queue()
        from_singleshot = Queue.Queue()
        
        # So will the multishot routinebox:
        to_multishot = Queue.Queue()
        from_multishot = Queue.Queue()
        
        # I could have had the boxes instantiate their own queues and pull
        # them out of each other as attributes, but it's more explicit
        # to instantiate them here, and hopefully easier for someone to
        # see how these things are connected.
        
        self.singleshot_routinebox = RoutineBox( singleshot_container, self, to_singleshot, from_singleshot, to_outputbox)
        self.multishot_routinebox = RoutineBox(multishot_container, self, to_multishot, from_multishot, to_outputbox, multishot=True)
        self.filebox = FileBox(filebox_container, self, to_singleshot, from_singleshot, to_multishot, from_multishot)
        self.outputbox = OutputBox(outputbox_container, to_outputbox)
        
        # Start daemon thread for the HTTP server:
        self.server = threading.Thread(target=HTTPServer(('', self.port), RequestHandler).serve_forever)
        self.server.daemon = True
        self.server.start()
        
        self.window.resize(1600, 900)
        self.window.maximize()
        self.window.show()
        print 'number of threads is:', threading.active_count()
    
    def destroy(self,*args):
        gtk.main_quit()
        # The routine boxes have subprocesses that need to be quit:
        self.singleshot_routinebox.destroy()
        self.multishot_routinebox.destroy()
        
        
if __name__ == '__main__':
    gtk.gdk.threads_init()
    app = AnalysisApp()
    with gtk.gdk.lock:
        gtk.main()
