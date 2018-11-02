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
from collections import OrderedDict
import sys

class FigureManager(object):

    def __init__(self):
        self.figs = OrderedDict()
        self._figure = matplotlib.pyplot.figure
        self._close = matplotlib.pyplot.close
        self._show = matplotlib.pyplot.show
        self.__allocated_figures = []

    def get_first_empty_figure(self, identifier, *args, **kwargs):
        i = 1
        while True:
            # skip over protected figures that have been allocated to a specific identifier
            if i in self.__allocated_figures:
                i += 1
                continue
            fig = self._figure(i,*args,**kwargs)
            if not fig.axes:
                # only protect the figure if it has an explicit identifier
                # (this stops "figure();figure();"" from generating multiple)
                # empty figures
                if identifier is not None:
                    self.__allocated_figures.append(i)
                return i, fig
            i += 1
            
    def set_first_figure_current(self):
        # only do this if we have any figures at all
        # If we don't, we don't want to create one as that sets the size of the
        # window and then figures sizes are ignored by subsequent runs of the script
        if len(matplotlib.pyplot.get_fignums()) == 0:
            return 
            
        identifier = 1
        fig = self._figure(identifier)
        if fig not in self.figs.values():
            self.figs[identifier] = fig
        elif identifier not in self.__allocated_figures:
            # handle case where we are swapping from all identified figures
            # to the first not being explicitly identified through a call to figure()
            # AND we already had a figure with the identifier of "1" which is what we use
            # for the default figure
            if identifier in self.figs and self.figs[identifier] != fig:
                j = identifier
                while j in self.figs:
                    j += 1
                self.figs[j] = self.figs[identifier]
                msg = """Warning: detected collision of matplotlib figure identifiers.
                    Plot output may not be as expected. 
                    Re-run the analysis script to (hopefully) resolve the collision.
                    To permanently fix this, please ensure you call figure() prior
                    to other matplotlib plotting functions.
                    """
                sys.stderr.write(lyse.dedent(msg))
            self.figs[identifier] = fig
            self.__allocated_figures.append(identifier)
            self._remove_dead_references(identifier, fig)
                
    def __call__(self,identifier=None, *args, **kwargs):
        if identifier is None:
            number, fig =  self.get_first_empty_figure(identifier, *args,**kwargs)
            self.figs[number] = fig
            self._remove_dead_references(number, fig)
        elif identifier in self.figs:
            fig = self.figs[identifier]
            self._figure(fig.number)
            if fig.number not in self.__allocated_figures:
                self.__allocated_figures.append(fig.number)
        else:
            number, fig =  self.get_first_empty_figure(identifier, *args,**kwargs)
            self.figs[identifier] = fig
            self._remove_dead_references(identifier, fig)
        return fig

    def close(self,identifier=None):
        if identifier is None:
            thisfig = matplotlib.pyplot.gcf()
            for key, fig in list(self.figs.items()):
                if fig is thisfig:
                    del self.figs[key]
                    self._close()
        elif isinstance(identifier,matplotlib.figure.Figure):
            thisfig = identifier
            for key, fig in list(self.figs.items()):
                if fig is thisfig:
                    del self.figs[key]
                    self._close(thisfig)
        elif identifier == 'all':
            self.figs = OrderedDict()
            self._close('all')
        else:
            fig = self.figs[identifier]
            self._close(fig)
            del self.figs[identifier]
            
    def show(self):
        if lyse.spinning_top:
            pass # supress show()
        else:
            self._show()

    def reset(self):
        self.__allocated_figures = []

    def _remove_dead_references(self, current_identifier, current_fig):
        for key, fig in list(self.figs.items()):
            if fig == current_fig and key != current_identifier:
                del self.figs[key]

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
