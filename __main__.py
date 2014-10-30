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

import numpy as np
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

# debug import, can be removed in production
import bprofile

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
  

def scientific_notation(x, sigfigs = 4):
    """Returns a unicode string of the float f in scientific notation"""
    if not isinstance(x, float):
        raise TypeError('x must be floating point number')
    if np.isnan(x) or np.isinf(x):
        return str(x)
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
    
    if x != 0:
        try:
            exponent = int(np.floor(np.log10(np.abs(x))))
            # Only multiples of 10^3
            exponent = int(np.floor(exponent/3)*3)
        except Exception:
            import IPython
            IPython.embed()
    else:
        exponent = 0
    significand = x/10**exponent
    pre_decimal, post_decimal = divmod(significand, 1)
    digits = sigfigs - len(str(int(pre_decimal)))
    significand = round(significand, digits)
    result =  str(significand)
    if exponent:
        superscript = ''.join(sups.get(char, char) for char in str(exponent))
        result += thinspace + times + thinspace + '10' + superscript
    return result
    
    
class WebServer(ZMQServer):

    def handler(self, request_data):
        logger.info('WebServer request: %s'%str(request_data))
        if request_data == 'hello':
            return 'hello'
        elif request_data == 'get dataframe':
            return app.filebox.shots_model.dataframe
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
            
            
class ItemDelegate(QtGui.QStyledItemDelegate):
    """An item delegate with a fixed height"""
    EXTRA_ROW_HEIGHT = 7

    def __init__(self, view, *args, **kwargs):
        self.view = view
        QtGui.QStyledItemDelegate.__init__(self, *args, **kwargs)
        
    def sizeHint(self, *args):
        fontmetrics = QtGui.QFontMetrics(self.view.font())
        text_height = fontmetrics.height()
        row_height = text_height + self.EXTRA_ROW_HEIGHT
        size = QtGui.QStyledItemDelegate.sizeHint(self, *args)
        return QtCore.QSize(size.width(), row_height)

        
class Model(QtGui.QStandardItemModel):
    def flags(self, index):
        """Return flags as normal except that the ItemIsEditable
        flag is always False"""
        result =  QtGui.QStandardItemModel.flags(self, index)
        return result & ~QtCore.Qt.ItemIsEditable
        
    
class DataFrameModel(object):

    def __init__(self, view):
        self._view = view
        self._model = Model()
        #self._header = HorizontalHeaderViewWithWidgets(self._model)
        self._vertheader = QtGui.QHeaderView(QtCore.Qt.Vertical)
        
        self._vertheader.setResizeMode(QtGui.QHeaderView.Fixed)
        self._header = QtGui.QHeaderView(QtCore.Qt.Horizontal)
        
        # Set a smaller font size on the headers:
        self._header.setStyleSheet("QHeaderView { font-size: 8pt; color: black;}")
        self._vertheader.setStyleSheet("QHeaderView { font-size: 8pt; color: black;}")
        self._vertheader.setHighlightSections(False)
        self._view.setModel(self._model)
        self._view.setHorizontalHeader(self._header)
        self._view.setVerticalHeader(self._vertheader)
        self._delegate = ItemDelegate(self._view)
        self._view.setItemDelegate(self._delegate)
        self._view.setSelectionBehavior(QtGui.QTableView.SelectRows)
        
        # This dataframe will contain all the scalar data
        # from the shot files that are currently open:
        index = pandas.MultiIndex.from_tuples([('filepath', '')])
        self.dataframe = pandas.DataFrame({'filepath':[]}, columns=index)
        
        active_item = QtGui.QStandardItem('active')
        active_item.setToolTip('whether or not single-shot analysis routines will be run on each shot')
        self._model.setHorizontalHeaderItem(0, active_item)
        
        status_item = QtGui.QStandardItem()
        status_item.setIcon(QtGui.QIcon(':qtutils/fugue/information'))
        status_item.setToolTip('status/progress of single-shot analysis')
        self._model.setHorizontalHeaderItem(1, status_item)
        
        filepath_item = QtGui.QStandardItem('filepath')
        filepath_item.setToolTip('filepath')
        self._model.setHorizontalHeaderItem(2, filepath_item)
        
        self._view.resizeColumnToContents(0)
        self._view.setColumnWidth(1, 100)
        self._view.setColumnWidth(2, 100)
        
        # Column indices to names and vice versa for fast lookup:
        self.column_indices = {'__active' : 0, '__status': 1, 'filepath': 2}
        self.column_names = {0: '__active', 1: '__status', 2: 'filepath'}
        
    def get_model_row_by_filepath(self, filepath):
        print(self.column_indices['filepath'])
        possible_items = self._model.findItems(filepath, column=self.column_indices['filepath'])
        if len(possible_items) > 1:
            raise LookupError('Multiple items found')
        elif not possible_items:
            raise LookupError('No item found')
        item = possible_items[0]
        index = item.index()
        return index.row()
        
    def get_df_column_names(self):
        return ['\n'.join(item for item in column_name if item) for column_name in self.dataframe.columns]
            
    def update_row(self, filepath, dataframe_already_updated=False):
        """"Updates a row in the dataframe and Qt model
        to the data in the HDF5 file for that shot"""
        # Update the row in the dataframe first:
        index = np.where(self.dataframe['filepath'].values == filepath)
        index = index[0][0]
        if not dataframe_already_updated:
            new_row_data = get_dataframe_from_shot(filepath)
            self.dataframe = replace_with_padding(self.dataframe, new_row_data, index) 
            
        # Check and create necessary new columns in the Qt model:
        new_column_names = set(self.get_df_column_names()) - set(self.column_names.values())
        new_columns_start = self._model.columnCount()
        self._model.insertColumns(new_columns_start, len(new_column_names))
        for i, column_name in enumerate(sorted(new_column_names)):
            # Set the header label of the new column:
            column_number = new_columns_start + i
            self.column_names[column_number] = column_name
            self.column_indices[column_name] = column_number
            header_item = QtGui.QStandardItem(column_name)
            header_item.setToolTip(column_name)
            self._model.setHorizontalHeaderItem(column_number, header_item)
            # self._view.setColumnWidth(column_number, self._header.sectionSizeFromContents(column_number).width())
            # self._view.setColumnWidth(column_number, 100)
            #self._view.resizeColumnToContents(column_number)
            
        # Update the data in the Qt model:
        model_row_number = self.get_model_row_by_filepath(filepath)
        for column_number, column_name in self.column_names.items():
            if column_name.startswith('__'):
                # One of our special columns, does not correspond to a column in the dataframe:
                continue
            item = self._model.item(model_row_number, column_number)
            if item is None:
                # This is the first time we've written a value to this part of the model:
                item = QtGui.QStandardItem()
                self._model.setItem(model_row_number, column_number, item)
            value = self.dataframe[column_name].values[index][0]
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
        
    def new_row(self, filepath):
        
        active_item = QtGui.QStandardItem()
        active_item.setCheckable(True)
        
        status_item = QtGui.QStandardItem()
        
        name_item = QtGui.QStandardItem(filepath)
        
        return [active_item, status_item, name_item] 
        
    
    @bprofile.BProfile('add_file.png')
    def add_file(self, filepath):
        if filepath in self.dataframe['filepath'].values:
            # Ignore duplicates:
            return
        # Add the new row to the model:
        self._model.appendRow(self.new_row(filepath))
        self._view.resizeRowToContents(self._model.rowCount()-1)
        # Add the new row to the dataframe.
        new_row_data = get_dataframe_from_shot(filepath)
        self.dataframe = concat_with_padding(self.dataframe, new_row_data)
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
        
        self.shots_model = DataFrameModel(self.ui.tableView)
            
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
    timer.start(500)
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())
    # Do not run qapplication.exec_() whilst waiting for keyboard input if
    # we hop into interactive mode.
    QtCore.pyqtRemoveInputHook()
    
    qapplication.exec_()
    server.shutdown()