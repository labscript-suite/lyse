from __future__ import division, unicode_literals, print_function, absolute_import # Ease the transition to Python 3

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

import labscript_utils.h5_lock, h5py
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
check_version('qtutils', '1.5.1', '2')
check_version('zprocess', '1.1.2', '2')

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
                                       QtGui.QMessageBox.Yes|QtGui.QMessageBox.No)
    return (reply == QtGui.QMessageBox.Yes)
  
  
class WebServer(ZMQServer):

    def handler(self, request_data):
        logger.info('WebServer request: %s'%str(request_data))
        if request_data == 'hello':
            return 'hello'
        elif request_data == 'get dataframe':
            return app.filebox.dataframe
        elif isinstance(request_data, dict):
            if 'filepath' in request_data:
                h5_filepath = labscript_utils.shared_drive.path_to_local(request_data['filepath'])
                app.filebox.incoming_queue.put([h5_filepath])
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

        
class EditColumnsDialog(QtGui.QDialog):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)

    def event(self, event):
        result = QtGui.QDialog.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result

        
class EditColumns(object):

    def __init__(self, filebox, columns=None):
        loader = UiLoader()
        self.ui = loader.load('edit_columns.ui', EditColumnsDialog())
        self.ui.setWindowModality(QtCore.Qt.ApplicationModal)
        self.connect_signals()
        self.ui.show()
        
    def connect_signals(self):
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)
            

class DataFrameModel(object):

    def __init__(self, treeview):
        self._treeview = treeview
        self._model = QtGui.QStandardItemModel()
        # self._header = QtGui.QHeaderView(QtCore.Qt.Horizontal)# HorizontalHeaderViewWithWidgets(self._model)
        self._header = HorizontalHeaderViewWithWidgets(self._model)
        self._treeview.setHeader(self._header)
        self._treeview.setModel(self._model)
        # This dataframe will contain all the scalar data
        # from the run files that are currently open:
        index = pandas.MultiIndex.from_tuples([('filepath', '')])
        self.dataframe = pandas.DataFrame({'filepath':[]}, columns=index)
        self._model.setHorizontalHeaderItem(0, QtGui.QStandardItem('filepath'))
        
    def get_column_names(self):
        return ['\n'.join(item for item in column_name if item) for column_name in self.dataframe.columns]
            
    def add_file(self, filepath):
        if filepath in self.dataframe['filepath'].values:
            # Ignore duplicates:
            return
        column_names_before_insertion = self.get_column_names()
        row = get_dataframe_from_shot(filepath)
        self.dataframe = concat_with_padding(self.dataframe, row)
        
        # Check and create necessary new columns:
        column_names_after_insertion = self.get_column_names()
        new_column_names = set(column_names_after_insertion) - set(column_names_before_insertion)
        new_columns_start = self._model.columnCount()
        self._model.insertColumns(new_columns_start, len(new_column_names))
        for i, column_name in enumerate(new_column_names):
            # Set the header label of the new column:
            column = new_columns_start + i
            item = QtGui.QStandardItem(column_name)
            item.setToolTip(column_name)
            self._model.setHorizontalHeaderItem(column, item)
            self._treeview.setColumnWidth(column, self._header.sectionSizeFromContents(column).width())
    
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
        # loader.registerCustomWidget(TreeView) # unsure if we will be needing this
        self.ui = loader.load('filebox.ui')
        container.addWidget(self.ui)
        
        self.connect_signals()
        
        self.analysis_loop_paused = False
        
        # A condition to let the looping threads know when to recheck conditions
        # they're waiting on (instead of having them do time.sleep)
        self.timing_condition = threading.Condition()
        
        # The folder that the 'add shots' dialog will open to:
        self.current_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        
        # Whether the last scroll to the bottom of the treeview has been processed:
        self.scrolled = True
        
        # A queue for storing incoming files from the ZMQ server so
        # the server can keep receiving files even if analysis is slow
        # or paused:
        self.incoming_queue = Queue.Queue()
        
        self.shots_model = DataFrameModel(self.ui.treeView)
            
        # Start the thread to handle incoming files, and store them in
        # a buffer if processing is paused:
        self.incoming = threading.Thread(target = self.incoming_buffer_loop)
        self.incoming.daemon = True
        self.incoming.start()
        
        #self.analysis = threading.Thread(target = self.analysis_loop)
        #self.analysis.daemon = True
        #self.analysis.start()

        #self.adjustment.set_value(self.adjustment.upper - self.adjustment.page_size)
        
    def connect_signals(self):
        self.ui.pushButton_edit_columns.clicked.connect(self.on_edit_columns_clicked)
        
    def on_edit_columns_clicked(self):
        # visible = {}
        # for column in self.treeview.get_columns():
            # label = column.get_widget()
            # if isinstance(label, gtk.Label):
                # title = label.get_text()
                # visible[title] = column.get_visible()
        self.dialog = EditColumns(self)
        
    def incoming_buffer_loop(self):
        logger = logging.getLogger('LYSE.FileBox.incoming')  
        # HDF5 prints lots of errors by default, for things that aren't
        # actually errors. These are silenced on a per thread basis,
        # and automatically silenced in the main thread when h5py is
        # imported. So we'll silence them in this thread too:
        h5py._errors.silence_errors()
        
        while True:
            filepaths = self.incoming_queue.get()
            logger.info('adding some files')
            self.add_files(filepaths)
            
    @inmain_decorator()
    def add_files(self, filepaths):
        for i, filepath in enumerate(filepaths):
            self.logger.info('adding %s'%filepath)
            self.shots_model.add_file(filepath)
        # with gtk.gdk.lock:
            # self.update_liststore()
        # if self.scrolled:
            # with gtk.gdk.lock:
                # # Are we scrolled to the bottom of the TreeView?
                # if self.adjustment.value == self.adjustment.upper - self.adjustment.page_size:
                    # self.scrolled = False                 
                    # gobject.idle_add(self.scroll_to_bottom)
        # # Let waiting threads know to check for new files:
        # with self.timing_condition:
            # self.timing_condition.notify_all()
        
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
        #self.singleshot_routinebox = RoutineBox(self.ui.verticalLayout_singleshot_routinebox,
        #                                        self, to_singleshot, from_singleshot, self.outputbox.port)
        #self.multishot_routinebox = RoutineBox(self.ui.verticalLayout_multishot_routinebox,
        #                                       self, to_multishot, from_multishot, self.outputbox.port, multishot=True)
        self.filebox = FileBox(self.ui.verticalLayout_filebox,
                               self.exp_config, to_singleshot, from_singleshot, to_multishot, from_multishot)
                               
        self.ui.resize(1600, 900)
        self.ui.show()
        # self.ui.showMaximized()
    
    def setup_config(self):
        config_path = os.path.join(config_prefix, '%s.ini' % socket.gethostname())
        required_config_params = {"DEFAULT":["experiment_name"],
                                  "programs":["text_editor",
                                              "text_editor_arguments",
                                              "hdf5_viewer",
                                              "hdf5_viewer_arguments"],
                                  "paths":["shared_drive",
                                           "experiment_shot_storage",
                                           "analysislib"],
                                  "ports":["lyse"]
                                 }           
        self.exp_config = LabConfig(config_path, required_config_params)
    
    def connect_signals(self):
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)
    
    def destroy(self,*args):
        raise NotImplementedError
        #gtk.main_quit()
        # The routine boxes have subprocesses that need to be quit:
        #self.singleshot_routinebox.destroy()
        #self.multishot_routinebox.destroy()
        #self.server.shutdown()
        
    ##### TESTING ONLY REMOVE IN PRODUCTION
    def submit_dummy_shots(self):
        path = r'C:\Experiments\rb_chip\connectiontable\2014\10\21\20141021T135341_connectiontable_11.h5'
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
    timer.start(500)  # You may change this if you wish.
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())
    qapplication.exec_()