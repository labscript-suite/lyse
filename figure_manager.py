#####################################################################
#                                                                   #
# /figure_manager.py                                                #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

from __future__ import division, unicode_literals, print_function, absolute_import
from labscript_utils import PY2
if PY2:
    str = unicode
    
import lyse
import sys

class FigureManager(object):

    def __init__(self):
        self.figs = {}
        self._figure = matplotlib.pyplot.figure
        self._close = matplotlib.pyplot.close
        self._show = matplotlib.pyplot.show
        
    def get_first_empty_figure(self,*args,**kwargs):
        i = 1
        while True:
            fig = self._figure(i,*args,**kwargs)
            if not fig.axes:
                return i, fig
            i += 1
            
    def set_first_figure_current(self):
        self._figure(1)
                
    def __call__(self,identifier=None, *args, **kwargs):
        if identifier is None:
            number, fig =  self.get_first_empty_figure(*args,**kwargs)
            self.figs[number] = fig
        elif identifier in self.figs:
            fig = self.figs[identifier]
            self._figure(fig.number)
        else:
            number, fig =  self.get_first_empty_figure(*args,**kwargs)
            self.figs[identifier] = fig
        return fig

    def close(self,identifier=None):
        if identifier is None:
            thisfig = matplotlib.pyplot.gcf()
            for key, fig in self.figs.items():
                if fig is thisfig:
                    del self.figs[key]
                    self._close()
        elif isinstance(identifier,matplotlib.figure.Figure):
            thisfig = identifier
            for key, fig in self.figs.items():
                if fig is thisfig:
                    del self.figs[fig]
                    self._close(thisfig)
        elif identifier == 'all':
            self.figs = {}
            self._close('all')
        else:
            fig = self.figs[identifier]
            self._close(fig.number)
            del self.figs[identifier]
            
    def show(self):
        if lyse.spinning_top:
            pass # supress show()
        else:
            self._show()


figuremanager = None
matplotlib = None

def install():
    if 'matplotlib.pyplot' in sys.modules:
        message = ('install() must be imported prior to importing pylab/pyplot ' +
                   'in order to correctly override the figure() function.')
        raise RuntimeError(message)

    global matplotlib
    global figuremanager
    import matplotlib.pyplot
    import matplotlib.figure

    figuremanager = FigureManager()
    matplotlib.pyplot.figure = figuremanager
    matplotlib.pyplot.close = figuremanager.close
    matplotlib.pyplot.show = figuremanager.show
