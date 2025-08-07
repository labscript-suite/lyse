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

# Open up the lab config
LABCONFIG = LabConfig()

# get port that lyse is using for communication
try:
    LYSE_PORT = int(LABCONFIG.get('ports', 'lyse'))
except Exception:
    LYSE_PORT = 42519
