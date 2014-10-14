from __future__ import division, unicode_literals, print_function, absolute_import

import os
import sys
import labscript_utils.excepthook

try:
    # Python 2 or 3, PyQt5:
    from PyQt5 import QtCore, QtWidgets
    from PyQt5 import Signal, Slot
except ImportError:
    if sys.version < '3':
        # Python 2, PyQt4:
        import sip
        API_NAMES = ["QDate", "QDateTime", "QString", "QTextStream", "QTime", "QUrl", "QVariant"]
        API_VERSION = 2
        for name in API_NAMES:
            sip.setapi(name, API_VERSION)
    
        from PyQt4 import QtCore, QtGui as QtWidgets
        from PyQt4.QtCore import pyqtSignal as Signal
        from PyQt4.QtCore import pyqtSlot as Slot
    else:
        # Python 3, PyQt4:
        from PyQt4 import QtCore, QtGui as QtWidgets
        from PyQt4.QtCore import pyqtSignal as Signal
        from PyQt4.QtCore import pyqtSlot as Slot

import signal
signal.signal(signal.SIGINT, signal.SIG_DFL) # Quit on ctrl-c

# Set working directory to runmanager folder, resolving symlinks
lyse_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(lyse_dir)

def set_win_appusermodel(window_id):
    from labscript_utils.winshell import set_appusermodel, appids, app_descriptions
    icon_path = os.path.abspath('lyse.ico')
    executable = sys.executable.lower()
    if not executable.endswith('w.exe'):
        executable = executable.replace('.exe', 'w.exe')
    relaunch_command = executable + ' ' + os.path.abspath(__file__.replace('.pyc', '.py'))
    relaunch_display_name = app_descriptions['lyse']
    set_appusermodel(window_id, appids['lyse'], icon_path, relaunch_command, relaunch_display_name)

    
try:
    from labscript_utils import check_version
except ImportError:
    raise ImportError('Require labscript_utils > 2.1.0')

        
check_version('labscript_utils', '1.1', '2')
check_version('qtutils', '1.5.1', '2')
check_version('zprocess', '1.1.2', '2')


import zprocess.locking
from zmq import ZMQError

from labscript_utils.labconfig import LabConfig, config_prefix
from labscript_utils.setup_logging import setup_logging
import labscript_utils.shared_drive as shared_drive
import lyse

from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager
from qtutils.outputbox import OutputBox
import qtutils.icons

# Set working directory to lyse folder, resolving symlinks
lyse_dir = os.path.dirname(os.path.realpath(__file__))
os.chdir(lyse_dir)

# Set a meaningful name for zprocess.locking's client id:
zprocess.locking.set_client_process_name('lyse')


@inmain_decorator()
def error_dialog(message):
    QtGui.QMessageBox.warning(app.ui, 'lyse', message)

    
@inmain_decorator()
def question_dialog(message):
    reply = QtGui.QMessageBox.question(app.ui, 'lyse', message,
                                       QtGui.QMessageBox.Yes|QtGui.QMessageBox.No)
    return (reply == QtGui.QMessageBox.Yes)
  
  
class LyseMainWindow(QtWidgets.QMainWindow):
    # A signal for when the window manager has created a new window for this widget:
    newWindow = Signal(int)

    def event(self, event):
        result = QtWidgets.QMainWindow.event(self, event)
        if event.type() == QtCore.QEvent.WinIdChange:
            self.newWindow.emit(self.effectiveWinId())
        return result
        
        
class Lyse(object):
    def __init__(self):
        loader = UiLoader()
        # loader.registerCustomWidget(TreeView)
        self.ui = loader.load('main.ui', LyseMainWindow())
        self.output_box = OutputBox(self.ui.verticalLayout_output_box)
        if os.name == 'nt':
            self.ui.newWindow.connect(set_win_appusermodel)
        self.ui.show()
    
    
if __name__ == "__main__":
    logger = setup_logging('lyse')
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    qapplication = QtWidgets.QApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)
    app = Lyse()
    sys.exit(qapplication.exec_())