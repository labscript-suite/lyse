import sys
import threading
import traceback

import gtk
import gobject
from matplotlib.backends.backend_gtkagg import FigureCanvasGTKAgg as FigureCanvas
from matplotlib.backends.backend_gtkagg import NavigationToolbar2GTKAgg as NavigationToolbar
import pylab

import excepthook
import subproc_utils


class OutputInterceptor(object):

    def __init__(self, queue, streamname='stdout'):
        self.queue = queue
        self.streamname = streamname
        self.real_stream = getattr(sys,streamname)
        self.fileno = self.real_stream.fileno
        self.readline = self.real_stream.readline
        self.flush = self.real_stream.flush
        
    def connect(self):
        setattr(sys,self.streamname,self)
    
    def disconnect(self):
        setattr(sys,self.streamname,self.real_stream)
            
    def write(self, s):
        self.queue.put([self.streamname, s])
        self.real_stream.write(s)
        
    def close(self):
        self.disconnect()
        sys.stdout.close()
        
        
class AnalysisWorker(object):
    def __init__(self, filepath, to_parent, from_parent):
        self.to_parent = to_parent
        self.from_parent = from_parent
        self.filepath = filepath
        
        # Replacement stdout and stderr to redirect the output of the
        # users code to the textview in the main app:
        self.stdout = OutputInterceptor(self.to_parent)
        self.stderr = OutputInterceptor(self.to_parent,'stderr')
        
        # Keeping track of figures and canvases:
        self.figures = []
        self.canvases = []
        
        # Whether or not to autoscale each figure with new data:
        self.autoscaling = {}
        
        # Start the thread that listens for instructions from the
        # parent process:
        self.mainloop_thread = threading.Thread(target=self.mainloop)
        self.mainloop_thread.daemon = True
        self.mainloop_thread.start()
        
    def mainloop(self):
        print 'worker: mainloop starting'
        while True:
            print 'worker: waiting for next task'
            task, data = self.from_parent.get()
            print 'worker: got task', task
            if task == 'quit':
                break
            elif task == 'reset figs':
                self.reset_figs()
            elif task == 'single' or task == 'multi':
                try:
                    print 'worker: calling do_analysis'
                    self.do_analysis(task,data)
                    print 'worker: finished analysis'
                    self.to_parent.put(['done',None])
                except:
                    print 'worker: there was an exception'
                    traceback_lines = traceback.format_exception(sys.exc_type, sys.exc_value, sys.exc_traceback)
                    del traceback_lines[1:3]
                    message = ''.join(traceback_lines)
                    self.to_parent.put(['stderr',message])
                    self.to_parent.put(['error', message])
            else:
                self.to_parent.put(['error','invalid task %s'%str(task)])
        print 'worker: broke out of loop!'
        
    def do_analysis(self,task,path):
        print 'worker: in do_analysis'
        axis_limits = {}
        with gtk.gdk.lock:
            print 'worker: acquired gtk lock'
            for f in self.figures:
                for i, a in enumerate(f.axes):
                    # Save the limits of the axes to restore them afterward:
                    axis_limits[f,i] = a.get_xlim(), a.get_ylim()
                f.clear()
        # The namespace the routine will run in:
        sandbox = {'path':path,'__file__':self.filepath}
        # Tell pylab to set the current figure to no. 1:
        pylab.figure(1)
        # Connect the output redirection:
        self.stdout.connect()
        self.stderr.connect()
        try:
            # Actually run the user's analysis!
            execfile(self.filepath,sandbox,sandbox)
        finally:
            # Disconnect output redirection:
            self.stdout.disconnect()
            self.stderr.disconnect()
        
        # Introspect the figures that were produced:
        i = 1
        while True:
            fig = pylab.figure(i)
            if not fig.axes:
                break
            elif not fig in self.figures:
                # If we don't already have this figure, make a window
                # to put it in:
                gobject.idle_add(self.new_figure,fig)
            elif not self.autoscaling[fig].get_active():
                with gtk.gdk.lock:
                    # Restore the axis limits:
                    for j, a in enumerate(fig.axes):
                        try:
                            xlim, ylim = axis_limits[fig,j]
                            a.set_xlim(xlim)
                            a.set_ylim(ylim)
                        except KeyError:
                            continue
            i += 1
        
        # Redraw all figures:
        with gtk.gdk.lock:
            for canvas in self.canvases:
                canvas.draw_idle()
    
    def new_figure(self, fig):
        window = gtk.Window()
        l, w = fig.get_size_inches()
        window.resize(int(l*100),int(w*100))
        c = FigureCanvas(fig)
        v = gtk.VBox()
        n = NavigationToolbar(c,window)
        b = gtk.ToggleButton('Autoscale')
        v.pack_start(b,False,False)
        v.pack_start(c)
        v.pack_start(n,False,False)
        window.add(v)
        window.show_all()
        window.present()
        self.canvases.append(c)
        self.figures.append(fig)
        self.autoscaling[fig] = b
        
    def reset_figs(self):
        pass
        
        
if __name__ == '__main__':
    gtk.threads_init()
    to_parent, from_parent = subproc_utils.setup_connection_with_parent()
    filepath = from_parent.get()
    worker = AnalysisWorker(filepath, to_parent, from_parent)
    with gtk.gdk.lock:
        gtk.main()
