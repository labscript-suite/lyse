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

class PlotGUI():
    pass