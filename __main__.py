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

def check_version(module_name, at_least, less_than, version=None):

    class VersionException(Exception):
        pass
        
    def get_version_tuple(version_string):
        version_tuple = [int(v.replace('+','-').split('-')[0]) for v in version_string.split('.')]
        while len(version_tuple) < 3: version_tuple += (0,)
        return version_tuple
    
    if version is None: version = __import__(module_name).__version__
    at_least_tuple, less_than_tuple, version_tuple = [get_version_tuple(v) for v in [at_least, less_than, version]]
    if not at_least_tuple <= version_tuple < less_than_tuple:
        raise VersionException('{module_name} {version} found. {at_least} <= {module_name} < {less_than} required.'.format(**locals()))

check_version('labscript_utils', '1.1', '2')
check_version('qtutils', '1.1', '2')
check_version('zprocess', '1.1.2', '2')

import zprocess.locking
from zmq import ZMQError

from labscript_utils.labconfig import LabConfig, config_prefix
from labscript_utils.setup_logging import setup_logging
import labscript_utils.shared_drive as shared_drive
import lyse

from qtutils import inmain, inmain_later, inmain_decorator, UiLoader, inthread, DisconnectContextManager, qstring_to_unicode
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
  
  
class Lyse(object):
    pass
    
    
if __name__ == "__main__":
    logger = setup_logging('lyse')
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    qapplication = QtWidgets.QApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)
    app = Lyse()
    sys.exit(qapplication.exec_())