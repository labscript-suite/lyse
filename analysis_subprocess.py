import lyse
lyse.spinning_top = True
import lyse.figure_manager

import sys
import os
import threading
import traceback
import time

import gtk
import gobject
from matplotlib.backends.backend_gtkagg import FigureCanvasGTKAgg as FigureCanvas
from matplotlib.backends.backend_gtkagg import NavigationToolbar2GTKAgg as NavigationToolbar
import pylab

import excepthook
import subproc_utils

if not sys.stdout.isatty():
    # Prevent bug on windows where writing to stdout without a command
    # window causes a crash:
    sys.stdout = sys.stderr = open(os.devnull,'w')

if os.name == 'nt':
    # Make it not look so terrible (if icons and themes are installed):
    settings = gtk.settings_get_default()
    settings.set_string_property('gtk-icon-theme-name', 'gnome-human', '')
    settings.set_string_property('gtk-theme-name', 'Clearlooks', '')
    settings.set_string_property('gtk-font-name', 'ubuntu 9', '')
    # Have Windows 7 consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.lyse.1-0' # arbitrary string
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass
                
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
        

class ModuleWatcher(object):
     def __init__(self,stderr):
         # The whitelist is the list of names of currently loaded modules:
         self.whitelist = set(sys.modules)
         self.modified_times = {}
         self.main = threading.Thread(target=self.mainloop)
         self.main.daemon = True
         self.stderr = stderr
         self.main.start()

         
     def mainloop(self):
         while True:
             time.sleep(1)
             self.check_and_unload()
             
     def check_and_unload(self):
         # Look through currently loaded modules:
         for name, module in sys.modules.items():
            # Look only at the modules not in the the whitelist:
            if name not in self.whitelist and hasattr(module,'__file__'):
                # Only consider modules which are .py files, no C extensions:
                module_file = module.__file__.replace('.pyc', '.py')
                if not module_file.endswith('.py') or not os.path.exists(module_file):
                    continue
                # Check and store the modified time of the .py file:
                modified_time = os.path.getmtime(module_file)
                previous_modified_time = self.modified_times.setdefault(name, modified_time)
                self.modified_times[name] = modified_time
                if modified_time != previous_modified_time:
                    # A module has been modified! Unload all modules
                    # not in the whitelist:
                    self.stderr.write('%s modified: all modules will be reloaded next run.\n'%module_file)
                    for name in sys.modules.copy():
                        if name not in self.whitelist:
                            # This unloads a module. This is slightly
                            # more general than reload(module), but
                            # has the same caveats regarding existing
                            # references. This also means that any
                            # exception in the import will occur later,
                            # once the module is (re)imported, rather
                            # than now where catching the exception
                            # would have to be handled differently.
                            del sys.modules[name]
                            if name in self.modified_times:
                                del self.modified_times[name]
        
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
        
        # An object with a method to unload user modules if any have
        # changed on disk:
        self.modulewatcher = ModuleWatcher(self.stderr)
        
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
            with kill_lock:
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
        for identifier, fig in lyse.figure_manager.figuremanager.figs.items():
            if not fig.axes:
                continue
            elif not fig in self.figures:
                # If we don't already have this figure, make a window
                # to put it in:
                gobject.idle_add(self.new_figure,fig,identifier)
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
    
    def new_figure(self, fig, identifier):
        window = gtk.Window()
        window.set_title(str(identifier) + ' - ' + os.path.basename(self.filepath))
        l, w = fig.get_size_inches()
        window.resize(int(l*100),int(w*100))
        window.set_icon_from_file('lyse.svg')
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
    to_parent, from_parent, kill_lock = subproc_utils.setup_connection_with_parent(lock = True)
    filepath = from_parent.get()
    worker = AnalysisWorker(filepath, to_parent, from_parent)
    with gtk.gdk.lock:
        gtk.main()
