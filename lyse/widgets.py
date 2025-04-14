#####################################################################
#                                                                   #
# /widgets.py                                                      #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program lyse, in the labscript suite     #
# (see http://labscriptsuite.org), and is licensed under the        #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Lyse GUI widgets
"""

# qt imports
from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import pyqtSignal as Signal

class UneditableModel(QtGui.QStandardItemModel):

    def flags(self, index):
        """Return flags as normal except that the ItemIsEditable
        flag is always False"""
        result = QtGui.QStandardItemModel.flags(self, index)
        return result & ~QtCore.Qt.ItemIsEditable

class EditColumnsDialog(QtWidgets.QDialog):
    close_signal = Signal()

    def __init__(self):
        QtWidgets.QDialog.__init__(self, None, QtCore.Qt.WindowSystemMenuHint | QtCore.Qt.WindowTitleHint)

    def closeEvent(self, event):
        self.close_signal.emit()
        event.ignore()


class ItemDelegate(QtWidgets.QStyledItemDelegate):

    """An item delegate with a fixed height and a progress bar in one column"""
    EXTRA_ROW_HEIGHT = 2

    def __init__(self, app, view, model, col_status, role_status_percent):
        self.app = app
        self.view = view
        self.model = model
        self.COL_STATUS = col_status
        self.ROLE_STATUS_PERCENT = role_status_percent
        QtWidgets.QStyledItemDelegate.__init__(self)

    def sizeHint(self, *args):
        fontmetrics = QtGui.QFontMetrics(self.view.font())
        text_height = fontmetrics.height()
        row_height = text_height + self.EXTRA_ROW_HEIGHT
        size = QtWidgets.QStyledItemDelegate.sizeHint(self, *args)
        return QtCore.QSize(size.width(), row_height)

    def paint(self, painter, option, index):
        if index.column() == self.COL_STATUS:
            status_percent = self.model.data(index, self.ROLE_STATUS_PERCENT)
            if status_percent == 100:
                # Render as a normal item - this shows whatever icon is set instead of a progress bar.
                return QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
            else:
                # Method of rendering a progress bar into the view copied from
                # Qt's 'network-torrent' example:
                # http://qt-project.org/doc/qt-4.8/network-torrent-torrentclient-cpp.html

                # Set up a QStyleOptionProgressBar to precisely mimic the
                # environment of a progress bar.
                progress_bar_option = QtWidgets.QStyleOptionProgressBar()
                progress_bar_option.state = QtWidgets.QStyle.State_Enabled
                progress_bar_option.direction = self.app.qapplication.layoutDirection()
                progress_bar_option.rect = option.rect
                progress_bar_option.fontMetrics = self.app.qapplication.fontMetrics()
                progress_bar_option.minimum = 0
                progress_bar_option.maximum = 100
                progress_bar_option.textAlignment = QtCore.Qt.AlignCenter
                progress_bar_option.textVisible = True

                # Set the progress and text values of the style option.
                progress_bar_option.progress = int(status_percent)
                progress_bar_option.text = '%d%%' % status_percent

                # Draw the progress bar onto the view.
                self.app.qapplication.style().drawControl(QtWidgets.QStyle.CE_ProgressBar, progress_bar_option, painter)
        else:
            return QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)

class TableView(QtWidgets.QTableView):
    leftClicked = Signal(QtCore.QModelIndex)
    doubleLeftClicked = Signal(QtCore.QModelIndex)
    """A QTableView that emits a custom signal leftClicked(index) after a left
    click on a valid index, and doubleLeftClicked(index) (in addition) on
    double click. Multiple inheritance of QObjects is not possible, so we
    are forced to duplicate code instead of sharing code with the extremely
    similar TreeView class in this module"""

    def __init__(self, *args):
        QtWidgets.QTableView.__init__(self, *args)
        self._pressed_index = None
        self._double_click = False

    def mousePressEvent(self, event):
        result = QtWidgets.QTableView.mousePressEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
        return result

    def leaveEvent(self, event):
        result = QtWidgets.QTableView.leaveEvent(self, event)
        self._pressed_index = None
        self._double_click = False
        return result

    def mouseDoubleClickEvent(self, event):
        # Ensure our left click event occurs regardless of whether it is the
        # second click in a double click or not
        result = QtWidgets.QTableView.mouseDoubleClickEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
            self._double_click = True
        return result

    def mouseReleaseEvent(self, event):
        result = QtWidgets.QTableView.mouseReleaseEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid() and index == self._pressed_index:
            self.leftClicked.emit(index)
            if self._double_click:
                self.doubleLeftClicked.emit(index)
        self._pressed_index = None
        self._double_click = False
        return result

class TreeView(QtWidgets.QTreeView):
    leftClicked = Signal(QtCore.QModelIndex)
    doubleLeftClicked = Signal(QtCore.QModelIndex)
    """A QTreeView that emits a custom signal leftClicked(index) after a left
    click on a valid index, and doubleLeftClicked(index) (in addition) on
    double click."""

    def __init__(self, *args):
        QtWidgets.QTreeView.__init__(self, *args)
        self._pressed_index = None
        self._double_click = False

    def mousePressEvent(self, event):
        result = QtWidgets.QTreeView.mousePressEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
        return result

    def leaveEvent(self, event):
        result = QtWidgets.QTreeView.leaveEvent(self, event)
        self._pressed_index = None
        self._double_click = False
        return result

    def mouseDoubleClickEvent(self, event):
        # Ensure our left click event occurs regardless of whether it is the
        # second click in a double click or not
        result = QtWidgets.QTreeView.mouseDoubleClickEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid():
            self._pressed_index = self.indexAt(event.pos())
            self._double_click = True
        return result

    def mouseReleaseEvent(self, event):
        result = QtWidgets.QTreeView.mouseReleaseEvent(self, event)
        index = self.indexAt(event.pos())
        if event.button() == QtCore.Qt.LeftButton and index.isValid() and index == self._pressed_index:
            self.leftClicked.emit(index)
            if self._double_click:
                self.doubleLeftClicked.emit(index)
        self._pressed_index = None
        self._double_click = False
        return result

