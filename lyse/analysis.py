"""
classes that drive the basic operation of lyse analysis workers
"""

import os
import time

from qtutils.qt import QtCore, QtGui
from qtutils import inmain_decorator, inmain, UiLoader, DisconnectContextManager
import qtutils.icons

import lyse
from lyse.ui_helpers import RoutineBoxData


class ClassicAnalysisRoutine(object):

    filepath_subprocess = 'classic_analysis_subprocess.py'

    def __init__(self, app, filepath, model, output_box_port, checked=QtCore.Qt.Checked):
        self.app = app # Reference to main lyse app

        self.filepath = filepath
        self.shortname = os.path.basename(self.filepath)
        self.model = model
        self.output_box_port = output_box_port
        
        self.COL_ACTIVE = RoutineBoxData.COL_ACTIVE
        self.COL_STATUS = RoutineBoxData.COL_STATUS
        self.COL_NAME = RoutineBoxData.COL_NAME
        self.ROLE_FULLPATH = RoutineBoxData.ROLE_FULLPATH
        
        self.error = False
        self.done = False
        
        self.to_worker, self.from_worker, self.worker = self.start_worker()
        
        # Make a row to put into the model:
        active_item =  QtGui.QStandardItem()
        active_item.setCheckable(True)
        active_item.setCheckState(checked)
        info_item = QtGui.QStandardItem()
        name_item = QtGui.QStandardItem(self.shortname)
        name_item.setToolTip(self.filepath)
        name_item.setData(self.filepath, self.ROLE_FULLPATH)
        self.model.appendRow([active_item, info_item, name_item])
            
        self.exiting = False
        
    def start_worker(self):
        # Start a worker process for this analysis routine:
        worker_path = os.path.join(lyse.LYSE_DIR, self.filepath_subprocess)

        child_handles = self.app.process_tree.subprocess(
            worker_path,
            # output_redirection_port=self.output_box_port, # IBS Change maybe make an option.
            startup_timeout=30,
        )
        
        to_worker, from_worker, worker = child_handles
        
        # Tell the worker what script it with be executing:
        to_worker.put(self.filepath)

        # Tell worker where to save UI data
        to_worker.put(self.app.last_save_config_file)

        return to_worker, from_worker, worker
        
    def do_analysis(self, filepath):
        self.to_worker.put(['analyse', filepath])
        signal, data = self.from_worker.get()
        if signal == 'error':
            return False, data
        elif signal == 'done':
            return True, data
        else:
            raise ValueError('invalid signal %s'%str(signal))
        
    @inmain_decorator()
    def set_status(self, status):
        index = self.get_row_index()
        if index is None:
            # Yelp, we've just been deleted. Nothing to do here.
            return
        status_item = self.model.item(index, self.COL_STATUS)
        if status == 'done':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/tick'))
            self.done = True
            self.error = False
        elif status == 'working':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/hourglass'))
            self.done = False
            self.error = False
        elif status == 'error':
            status_item.setIcon(QtGui.QIcon(':/qtutils/fugue/exclamation'))
            self.error = True
            self.done = False
        elif status == 'clear':
            status_item.setData(None, QtCore.Qt.DecorationRole)
            self.done = False
            self.error = False
        else:
            raise ValueError(status)
        
    @inmain_decorator()
    def enabled(self):
        index = self.get_row_index()
        if index is None:
            # Yelp, we've just been deleted.
            return False
        enabled_item = self.model.item(index, self.COL_ACTIVE)
        return (enabled_item.checkState() == QtCore.Qt.Checked)
        
    def get_row_index(self):
        """Returns the row index for this routine's row in the model"""
        for row in range(self.model.rowCount()):
            name_item = self.model.item(row, self.COL_NAME)
            fullpath = name_item.data(self.ROLE_FULLPATH)
            if fullpath == self.filepath:
                return row

    def restart(self):
        # TODO set status to 'restarting' or an icon or something, and gray out the item?
        self.end_child(restart=True)
        
    def remove(self):
        """End the child process and remove from the treeview"""
        self.end_child()
        index = self.get_row_index()
        if index is None:
            # Already gone
            return
        self.model.removeRow(index)
         
    def end_child(self, restart=False):
        self.to_worker.put(['quit', None])
        timeout_time = time.time() + 2
        self.exiting = True
        QtCore.QTimer.singleShot(50,
            lambda: self.check_child_exited(self.worker, timeout_time, kill=False, restart=restart))

    def check_child_exited(self, worker, timeout_time, kill=False, restart=False):
        worker.poll()
        if worker.returncode is None and time.time() < timeout_time:
            QtCore.QTimer.singleShot(50,
                lambda: self.check_child_exited(worker, timeout_time, kill, restart))
            return
        elif worker.returncode is None:
            if not kill:
                worker.terminate()
                self.app.output_box.output('%s worker not responding.\n'%self.shortname)
                timeout_time = time.time() + 2
                QtCore.QTimer.singleShot(50,
                    lambda: self.check_child_exited(worker, timeout_time, kill=True, restart=restart))
                return
            else:
                worker.kill()
                self.app.output_box.output('%s worker killed\n'%self.shortname, red=True)
        elif kill:
            self.app.output_box.output('%s worker terminated\n'%self.shortname, red=True)
        else:
            self.app.output_box.output('%s worker exited cleanly\n'%self.shortname)
        
        # if analysis was running notify analysisloop that analysis has failed
        self.from_worker.put(('error', {}))

        if restart:
            self.to_worker, self.from_worker, self.worker = self.start_worker()
            self.app.output_box.output('%s worker restarted\n'%self.shortname)
        self.exiting = False
