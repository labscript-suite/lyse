#####################################################################
#                                                                   #
# /utils.py                                                         #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Lyse API common utilities
"""


from pathlib import Path

# labscript imports
from labscript_utils.labconfig import LabConfig

LYSE_DIR = Path(__file__).resolve().parent.parent
"""Variable that stores lyse installation directory"""

# Open up the lab config
LABCONFIG = LabConfig()
""":external+labscript-utils:class:`~labscript_utils.labconfig.LabConfig` instance"""

# get port that lyse is using for communication
try:
    LYSE_PORT = int(LABCONFIG.get('ports', 'lyse'))
    """Port that lyse listens on for inter-module communications.
    
    Read from labconfig, defaults to 42519 if config doesn't specify.
    """
    # document here so local docs builds get the string
except Exception:
    LYSE_PORT = 42519
    """Port that lyse listens on for inter-module communications.
    
    Read from labconfig, defaults to 42519 if config doesn't specify.
    """
    # document here so RTD gets the string
