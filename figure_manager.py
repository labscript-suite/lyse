import sys
if 'matplotlib.pyplot' in sys.modules:
    raise ImportError('lyse must be imported prior to pylab/pyplot in order to correctly override the figure() function.')

class FigureManager(object):

    def __init__(self):
        self.figs = {}
        self._figure = matplotlib.pyplot.figure
    
    def get_first_empty_figure(self,*args,**kwargs):
        i = 1
        while True:
            fig = self._figure(i,*args,**kwargs)
            if not fig.axes:
                return i, fig
            i += 1
                
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

import matplotlib.pyplot        
matplotlib.pyplot.figure = FigureManager()
