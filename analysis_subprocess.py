#####################################################################
#                                                                   #
# /analysis_subprocess.py                                           #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

from __future__ import division, unicode_literals, print_function, absolute_import
from labscript_utils import PY2
if PY2:
    str = unicode

import labscript_utils.excepthook
import zprocess
to_parent, from_parent, kill_lock = zprocess.setup_connection_with_parent(lock = True)

import sys
import os
import threading
import traceback
import time

from qtutils.qt import QtCore, QtGui, QtWidgets, QT_ENV, PYQT5
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils.qt.QtCore import pyqtSlot as Slot

import matplotlib
if QT_ENV == PYQT5:
    matplotlib.use("QT5Agg")
else:
    matplotlib.use("QT4Agg")

import lyse
lyse.spinning_top = True
import lyse.figure_manager
lyse.figure_manager.install()

if QT_ENV == PYQT5:
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
else:
    from matplotlib.backends.backend_qt4agg import NavigationToolbar2QT as NavigationToolbar
import pylab
import zprocess.locking, labscript_utils.h5_lock, h5py

import zprocess
from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager
import qtutils.icons

from labscript_utils.modulewatcher import ModuleWatcher

class _DeprecationDict(dict):
    """Dictionary that spouts deprecation warnings when you try to access some
    keys."""
    def __init__(self, *args, **kwargs):
        self.deprecation_messages = {} # To be added to after the deprecated items are added to the dict.
        dict.__init__(self, *args, **kwargs)

    def __getitem__(self, key):
        if key in self.deprecation_messages:
            import warnings
            import linecache
            # DeprecationWarnings are ignored by default. Clear the filter so
            # they are not:
            previous_warning_filters = warnings.filters[:]
            try:
                warnings.resetwarnings()
                # Hacky stuff to get it to work from within execfile() with
                # correct line data:
                linecache.clearcache()
                caller = sys._getframe(1)
                globals = caller.f_globals
                lineno = caller.f_lineno
                module = globals['__name__']
                filename = globals.get('__file__')
                fnl = filename.lower()
                if fnl.endswith((".pyc", ".pyo")):
                    filename = filename[:-1]
                message = self.deprecation_messages[key]
                warnings.warn_explicit(message, DeprecationWarning, filename, lineno, module)
            finally:
                # Restore the warnings filter:
                warnings.filters[:] = previous_warning_filters
        return dict.__getitem__(self, key)

    def __setitem__(self, key, value):
        if key in self.deprecation_messages:
            # No longer deprecated if the user puts something in place of the
            # originally deprecated item:
            del self.deprecation_messages[key]
        return dict.__setitem__(self, key, value)


def set_win_appusermodel(window_id):
    from labscript_utils.winshell import set_appusermodel, appids, app_descriptions
    icon_path = os.path.abspath('lyse.ico')
    executable = sys.executable.lower()
    if not executable.endswith('w.exe'):
        executable = executable.replace('.exe', 'w.exe')
    relaunch_command = executable + ' ' + os.path.abspath(__file__.replace('.pyc', '.py'))
    relaunch_display_name = app_descriptions['lyse']
    set_appusermodel(window_id, appids['lyse'], icon_path, relaunch_command, relaunch_display_name)


class PlotWindow(QtWidgets.QWidget):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)
    close_signal = Signal()

    def event(self, event):
        result = QtWidgets.QWidget.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result

    def closeEvent(self, event):
        self.hide()
        event.ignore()
        

class Plot(object):
    def __init__(self, figure, identifier, filepath):
        loader = UiLoader()
        self.ui = loader.load('plot_window.ui', PlotWindow())

        # Tell Windows how to handle our windows in the the taskbar, making pinning work properly and stuff:
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)

        self.set_window_title(identifier, filepath)

        # figure.tight_layout()
        self.figure = figure
        self.canvas = figure.canvas
        self.navigation_toolbar = NavigationToolbar(self.canvas, self.ui)

        self.lock_action = self.navigation_toolbar.addAction(
            QtGui.QIcon(':qtutils/fugue/lock-unlock'),
           'Lock axes', self.on_lock_axes_triggered)
        self.lock_action.setCheckable(True)
        self.lock_action.setToolTip('Lock axes')

        self.copy_to_clipboard_action = self.navigation_toolbar.addAction(
            QtGui.QIcon(':qtutils/fugue/clipboard--arrow'),
           'Copy to clipboard', self.on_copy_to_clipboard_triggered)
        self.copy_to_clipboard_action.setToolTip('Copy to clipboard')
        self.copy_to_clipboard_action.setShortcut(QtGui.QKeySequence.Copy)


        self.ui.verticalLayout_canvas.addWidget(self.canvas)
        self.ui.verticalLayout_navigation_toolbar.addWidget(self.navigation_toolbar)

        self.lock_axes = False
        self.axis_limits = None

        self.update_window_size()

        self.ui.show()

    def on_lock_axes_triggered(self):
        if self.lock_action.isChecked():
            self.lock_axes = True
            self.lock_action.setIcon(QtGui.QIcon(':qtutils/fugue/lock'))
        else:
            self.lock_axes = False
            self.lock_action.setIcon(QtGui.QIcon(':qtutils/fugue/lock-unlock'))

    def on_copy_to_clipboard_triggered(self):
        lyse.figure_to_clipboard(self.figure)

    @inmain_decorator()
    def save_axis_limits(self):
        axis_limits = {}
        for i, ax in enumerate(self.figure.axes):
            # Save the limits of the axes to restore them afterward:
            axis_limits[i] = ax.get_xlim(), ax.get_ylim()

        self.axis_limits = axis_limits

    @inmain_decorator()
    def clear(self):
        self.figure.clear()

    @inmain_decorator()
    def restore_axis_limits(self):
        for i, ax in enumerate(self.figure.axes):
            try:
                xlim, ylim = self.axis_limits[i]
                ax.set_xlim(xlim)
                ax.set_ylim(ylim)
            except KeyError:
                continue

    @inmain_decorator()
    def set_window_title(self, identifier, filepath):
        self.ui.setWindowTitle(str(identifier) + ' - ' + os.path.basename(filepath))

    @inmain_decorator()
    def update_window_size(self):
        l, w = self.figure.get_size_inches()
        dpi = self.figure.get_dpi()
        self.canvas.resize(int(l*dpi),int(w*dpi))
        self.ui.adjustSize()

    @inmain_decorator()
    def draw(self):
        self.canvas.draw()

    def show(self):
        self.ui.show()

    @property
    def is_shown(self):
        return self.ui.isVisible()


class AnalysisWorker(object):
    def __init__(self, filepath, to_parent, from_parent):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.filepath = filepath

        # Filepath as a unicode string on py3 and a bytestring on py2,
        # so that the right string type can be passed to functions that
        # require the 'native' string type for that python version. On
        # Python 2, encode it with the filesystem encoding.
        if PY2:
            self.filepath_native_string = self.filepath.encode(sys.getfilesystemencoding())
        else:
            self.filepath_native_string = self.filepath
        
        # Add user script directory to the pythonpath:
        sys.path.insert(0, os.path.dirname(self.filepath_native_string))
        
        # Plot objects, keyed by matplotlib Figure object:
        self.plots = {}

        # An object with a method to unload user modules if any have
        # changed on disk:
        self.modulewatcher = ModuleWatcher()
        
        # Start the thread that listens for instructions from the
        # parent process:
        self.mainloop_thread = threading.Thread(target=self.mainloop)
        self.mainloop_thread.daemon = True
        self.mainloop_thread.start()
        
    def mainloop(self):
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        while True:
            task, data = self.from_parent.get()
            with kill_lock:
                if task == 'quit':
                    inmain(qapplication.quit)
                elif task == 'analyse':
                    path = data
                    success = self.do_analysis(path)
                    if success:
                        self.to_parent.put(['done', lyse._updated_data])
                    else:
                        self.to_parent.put(['error', lyse._updated_data])
                else:
                    self.to_parent.put(['error','invalid task %s'%str(task)])
        
    @inmain_decorator()
    def do_analysis(self, path):
        now = time.strftime('[%x %X]')
        if path is not None:
            print('%s %s %s ' %(now, os.path.basename(self.filepath), os.path.basename(path)))
        else:
            print('%s %s' %(now, os.path.basename(self.filepath)))

        self.pre_analysis_plot_actions()

        # The namespace the routine will run in:
        sandbox = _DeprecationDict(path=path,
                                   __name__='__main__',
                                   __file__= os.path.basename(self.filepath_native_string))
        # path global variable is deprecated:
        deprecation_message = ("use of 'path' global variable is deprecated and will be removed " +
                               "in a future version of lyse.  Please use lyse.path, which defaults " +
                               "to sys.argv[1] when scripts are run stand-alone.")
        sandbox.deprecation_messages['path'] = deprecation_message
        # Use lyse.path instead:
        lyse.path = path
        lyse._updated_data = {}

        # Save the current working directory before changing it to the
        # location of the user's script:
        cwd = os.getcwd()
        os.chdir(os.path.dirname(self.filepath))

        # Do not let the modulewatcher unload any modules whilst we're working:
        try:
            with self.modulewatcher.lock:
                # Actually run the user's analysis!
                with open(self.filepath) as f:
                    code = compile(f.read(), os.path.basename(self.filepath_native_string),
                                   'exec', dont_inherit=True)
                    exec(code, sandbox)
        except:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            del traceback_lines[1]
            # Avoiding a list comprehension here so as to avoid this
            # python bug in earlier versions of 2.7 (fixed in 2.7.9):
            # https://bugs.python.org/issue21591
            message = ''
            for line in traceback_lines:
                if PY2:
                    # errors='replace' is for Windows filenames present in the
                    # traceback that are not UTF8. They will not display
                    # correctly, but that's the best we can do - the traceback
                    # may contain code from the file in a different encoding,
                    # so we could have a mixed encoding string. This is only
                    # a problem for Python 2.
                    line = line.decode('utf8', errors='replace')
                message += line
            sys.stderr.write(message)
            return False
        else:
            return True
        finally:
            os.chdir(cwd)
            print('')
            self.post_analysis_plot_actions()
        
    def pre_analysis_plot_actions(self):
        for plot in self.plots.values():
            plot.save_axis_limits()
            plot.clear()

    def post_analysis_plot_actions(self):
        # reset the current figure to figure 1:
        lyse.figure_manager.figuremanager.set_first_figure_current()
        # Introspect the figures that were produced:
        for identifier, fig in lyse.figure_manager.figuremanager.figs.items():
            if not fig.axes:
                continue
            try:
                plot = self.plots[fig]
            except KeyError:
                # If we don't already have this figure, make a window
                # to put it in:
                self.new_figure(fig, identifier)
            else:
                if not plot.is_shown:
                    plot.show()
                    plot.update_window_size()
                plot.set_window_title(identifier, self.filepath)
                if plot.lock_axes:
                    plot.restore_axis_limits()
                plot.draw()


    def new_figure(self, fig, identifier):
        self.plots[fig] = Plot(fig, identifier, self.filepath)

    def reset_figs(self):
        pass
        
        
if __name__ == '__main__':
    filepath = from_parent.get()
    
    # Set a meaningful client id for zprocess.locking:
    zprocess.locking.set_client_process_name('lyse-'+os.path.basename(filepath))

    qapplication = QtWidgets.QApplication(sys.argv)
    worker = AnalysisWorker(filepath, to_parent, from_parent)
    qapplication.exec_()
        
