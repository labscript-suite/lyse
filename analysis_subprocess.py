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
import zprocess
to_parent, from_parent, kill_lock = zprocess.setup_connection_with_parent(lock = True)

import sys
import os
import threading
import traceback
import time

import sip
# Have to set PyQt API via sip before importing PyQt:
API_NAMES = ["QDate", "QDateTime", "QString", "QTextStream", "QTime", "QUrl", "QVariant"]
API_VERSION = 2
for name in API_NAMES:
    sip.setapi(name, API_VERSION)

from PyQt4 import QtCore, QtGui
from PyQt4.QtCore import pyqtSignal as Signal
from PyQt4.QtCore import pyqtSlot as Slot

import matplotlib
matplotlib.use("QT4Agg")

import lyse
lyse.spinning_top = True
import lyse.figure_manager
lyse.figure_manager.install()

from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt4agg import NavigationToolbar2QTAgg as NavigationToolbar
import pylab
import zprocess.locking, labscript_utils.h5_lock, h5py

import zprocess
from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager

from labscript_utils.modulewatcher import ModuleWatcher


def set_win_appusermodel(window_id):
    from labscript_utils.winshell import set_appusermodel, appids, app_descriptions
    icon_path = os.path.abspath('lyse.ico')
    executable = sys.executable.lower()
    if not executable.endswith('w.exe'):
        executable = executable.replace('.exe', 'w.exe')
    relaunch_command = executable + ' ' + os.path.abspath(__file__.replace('.pyc', '.py'))
    relaunch_display_name = app_descriptions['lyse']
    set_appusermodel(window_id, appids['lyse'], icon_path, relaunch_command, relaunch_display_name)
    
    
class PlotWindow(QtGui.QDialog):
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
        
        
class AnalysisWorker(object):
    def __init__(self, filepath, to_parent, from_parent):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.filepath = filepath
        
        # Add user script directory to the pythonpath:
        sys.path.insert(0, os.path.dirname(self.filepath))
        
        # Keeping track of figures and canvases:
        self.figures = []
        self.canvases = []
        self.windows = {}
        
        # Whether or not to autoscale each figure with new data:
        self.autoscaling = {}
        
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
                        self.to_parent.put(['done', None])
                    else:
                        self.to_parent.put(['error', None])
                else:
                    self.to_parent.put(['error','invalid task %s'%str(task)])
        
    @inmain_decorator()
    def do_analysis(self, path):
        now = time.strftime('[%x %X]')
        if path is not None:
            print('%s %s %s ' %(now, os.path.basename(self.filepath), os.path.basename(path)))
        else:
            print('%s %s' %(now, os.path.basename(self.filepath)))
        axis_limits = {}
        for f in self.figures:
            for i, a in enumerate(f.axes):
                # Save the limits of the axes to restore them afterward:
                axis_limits[f,i] = a.get_xlim(), a.get_ylim()
            f.clear()
        # The namespace the routine will run in:
        sandbox = {'path':path, '__file__':self.filepath, '__name__':'__main__', '__file__': self.filepath}
        # Do not let the modulewatcher unload any modules whilst we're working:
        try:
            with self.modulewatcher.lock:
                # Actually run the user's analysis!
                execfile(self.filepath, sandbox, sandbox)
        except:
            traceback_lines = traceback.format_exception(*sys.exc_info())
            del traceback_lines[1]
            message = ''.join(traceback_lines)
            sys.stderr.write(message)
            return False
        else:
            return True
        finally:
            print('')
            # reset the current figure to figure 1:
            lyse.figure_manager.figuremanager.set_first_figure_current()
            # Introspect the figures that were produced:
            for identifier, fig in lyse.figure_manager.figuremanager.figs.items():
                if not fig.axes:
                    continue
                elif not fig in self.figures:
                    # If we don't already have this figure, make a window
                    # to put it in:
                    self.new_figure(fig, identifier)
                else:
                    self.update_window_title(self.windows[fig], identifier)
                    if not self.autoscaling[fig].get_active():
                        # Restore the axis limits:
                        for j, a in enumerate(fig.axes):
                            a.autoscale(enable=False)
                            try:
                                xlim, ylim = axis_limits[fig,j]
                                a.set_xlim(xlim)
                                a.set_ylim(ylim)
                            except KeyError:
                                continue
                    else:
                        for j, a in enumerate(fig.axes):
                            a.autoscale(enable=True)
                    
            # Redraw all figures:
            for canvas in self.canvases:
                canvas.draw()
        
                
    def update_window_title(self, window, identifier):
        window.setWindowTitle(str(identifier) + ' - ' + os.path.basename(self.filepath))
        
    def new_figure(self, fig, identifier):
        window = QtGui.QDialog()
        self.update_window_title(window, identifier)
        l, w = fig.get_size_inches()
        dpi = fig.get_dpi()
        window.resize(int(l*dpi),int(w*dpi))
        # window.set_icon_from_file('lyse.svg')
        # c = FigureCanvas(fig)
        # v = gtk.VBox()
        # n = NavigationToolbar(c,window)
        # b = gtk.ToggleButton('Autoscale')
        # v.pack_start(b,False,False)
        # v.pack_start(c)
        # v.pack_start(n,False,False)
        # window.add(v)
        # window.show_all()
        # window.present()
        # self.canvases.append(c)
        # self.figures.append(fig)
        # self.autoscaling[fig] = b
        # self.windows[fig] = window
        
    def reset_figs(self):
        pass
        
        
if __name__ == '__main__':
    filepath = from_parent.get()
    
    # Set a meaningful client id for zprocess.locking:
    zprocess.locking.set_client_process_name('lyse-'+os.path.basename(filepath))
    
    qapplication = QtGui.QApplication(sys.argv)
    worker = AnalysisWorker(filepath, to_parent, from_parent)
    qapplication.exec_()
        
