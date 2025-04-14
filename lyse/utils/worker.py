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

from labscript_utils import dedent

from lyse import spinning_top, _plot_classes, _delay_flag
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
