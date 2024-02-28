#####################################################################
#                                                                   #
# /__main__.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

def first_import():
    """
    This function just imports a bunch of modules.  This causes python to load and cache them so
    they will loaded faster the next time.  We do this here so that we can monitor this with a splash
    window
    """
    import os

    # Splash screen
    from labscript_utils.splash import Splash
    splash = Splash(os.path.join(os.path.dirname(__file__), 'lyse.svg'))
    splash.show()

    splash.update_text('importing standard library modules')
    # stdlib imports
    import sys
    import logging
    import threading
    import signal
    import subprocess
    import time
    import traceback
    import queue
    import warnings

    # 3rd party imports:
    splash.update_text('importing numpy')
    import numpy as np
    splash.update_text('importing h5_lock and h5py')
    import labscript_utils.h5_lock
    import h5py
    splash.update_text('importing pandas')
    import pandas

    splash.update_text('importing labscript suite modules')

    from labscript_utils.ls_zprocess import ZMQServer, ProcessTree
    import zprocess
    from labscript_utils.labconfig import LabConfig, save_appconfig, load_appconfig
    from labscript_utils.setup_logging import setup_logging
    from labscript_utils.qtwidgets.headerview_with_widgets import HorizontalHeaderViewWithWidgets
    from labscript_utils.qtwidgets.outputbox import OutputBox
    import labscript_utils.shared_drive as shared_drive
    from labscript_utils import dedent
    import labscript_utils.excepthook

    splash.update_text('importing qt modules')

    from qtutils.qt import QtCore, QtGui, QtWidgets
    from qtutils.qt.QtCore import pyqtSignal as Signal
    from qtutils import inmain_decorator, inmain, UiLoader, DisconnectContextManager
    from qtutils.auto_scroll_to_end import set_auto_scroll_to_end

    return splash

if __name__ == "__main__":

    # This is the first entry point into the program so we can open the splash 
    # and import things for the first time
    splash = first_import()

    # 
    # Now import only what is needed to start the Lyse application
    # 

    import sys
    import signal
    from qtutils.qt import QtCore, QtWidgets
    import desktop_app
    import lyse.main

    # Associate app windows with OS menu shortcuts:
    desktop_app.set_process_appid('lyse')

    splash.update_text('starting GUI')
    qapplication = QtWidgets.QApplication.instance()
    if qapplication is None:
        qapplication = QtWidgets.QApplication(sys.argv)
    qapplication.setAttribute(QtCore.Qt.AA_DontShowIconsInMenus, False)

    app = lyse.main.Lyse(qapplication)

    # Let the interpreter run every 500ms so it sees Ctrl-C interrupts:
    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)  # Let the interpreter run each 500 ms.
    # Upon seeing a ctrl-c interrupt, quit the event loop
    signal.signal(signal.SIGINT, lambda *args: qapplication.exit())
    
    splash.hide()
    qapplication.exec_()

    # Shutdown the webserver.
    app.server.shutdown()
