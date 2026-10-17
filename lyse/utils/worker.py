#####################################################################
#                                                                   #
# /utils.worker.py                                                  #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Lyse worker common utilities
"""

import os
import sys
import threading

from labscript_utils import dedent


spinning_top = False
"""Variable to tell lyse API if running from within lyse GUI"""
path = None
"""Path to hdf5 being analysed.

If running stand-alone, not from within the lyse GUI, default is None.
Within lyse GUI, updated automatically to the correct path.
"""
_updated_data = {}
"""Data to be sent back to the lyse GUI if running within lyse"""
_plot_classes = {}
"""Dictionary of plot id's to classes to use for Plot object"""
Plot=object
"""A fake Plot object to subclass if we are not running in the GUI"""
plots = {}
"""An empty dictionary of plots (overwritten by the analysis worker if running within lyse)"""
delay_event = threading.Event()
"""A threading.Event to signal a delay"""
_delay_flag = False
"""Flag to determine whether we should wait for the delay event"""


utils_dir = os.path.dirname(os.path.realpath(__file__))

def figure_to_clipboard(figure=None, **kwargs):
    """Copy a matplotlib figure to the clipboard as a png. 

    If figure is None,
    the current figure will be copied. Copying the figure is implemented by
    calling figure.savefig() and then copying the image data from the
    resulting file. If bbox_inches keyword arg is not provided,
    bbox_inches='tight' will be used.

    Args:
        figure (:obj:`matplotlib:matplotlib.figure`, optional): 
            Figure to copy to the clipboard. If `None`, copies the current figure.
        **kwargs: Passed to `figure.savefig()` as kwargs.
    """
    
    import matplotlib.pyplot as plt
    from zprocess import start_daemon
    import tempfile

    if 'bbox_inches' not in kwargs:
        kwargs['bbox_inches'] = 'tight'
               
    if figure is None:
        figure = plt.gcf()

    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        tempfile_name = f.name

    figure.savefig(tempfile_name, **kwargs)

    tempfile2clipboard = os.path.join(utils_dir, 'tempfile2clipboard.py')
    start_daemon([sys.executable, tempfile2clipboard, '--delete', tempfile_name])


def register_plot_class(identifier, cls):
    if not spinning_top:
        msg = """Warning: lyse.register_plot_class has no effect on scripts not run with
            the lyse GUI.
            """
        sys.stderr.write(dedent(msg))
    _plot_classes[identifier] = cls

def get_plot_class(identifier):
    return _plot_classes.get(identifier, None)

def delay_results_return():
    global _delay_flag
    if not spinning_top:
        msg = """Warning: lyse.delay_results_return has no effect on scripts not run 
            with the lyse GUI.
            """
        sys.stderr.write(dedent(msg))
    _delay_flag = True
