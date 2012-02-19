import Queue
import threading
import subproc_utils
import os
import time
import traceback
import gtk
import gobject
import excepthook

# constants for which columns of the liststore correspond to what:
FILE = 0
ENABLE = 1
SHOW_PLOTS = 2
MULTIPLE_PLOTS = 3
WORKING = 4
ERROR = 5
SUCCESS = 6
PULSE = 7
INCONSISTENT = 8
TOOLTIP = 9
SENSITIVE = 10

N_COLS = 11

class AnalysisRoutine(object):

    def __init__(self,filepath, routinebox, restart=False):
        # The path to the python script that does the analysis:
        self.filepath = filepath
        self.routinebox = routinebox
        
        self.to_outputbox = self.routinebox.to_outputbox
        
        self.pulse_timeout = 0
        
        objs = subproc_utils.subprocess_with_queues('analysis_subprocess.py')
        # Two queues for communicatng with the worker process for this
        # routine, The worker process itself (a subprocess.Popen object),
        # and a multiprocessing.Manager for sharing the queues between
        # processes:
        self.to_worker, self.from_worker, self.worker, self.manager = objs

        # The reason why I'm managing IPC with queues and a manager
        # instead of instantiating a multiprocessing.Process right
        # here is that *too much* data would be shared if I were
        # to do that. Specifically, gtk threads would fail in the
        # subprocess. This isn't a surprise, the subprocess is created
        # with a fork(), and a GUI API doesn't really expect to be
        # forked. So by doing things this way, our subprocess runs in a
        # clean environment. Note that on windows this isn't a problem
        # since there is no fork(), and subprocess creation happens
        # by the child process importing its parent python script as
        # a module.  However I want this program to be cross platform,
        # and wrapping sections of code in if __name__ == '__main__'
        # to prevent the subprocess re-running its parents code was
        # always ugly anyway. This seems to be a good solution, and
        # still stays within the standard library.

        # Tell the worker what script it with be executing:
        self.to_worker.put(self.filepath)
        
        # A queue so that the listener loop can communicate to the
        # do_analysis function when it gets word from the worker that
        # analysis has completed (or not completed, in the case of
        # an exception)
        self.from_listener = Queue.Queue()
                
        # Start the thread to listen for responses from the worker:
        self.listener_thread = threading.Thread(target=self.listener_loop)
        self.listener_thread.daemon = True
        self.listener_thread.start()
        
        # Make a row to put into the liststore!
        row = [0]*N_COLS
        row[FILE] = os.path.basename(self.filepath)
        row[ENABLE] = True
        row[SHOW_PLOTS] = True
        row[MULTIPLE_PLOTS] = False
        row[WORKING] = False
        row[ERROR] = False
        row[SUCCESS] = False
        row[PULSE] = 0
        row[INCONSISTENT] = False
        row[TOOLTIP] = self.filepath
        row[SENSITIVE] = True
        if not restart:
            # Insert a new row at the bottom of the list:
            self.index = self.routinebox.liststore.iter_n_children(None)
            self.routinebox.liststore.append(row)
        else:
            # Change the values of the existing row:
            iter = self.routinebox.liststore.get_iter(self.index)
            for i, value in enumerate(row):
                self.routinebox.liststore.set(iter,i,value)
    
    def destroy(self):
        print 'destroy happening!'
        # Kill the listener thread:
        self.from_worker.put(['quit',None])
        # Kill the worker process:
        self.worker.terminate()
        # Stop the timeout attempting to update the spinner:
        if self.pulse_timeout is not None:
            gobject.source_remove(self.pulse_timeout)
    
    def restart(self):
        self.destroy()
        self.__init__(self.filepath, self.routinebox, restart=True)
                
    def listener_loop(self):
        # A rename just to make it clear that whilst we're putting items
        # in this queue here, they come out in the do_analysis function:
        to_do_analysis_function = self.from_listener
        
        print 'listener_loop: starting'
        # Process data coming back from the worker:
        while True:
            signal, data = self.from_worker.get()
            print 'listener_loop: got signal from worker'
            if signal == 'quit':
                break
            elif signal == 'figure closed':
                figures_visible, total_figures = data
                self.change_figure_visibility(figures_visible, total_figures)
            elif signal in ['done','error']:
                # This signal is meant for the do_analysis function. Pass
                # it on:
                to_do_analysis_function.put([signal, data])
            elif signal in ['stdout', 'stderr']:
                # Forward output to the outputbox:
                self.to_outputbox.put([signal, data])
        
    def do_analysis(self,task,data):
        with gtk.gdk.lock:
            self.routinebox.liststore[self.index][WORKING] = True
            self.pulse_timeout = gobject.timeout_add(100,self.pulse)
        # We don't even care if it's single shot or multi shot. Pass
        # the task and data directly to the worker process and it will
        # work it out:
        self.to_worker.put([task,data])
        print 'do_analysis: asked for task to be done'
        # This data comes not directly from the worker but via the
        # listener_loop thread. This is because that thread has to be
        # listening for communication from the worker all the time,
        # whereas this function is only running some of the time.
        try:
            signal, data = self.from_listener.get()
        except EOFError:
            # Child has been killed, one way or another.
            with gtk.gdk.lock:
                self.routinebox.liststore[self.index][ERROR] = True
                self.routinebox.liststore[self.index][WORKING] = False  
            return False
        print 'do_analysis: got response from worker'
        with gtk.gdk.lock:
            gobject.source_remove(self.pulse_timeout)
        if signal == 'error':
            with gtk.gdk.lock:
                self.routinebox.liststore[self.index][ERROR] = True
                self.routinebox.liststore[self.index][WORKING] = False  
            return False
        with gtk.gdk.lock:
            self.routinebox.liststore[self.index][SUCCESS] = True
            self.routinebox.liststore[self.index][WORKING] = False
        return True
    
    def pulse(self):
        self.routinebox.liststore[self.index][PULSE] += 1
        return True
        

