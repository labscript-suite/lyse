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
"""Analysis subprocess definitions and routines
"""
 
import labscript_utils.excepthook # I do magic stuff, so import must be in place
import labscript_utils.h5_lock, h5py

from labscript_utils.ls_zprocess import ProcessTree

import sys
import os
import threading
import traceback
import time
from types import ModuleType

from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal

from qtutils import inmain, inmain_decorator, UiLoader
import qtutils.icons

import multiprocessing

from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar

# Labscript imports
from labscript_utils.modulewatcher import ModuleWatcher
from labscript_utils import dedent


# Associate app windows with OS menu shortcuts:
import desktop_app
desktop_app.set_process_appid('lyse')

# lyse imports
import lyse.utils
import lyse.utils.worker
import lyse.figure_manager

# This process is not fork-safe. Spawn fresh processes on platforms that would fork:
if (
    hasattr(multiprocessing, 'get_start_method')
    and multiprocessing.get_start_method(True) != 'spawn'
):
    multiprocessing.set_start_method('spawn')


class PlotWindowCloseEvent(QtGui.QCloseEvent):
    def __init__(self, force, *args, **kwargs):
        QtGui.QCloseEvent.__init__(self, *args, **kwargs)
        self.force = force

class PlotWindow(QtWidgets.QWidget):
    # A signal for when the window manager has created a new window for this widget:
    close_signal = Signal()

    def __init__(self, plot, *args, **kwargs):
        self.__plot = plot
        QtWidgets.QWidget.__init__(self, *args, **kwargs)

    def closeEvent(self, event):
        self.hide()
        if isinstance(event, PlotWindowCloseEvent) and event.force:
            self.__plot.on_close()
            event.accept()
        else:
            event.ignore()
        

class Plot(object):
    def __init__(self, figure, identifier, filepath):
        self.identifier = identifier
        loader = UiLoader()
        self.ui = loader.load(os.path.join(lyse.utils.LYSE_DIR, 'user_interface/plot_window.ui'), PlotWindow(self))

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
        lyse.utils.worker.figure_to_clipboard(self.figure)

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

    def analysis_complete(self, figure_in_use):
        """To be overriden by subclasses. 
        Called as part of the post analysis plot actions"""
        pass

    def get_window_state(self):
        """Called when the Plot window is about to be closed due to a change in 
        registered Plot window class

        Can be overridden by subclasses if custom information should be saved
        (although bear in mind that you will passing the information from the previous 
        Plot subclass which might not be what you want unless the old and new classes
        have a common ancestor, or the change in Plot class is triggered by a reload
        of the module containing your Plot subclass). 

        Returns a dictionary of information on the window state.

        If you have overridden this method, please call the base method first and
        then update the returned dictionary with your additional information before 
        returning it from your method.
        """
        return {
            'window_geometry': self.ui.saveGeometry(),
            'axis_lock_state': self.lock_axes,
            'axis_limits': self.axis_limits,
        }

    def restore_window_state(self, state):
        """Called when the Plot window is recreated due to a change in registered
        Plot window class.

        Can be overridden by subclasses if custom information should be restored
        (although bear in mind that you will get the information from the previous 
        Plot subclass which might not be what you want unless the old and new classes
        have a common ancestor, or the change in Plot class is triggered by a reload
        of the module containing your Plot subclass). 

        If overriding, please call the parent method in addition to your new code

        Arguments:
            state: A dictionary of information to restore
        """
        geometry = state.get('window_geometry', None)
        if geometry is not None:
            self.ui.restoreGeometry(geometry)

        axis_limits = state.get('axis_limits', None)
        axis_lock_state = state.get('axis_lock_state', None)
        if axis_lock_state is not None:
            if axis_lock_state:
                # assumes the default state is False for new windows
                self.lock_action.trigger()

                if axis_limits is not None:
                    self.axis_limits = axis_limits
                    self.restore_axis_limits()

    def on_close(self):
        """Called when the window is closed.

        Note that this only happens if the Plot window class has changed. 
        Clicking the "X" button in the window title bar has been overridden to hide
        the window instead of closing it."""
        # release selected toolbar action as selecting an action acquires a lock
        # that is associated with the figure canvas (which is reused in the new
        # plot window) and this must be released before closing the window or else
        # it is held forever
        self.navigation_toolbar.pan()
        self.navigation_toolbar.zoom()
        self.navigation_toolbar.pan()
        self.navigation_toolbar.pan()


class AnalysisWorker(object):
    def __init__(self, filepath, to_parent, from_parent):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.filepath = filepath

        # Add user script directory to the pythonpath:
        sys.path.insert(0, os.path.dirname(self.filepath))
        
        # Create a module for the user's routine, and insert it into sys.modules as the
        # __main__ module:
        self.routine_module = ModuleType('__main__')
        self.routine_module.__file__ = self.filepath
        # Save the dict so we can reset the module to a clean state later:
        self.routine_module_clean_dict = self.routine_module.__dict__.copy()
        sys.modules[self.routine_module.__name__] = self.routine_module

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
                        if lyse.utils.worker._delay_flag:
                            lyse.utils.worker.delay_event.wait()
                        self.to_parent.put(['done', lyse.utils.worker._updated_data])
                    else:
                        self.to_parent.put(['error', lyse.utils.worker._updated_data])
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

        # Reset the routine module's namespace:
        self.routine_module.__dict__.clear()
        self.routine_module.__dict__.update(self.routine_module_clean_dict)
        # inject path into script namespace
        self.routine_module.__dict__['path'] = path

        # global variables used to communicate between analysis processes and GUI functions
        lyse.utils.worker.path = path  # for backwards compat with scripts that use lyse.path
        lyse.utils.worker.plots = self.plots
        lyse.utils.worker.Plot = Plot
        lyse.utils.worker._updated_data = {}
        lyse.utils.worker._delay_flag = False
        lyse.utils.worker.delay_event.clear()

        # Save the current working directory before changing it to the
        # location of the user's script:
        cwd = os.getcwd()
        os.chdir(os.path.dirname(self.filepath))

        # Do not let the modulewatcher unload any modules whilst we're working:
        try:
            with self.modulewatcher.lock:
                # Actually run the user's analysis!
                with open(self.filepath) as f:
                    code = compile(
                        f.read(),
                        self.routine_module.__file__,
                        'exec',
                        dont_inherit=True,
                    )
                    exec(code, self.routine_module.__dict__)
        except Exception:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            print('\n'.join(traceback_lines[1:]), file=sys.stderr)
            return False
        else:
            return True
        finally:
            os.chdir(cwd)
            print('')
            self.post_analysis_plot_actions()
        
    def pre_analysis_plot_actions(self):
        lyse.figure_manager.figuremanager.reset()
        for plot in self.plots.values():
            plot.save_axis_limits()
            plot.clear()

    def post_analysis_plot_actions(self):
        # reset the current figure to figure 1:
        lyse.figure_manager.figuremanager.set_first_figure_current()
        # Introspect the figures that were produced:
        for identifier, fig in lyse.figure_manager.figuremanager.figs.items():
            window_state = None
            if not fig.axes:
                # Try and clear the figure if it is not in use
                try:
                    plot = self.plots[fig]
                    plot.set_window_title("Empty", self.filepath)
                    plot.draw()
                    plot.analysis_complete(figure_in_use=False)
                except KeyError:
                    pass
                # Skip the rest of the loop regardless of whether we managed to clear
                # the unused figure or not!
                continue
            try:
                plot = self.plots[fig]

                # Get the Plot subclass registered for this plot identifier if it exists
                cls = lyse.utils.worker.get_plot_class(identifier)
                # If no plot was registered, use the base class
                if cls is None: cls = Plot
                
                # if plot instance does not match the expected identifier,  
                # or the identifier in use with this plot has changes,
                #  we need to close and reopen it!
                if type(plot) != cls or plot.identifier != identifier:
                    window_state = plot.get_window_state()

                    # Create a custom CloseEvent to force close the plot window
                    event = PlotWindowCloseEvent(True)
                    QtCore.QCoreApplication.instance().postEvent(plot.ui, event)
                    # Delete the plot
                    del self.plots[fig]

                    # force raise the keyerror exception to recreate the window
                    self.plots[fig]

            except KeyError:
                # If we don't already have this figure, make a window
                # to put it in:
                plot = self.new_figure(fig, identifier)

                # restore window state/geometry if it was saved
                if window_state is not None:
                    plot.restore_window_state(window_state)
            else:
                if not plot.is_shown:
                    plot.show()
                    plot.update_window_size()
                plot.set_window_title(identifier, self.filepath)
                if plot.lock_axes:
                    plot.restore_axis_limits()
                plot.draw()
            plot.analysis_complete(figure_in_use=True)


    def new_figure(self, fig, identifier):
        try:
            # Get custom class for this plot if it is registered
            cls = lyse.utils.worker.get_plot_class(identifier)
            # If no plot was registered, use the base class
            if cls is None: cls = Plot
            # if cls is not a subclass of Plot, then raise an Exception
            if not issubclass(cls, Plot): 
                raise RuntimeError('The specified class must be a subclass of lyse.Plot')
            # Instantiate the plot
            self.plots[fig] = cls(fig, identifier, self.filepath)
        except Exception:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            message = """Failed to instantiate custom class for plot "{identifier}".
                Perhaps lyse.register_plot_class() was called incorrectly from your
                script? The exception raised was:
                """.format(identifier=identifier)
            message = dedent(message) + '\n'.join(traceback_lines[1:])
            message += '\n'
            message += 'Due to this error, we used the default lyse.Plot class instead.\n'
            sys.stderr.write(message)

            # instantiate plot using original Base class so that we always get a plot
            self.plots[fig] = Plot(fig, identifier, self.filepath)

        return self.plots[fig]

    def reset_figs(self):
        pass
        
        
if __name__ == '__main__':

    os.environ['MPLBACKEND'] = "qt5agg"

    lyse.utils.worker.spinning_top = True

    lyse.figure_manager.install()

    # Where are pylab features used?  Should this be here?
    import pylab

    process_tree = ProcessTree.connect_to_parent()
    to_parent = process_tree.to_parent
    from_parent = process_tree.from_parent
    kill_lock = process_tree.kill_lock
    filepath = from_parent.get()

    # Rename this module to _analysis_subprocess and put it in sys.modules
    # under that name. The user's analysis routine will become the __main__ module
    # '_analysis_subprocess'.
    __name__ = '_analysis_subprocess'

    sys.modules[__name__] = sys.modules['__main__']

    # Set a meaningful client id for zlock
    process_tree.zlock_client.set_process_name('lyse-'+os.path.basename(filepath))

    qapplication = QtWidgets.QApplication.instance()
    if qapplication is None:
        qapplication = QtWidgets.QApplication(sys.argv)
    worker = AnalysisWorker(filepath, to_parent, from_parent)
    qapplication.exec_()
        
