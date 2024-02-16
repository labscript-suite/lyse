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

import labscript_utils.excepthook
from labscript_utils.ls_zprocess import ProcessTree

import sys
import os
import threading
import traceback
import time
import queue
import inspect
from types import ModuleType
import multiprocessing

from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal
from qtutils.qt.QtCore import pyqtSlot as Slot


from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager
import qtutils.icons

from labscript_utils.labconfig import LabConfig, save_appconfig, load_appconfig
from labscript_utils.qtwidgets.outputbox import OutputBox
from labscript_utils.modulewatcher import ModuleWatcher
import lyse
import lyse.ui_helpers

# Associate app windows with OS menu shortcuts:
import desktop_app
desktop_app.set_process_appid('lyse')


# This process is not fork-safe. Spawn fresh processes on platforms that would fork:
if (
    hasattr(multiprocessing, 'get_start_method')
    and multiprocessing.get_start_method(True) != 'spawn'
):
    multiprocessing.set_start_method('spawn')

class PlotWindow(QtWidgets.QMdiSubWindow):
    
    WindowHints = QtCore.Qt.CustomizeWindowHint | QtCore.Qt.WindowMinimizeButtonHint | QtCore.Qt.WindowMaximizeButtonHint

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMinimumHeight(256)
        self.setMinimumWidth(256)
        self.FrameState = True
    
    def closeEvent(self, event):
        event.ignore()

    def setFrameState(self, state):
        """
        Enable / disable the frame
        """

        self.FrameState = bool(state)

        if self.FrameState:
            hint = self.WindowHints
        else:
            hint = self.WindowHints | QtCore.Qt.FramelessWindowHint

        self.setWindowFlags(hint) 
        self.update()

def mdiArea_addWindow(mdiArea, widget):
    sub_window = PlotWindow()
    sub_window.setWidget(widget)
    mdiArea.addSubWindow(sub_window)
    
    sub_window.setFrameState(True)
    
    sub_window.show()    

    return sub_window

class Plot(object):
    def __init__(self, figure, identifier, mdiArea_canvas):
        self.identifier = identifier

        # Get the plot window ready
        self.widget = QtWidgets.QWidget()

        loader = UiLoader()
        self.ui = loader.load(os.path.join(lyse.LYSE_DIR, 'plot_window.ui'), self.widget)
        self.subwindow = mdiArea_addWindow(mdiArea_canvas, self.widget)

        self.set_window_title(self.identifier)

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
    def set_window_title(self, identifier):
        self.ui.setWindowTitle(str(identifier))

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
        window_size = self.subwindow.size()
        window_pos = self.subwindow.pos()
        return {
            'window_size': (window_size.width(), window_size.height()),
            'window_pos': (window_pos.x(), window_pos.y()),
            'axis_lock_state': self.lock_axes,
            'axis_limits': self.axis_limits,
            'frame_state': self.subwindow.FrameState
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

        if 'window_size' in state:
            self.subwindow.resize(*state['window_size'])

        if 'window_pos' in state:
            self.subwindow.move(*state['window_pos'])

        if 'frame_state' in state:
            self.subwindow.setFrameState(state['frame_state'])

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
    def __init__(self, filepath, mdiArea_canvas):
        self.filepath = filepath

        # This is a mdi Area where all of the figures will go.
        self.mdiArea_canvas = mdiArea_canvas
        self.mdiArea_canvas.setMinimumHeight(256)

        # Add user script directory to the pythonpath:
        sys.path.insert(0, os.path.dirname(self.filepath))
        
        # Create a module for the user's routine, and insert it into sys.modules as the
        # __main__ module:
        self.routine_module = ModuleType('__main__')
        self.routine_module.__file__ = self.filepath
        # Save the dict so we can reset the module to a clean state later:
        self.routine_module_clean_dict = self.routine_module.__dict__.copy()
        sys.modules[self.routine_module.__name__] = self.routine_module

        # Plot objects, keyed by matplotlib Figure object
        self.plots = {}

        # State of each plot keyed by the window identifier NOT the figure object
        self.window_state = {}

        # An object with a method to unload user modules if any have
        # changed on disk:
        self.modulewatcher = ModuleWatcher()
        
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

        # Use lyse.path instead:
        lyse.path = path
        lyse.plots = self.plots
        lyse.Plot = Plot
        lyse._updated_data = {}
        lyse._delay_flag = False
        lyse.delay_event.clear()

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

        # Introspect the figures that were produced

        for identifier, fig in lyse.figure_manager.figuremanager.figs.items():
            window_state = self.window_state.get(identifier, None)
            if not fig.axes:
                # Try and clear the figure if it is not in use
                try:
                    plot = self.plots[fig]
                    plot.set_window_title("Empty")
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
                cls = lyse.get_plot_class(identifier)
                # If no plot was registered, use the base class
                if cls is None: cls = Plot
                
                # if plot instance does not match the expected identifier,  
                # or the identifier in use with this plot has changes,
                #  we need to close and reopen it!
                if type(plot) != cls or plot.identifier != identifier:
                    window_state = plot.get_window_state()

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
                plot.set_window_title(identifier)
                if plot.lock_axes:
                    plot.restore_axis_limits()
                plot.draw()
            plot.analysis_complete(figure_in_use=True)

    def new_figure(self, fig, identifier):
        try:
            # Get custom class for this plot if it is registered
            cls = lyse.get_plot_class(identifier) # IBS: register_plot_class is not used anywhere.

            # If no plot was registered, use the base class
            if cls is None: cls = Plot
            # if cls is not a subclass of Plot, then raise an Exception
            if not issubclass(cls, Plot): 
                raise RuntimeError('The specified class must be a subclass of lyse.Plot')
            # Instantiate the plot
            self.plots[fig] = cls(fig, identifier, self.mdiArea_canvas)
        except Exception:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            message = """Failed to instantiate custom class for plot "{identifier}".
                Perhaps lyse.register_plot_class() was called incorrectly from your
                script? The exception raised was:
                """.format(identifier=identifier)
            message = lyse.dedent(message) + '\n'.join(traceback_lines[1:])
            message += '\n'
            message += 'Due to this error, we used the default lyse.Plot class instead.\n'
            sys.stderr.write(message)

            # instantiate plot using original Base class so that we always get a plot
            self.plots[fig] = Plot(fig, identifier, self.mdiArea_canvas)

        return self.plots[fig]

    def reset_figs(self):
        pass

    def get_window_state(self):
        return {plot.identifier:plot.get_window_state() for fig, plot in self.plots.items()}

    def set_window_state(self, window_state):
        self.window_state = window_state


class LyseWorkerWindow(QtWidgets.QWidget):

    def __init__(self, app, *args, **kwargs):
        self.app = app

        QtWidgets.QWidget.__init__(self, *args, **kwargs)

    def closeEvent(self, event):
        self.hide()
        event.ignore()

class LyseWorker():
    def __init__(self, process_tree, qapplication):

        # Interprocess communication setup
        self.to_parent = process_tree.to_parent
        self.from_parent = process_tree.from_parent
        self.kill_lock = process_tree.kill_lock

        # File name from parent
        self.filepath = self.from_parent.get()
        file = os.path.splitext(os.path.basename(self.filepath))[0] # file with no path and no extension
        self.title = f"Lyse_analysis_{file}"

        # Config location from parent
        lyse_config_file = self.from_parent.get() # this will be the lyse config file
        self.save_config_file = os.path.join(os.path.dirname(lyse_config_file), f"{file}.ini")

        # Set a meaningful client id for zlock
        process_tree.zlock_client.set_process_name('lyse-'+os.path.basename(self.filepath))

        # GUI setup
        self.qapplication = qapplication

        loader = UiLoader()
        self.ui = loader.load(os.path.join(lyse.LYSE_DIR, 'subprocess_window.ui'), LyseWorkerWindow(self))
        self.ui.setWindowTitle(self.title)
        
        # Create a hidden output box
        self.ui.output_box = OutputBox(self.ui.splitter_bottom)
        self._splitter_sizes = self.ui.splitter_bottom.sizes()
        self.ui.output_box.output_textedit.hide()

        # Setup for output capturing
        sys.stdout = self.ui.output_box
        sys.stderr = self.ui.output_box

        # Connect signals
        self.ui.button_show_terminal.toggled.connect(self.set_terminal_visible)
        self.ui.button_tile_subwindows.clicked.connect(self.tile_subwindows)
        self.ui.button_cascade_subwindows.clicked.connect(self.cascade_subwindows)

        self.worker = AnalysisWorker(self.filepath, self.ui.mdiArea_canvas)

        self.load_configuration()

        # Start the thread that listens for instructions from the parent process:
        self.parentloop_thread = threading.Thread(target=self.parentloop)
        self.parentloop_thread.daemon = True
        self.parentloop_thread.start()           

        self.ui.output_box.write(f'{self.title} started.')
        self.ui.show()

    @inmain_decorator()
    def _show(self):
        self.ui.show()

    def parentloop(self):
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        while True:
            task, data = self.from_parent.get()
            with self.kill_lock:
                if task == 'quit':
                    self.quit()
                elif task == 'analyse':
                    self._show()
                    path = data
                    success = self.worker.do_analysis(path)
                    if success:
                        if lyse._delay_flag:
                            lyse.delay_event.wait()
                        self.to_parent.put(['done', lyse._updated_data])
                    else:
                        self.to_parent.put(['error', lyse._updated_data])
                else:
                    self.to_parent.put(['error','invalid task %s'%str(task)])

    @inmain_decorator()
    def quit(self):
        self.save_configuration()
        self.qapplication.quit()

    @inmain_decorator()
    def get_save_data(self):
        save_data = {}

        window_size = self.ui.size()
        save_data['window_size'] = (window_size.width(), window_size.height())

        window_pos = self.ui.pos()
        save_data['window_pos'] = (window_pos.x(), window_pos.y())

        save_data['screen_geometry'] = lyse.ui_helpers.get_screen_geometry(self.qapplication)

        save_data['button_show_terminal'] = self.ui.button_show_terminal.isChecked()

        # If the terminal is hidden use the cached size
        if self.ui.button_show_terminal.isChecked():
            save_data['splitter_bottom'] = self.ui.splitter_bottom.sizes()
        else:
            save_data['splitter_bottom'] = self._splitter_sizes

        # plot_windows information
        save_data['plot_windows'] = self.worker.get_window_state()

        return save_data

    def save_configuration(self):
        save_data = self.get_save_data()
        save_appconfig(self.save_config_file, {f'{self.title}_state': save_data})

    def load_configuration(self):

        save_data = load_appconfig(self.save_config_file)
        save_data = save_data.get(f'{self.title}_state', {})

        if 'screen_geometry' not in save_data:
            return
        
        screen_geometry = save_data['screen_geometry']

        # Only restore the window size and position, and splitter
        # positions if the screen is the same size/same number of monitors
        # etc. This prevents the window moving off the screen if say, the
        # position was saved when 2 monitors were plugged in but there is
        # only one now, and the splitters may not make sense in light of a
        # different window size, so better to fall back to defaults:

        current_screen_geometry = lyse.ui_helpers.get_screen_geometry(self.qapplication)
        if current_screen_geometry == screen_geometry:
            if 'window_size' in save_data:
                self.ui.resize(*save_data['window_size'])

            if 'window_pos' in save_data:
                self.ui.move(*save_data['window_pos'])

            if 'splitter_bottom' in save_data:
                self._splitter_sizes = save_data['splitter_bottom']
                self.ui.splitter_bottom.setSizes(self._splitter_sizes)

        self.set_terminal_visible(save_data.get('button_show_terminal', False))

        self.worker.set_window_state(save_data.get('plot_windows', {}))

        return save_data

    def cascade_subwindows(self, state):
        """
        Cascade the subwindows 
        """

        # Make sure the windows have frames.
        subwindows = self.ui.mdiArea_canvas.subWindowList()

        for sub in subwindows: 
           sub.setFrameState(True) 

        self.ui.mdiArea_canvas.cascadeSubWindows()

    def tile_subwindows(self, state):
        """
        Tile the subwindows 
        
        Remove the title bar is there is only one window.
        """

        subwindows = self.ui.mdiArea_canvas.subWindowList()

        # The following code removes the title bar if there is one subwindow
        FrameState = len(subwindows) != 1

        for sub in subwindows: 
           sub.setFrameState(FrameState) 

        self.ui.mdiArea_canvas.tileSubWindows()

    def set_terminal_visible(self, visible):
        if visible:
            self.ui.output_box.output_textedit.show()
        else:
            # Store this because we want to remember the splitter position when the terminal is hidden
            self._splitter_sizes = self.ui.splitter_bottom.sizes()

            self.ui.output_box.output_textedit.hide()


        self.ui.button_show_terminal.setChecked(visible)    

if __name__ == '__main__':

    os.environ['MPLBACKEND'] = "qt5agg"

    import lyse
    lyse.spinning_top = True
    import lyse.figure_manager
    lyse.figure_manager.install()

    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
    import pylab
    import labscript_utils.h5_lock, h5py

    process_tree = ProcessTree.connect_to_parent()

    qapplication = QtWidgets.QApplication.instance()
    if qapplication is None:
        qapplication = QtWidgets.QApplication(sys.argv)

    # Rename this module to _analysis_subprocess and put it in sys.modules
    # under that name. The user's analysis routine will become the __main__ module
    # '_analysis_subprocess'.
    __name__ = '_analysis_subprocess'

    sys.modules[__name__] = sys.modules['__main__']

    worker = LyseWorker(process_tree, qapplication)

    qapplication.exec_()