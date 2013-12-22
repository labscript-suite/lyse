# -*- coding: UTF-8 -*-
import sys
import os
from PySide.QtCore import *
from PySide.QtGui import *

from qtutils import *
from qtutils.UiLoader import UiLoader as QUiLoader
#from PySide.QtUiTools import QUiLoader

class RoutineTreeviewModel(QStandardItemModel):
    def __init__(self, parent=None):
        QStandardItemModel.__init__(self,parent)
        self._column_headings = ['check', 'progress', 'Analysis Script']
        self._create_headers()
        
    def _create_headers(self):
        for i,column in enumerate(self._column_headings):
            self.setHorizontalHeaderItem(i,QStandardItem(column))
            
            
class RoutineTreeView(QTreeView):
    def __init__(self,*args,**kwargs):
        QTreeView.__init__(self,*args,**kwargs)
        
        self._drag_start_position = None
        self._type = 'None'
    
    def setup_dragging(self,type):
        self._type = type
        self.setDragEnabled(True)
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_position = event.pos()
        return QTreeView.mousePressEvent(self,event)
            
    def mouseMoveEvent(self,event):
        if not event.buttons()&Qt.LeftButton or self._drag_start_position is None:
            return QTreeView.mouseMoveEvent(self,event)
        if (event.pos()-self._drag_start_position).manhattanLength() < QApplication.startDragDistance():
            return QTreeView.mouseMoveEvent(self,event)
            
        # save the selected indexes
        self._index_list = self.selectedIndexes()
            
        # we should begin the drag!
        drag = QDrag(self)
        mimeData = QMimeData()
        mimeData.setData("lyse.RoutineBox",self._type)
        drag.setMimeData(mimeData)
        
        dropAction = drag.exec_(Qt.MoveAction)
        #if dropAction == Qt.MoveAction:
            # we should find out where
        
    def dragEnterEvent(self, event):
        print 'drag enter'
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                event.accept()
                event.acceptProposedAction()
                print 'lyse internal'
            else:
                event.ignore()
        except Exception:
            if event.mimeData().hasUrls():
                event.setDropAction(Qt.CopyAction)
                event.accept()
            else:            
                event.ignore()

    def dragMoveEvent(self, event):
        #print 'dragMove'
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                event.setDropAction(Qt.MoveAction)
                dropIndex = self.indexAt(event.pos())
                if dropIndex.isValid():
                    if dropIndex not in self._index_list:
                        # Checks to do:
                        #    make sure the drop target is not one of the selected items
                        #    Make sure the drop target is not a child of a selected row
                        index = dropIndex
                        while index.row() != -1:
                            if index in self._index_list:
                                event.ignore()
                                return False
                            index = index.parent()
                            
                        event.accept()
                #print 'lyse internal'
            else:
                event.ignore()
        except Exception:
            if event.mimeData().hasUrls():
                event.setDropAction(Qt.CopyAction)
                event.accept()
            else:            
                event.ignore()

    def dropEvent(self, event):
        #print 'dropEvent'
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                #print 'lyse internal'
                dropIndex = self.indexAt(event.pos())
                if dropIndex.isValid():
                    append_or_insert = 'append'
                    
                    # Check whether we really wanted to place it between two rows, or make it a child of a row
                    if dropIndex != self.indexAt(QPoint(event.pos().x(), event.pos().y()+5)):
                        append_or_insert = 'insert_below'
                    if dropIndex != self.indexAt(QPoint(event.pos().x(), event.pos().y()-5)):
                        append_or_insert = 'insert_above'
                    
                    # covert to index of first column
                    dropIndex = self.model().index(dropIndex.row(),0,dropIndex.parent())
                    
                    # Checks to do:
                    #    make sure the drop target is not one of the selected items
                    #    Make sure the drop target is not a child of a selected row
                    index = dropIndex
                    while index.row() != -1:
                        if index in self._index_list:
                            event.ignore()
                            return False
                        index = index.parent()
                        print index
                    
                    # if we get this far, then accept the event!
                    event.accept()
                    # algorithm:
                    # * If a row has children which are not selected, move them "up a level"
                    #   until they have a parent which is not selected
                    # * Build up row hierarchy of selected rows in tuple form
                    # * Remove any children from the "items to move" which have parents that 
                    #   are also moving
                    # * Take one of the rows to move and remove it
                    # * if this row was at the same or higher level than the drop target,
                    #   and had a row number that was closer to 0 than the drop target,
                    #   then we need to update the index of the drop target
                    # * add it as a child of the drop target
                    # * work out it's new index and add it to the selection model
                    
                    # print self._index_list
                    
                    # for index in self._index_list:
                        # if index.column() == 0:
                            # item = self.model.itemFromIndex(index)
                            # if item.hasChildren():
                                # for i in item.rowCount():
                                    
                    
                    # convert selected indices to a list of items
                    # we'll look up the index from the items when we need to!
                    # This is because index's will change when the model does
                    # and so any ModelIndex we use will become invalid after the first model change
                    # It is much better to use items as the reference point because they are not deleted
                    selected_item_list = []
                    for index in sorted(self._index_list):
                        if index.column() == 0:
                            selected_item_list.append(self.model().itemFromIndex(index))
                    print selected_item_list
                    
                    # Decide where we are inserting to, or if we are appending
                    # and what item we are adding it to
                    if append_or_insert == 'append':
                        itemToAddTo = self.model().itemFromIndex(dropIndex)
                    else:
                        if append_or_insert == 'insert_below':
                            insert_position = dropIndex.row()+1
                        else:
                            insert_position = dropIndex.row()
                        itemToAddTo = self.model().itemFromIndex(dropIndex.parent())
                        if itemToAddTo is None:
                            itemToAddTo = self.model()
                        if insert_position < 0:
                            insert_position = 0
                        if insert_position >= itemToAddTo.rowCount():
                            append_or_insert = 'append'
                        
                    # For each selected item, move it to the new place
                    for selected_item in selected_item_list:
                        parentItem = selected_item.parent()
                        index = self.model().indexFromItem(selected_item)
                        if parentItem:
                            items = parentItem.takeRow(index.row())
                        else:
                            items = self.model().takeRow(index.row())
                            
                        if append_or_insert in ['insert_below', 'insert_above']:
                            itemToAddTo.insertRow(insert_position,items)
                        else:
                            itemToAddTo.appendRow(items)
                        
                        for item in items:
                            self.selectionModel().select(self.model().indexFromItem(item),QItemSelectionModel.Select)
                    
                    # Make the item we added to, expanded (if it isn't directly the model)
                    if itemToAddTo is not self.model():
                        self.expand(self.model().indexFromItem(itemToAddTo))
                    return True
            else:
                event.ignore()
        except Exception:
            raise
            if event.mimeData().hasUrls():
                event.setDropAction(Qt.CopyAction)
                event.accept()
            else:            
                event.ignore()
        
        
        return False
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.endswith('.h5') or path.endswith('.hdf5'):
                    self._logger.info('Acceptable file dropped. Path is %s'%path)
                    if self.add_to_queue:
                        self.add_to_queue(str(path))
                    else:
                        self._logger.info('Dropped file not added to queue because there is no access to the neccessary add_to_queue method')
                else:
                    self._logger.info('Invalid file dropped. Path was %s'%path)
        else:
            event.ignore()
            
class RoutineBox(object):    
    def __init__(self, lyse, layout, type):
        self.lyse = lyse
        
        if type not in ['single', 'multi']:
            raise RuntimeError('You cannot instantaite a routine box with a type other than "single" or "multi"')
        
        # Load the UI
        loader = QUiLoader()
        loader.registerCustomPromotion('routine_treeview',RoutineTreeView)
        self._ui = loader.load(os.path.join(os.path.dirname(os.path.realpath(__file__)),'routine_box.ui'))
        
        # Set type specific properties
        if type == 'single':
            self._ui.groupbox.setTitle('Single-shot Analysis Scripts')
        elif type == 'multi':
            self._ui.groupbox.setTitle('Multi-shot Analysis Scripts')
        else:
            raise NotImplementedError('Support for type=%s in RoutineBox has not bee implemented properly'%str(type))
        
        # Setup the tree model
        self.model = RoutineTreeviewModel()
        self._ui.routine_treeview.setModel(self.model)
        self._ui.routine_treeview.setup_dragging(type)
        
        # connect signals
        self._ui.add_routine_button.clicked.connect(self.add_routine)
        
        layout.addWidget(self._ui)

        self._tempi = 0
        
    def add_routine(self):
        # TODO: prompt user for file path
        
        # Create Items
        items = []
        check_item = QStandardItem()
        check_item.setCheckable(True)
        check_item.setCheckState(Qt.Checked) # other option is Qt.Unchecked
        items.append(check_item)
        # TODO: implement shift and figure out unicode
        # items.append(QStandardItem(u'‹›'))
        items.append(QStandardItem(''))
        # script name
        items.append(QStandardItem('my_script_%d.py'%self._tempi))
        self.model.appendRow(items)
        self._tempi += 1
        
if __name__ == '__main__':
    
    qapplication = QApplication(sys.argv)
    
    window = QWidget()
    layout = QVBoxLayout(window)
    
    # create routine Box
    routineBox = RoutineBox({},layout,'single')
    
    window.show()
    sys.exit(qapplication.exec_())