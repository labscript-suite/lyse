#####################################################################
#                                                                   #
# /tempfile2clipboard.py                                            #
#                                                                   #
# Copyright 2017, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################

from __future__ import division, unicode_literals, print_function, absolute_import
import sys
import os

from qtutils.qt.QtWidgets import QApplication
from qtutils.qt.QtGui import QImage

"""
This is a stand-alone script which copies an image to the clipboard and then
optionally deletes the image file.

usage:

    python tempfile2clipboard.py [--delete] image_filepath


After copying the image data to the clipboard, this script optionally deletes
the image file, if the --delete argument is provided, and then continues
running until the clipboard data changes.

This is due to the way in which some clipboards work - data is not requested
from the application until it is pasted somewhere, and so the application
doing the copying must still be running. It is also useful to have this
functionality be a stand-alone script since it requires a Qt mainloop, and
this way we avoid corner-cases of how this may interfere with any use of Qt in
the calling program.

"""

def main():
    USAGE = 'Usage:\n    python tempfile2clipboard.py [--delete] image_filepath\n'

    if len(sys.argv) > 1 and sys.argv[1] == '--delete':
        delete = True
        del sys.argv[1]
    else:
        delete = False

    if len(sys.argv) != 2:
        sys.stderr.write("Invalid arguments.\n" + USAGE)
        sys.exit(1)

    image_file = sys.argv[1]
    image = QImage(image_file)
    if delete:
        os.unlink(image_file)

    if image.isNull():
        sys.stderr.write("Invalid image file: {}.\n".format(image_file) + USAGE)
        sys.exit(1)

    app = QApplication([])
    app.clipboard().setImage(image)

    # Keep running until the clipboard contents change to something else:
    app.clipboard().dataChanged.connect(app.quit)
    app.exec_()


if __name__ == '__main__':
    main()