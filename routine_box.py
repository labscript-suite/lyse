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
        
        self.collapsed.connect(self.expand)
    
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
                        
        # we should begin the drag!
        drag = QDrag(self)
        mimeData = QMimeData()
        mimeData.setData("lyse.RoutineBox",self._type)
        drag.setMimeData(mimeData)
        
        dropAction = drag.exec_(Qt.MoveAction)
        
    def dragEnterEvent(self, event):
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                event.accept()
                event.acceptProposedAction()
            else:
                event.ignore()
        except Exception:
            if event.mimeData().hasUrls():
                event.setDropAction(Qt.CopyAction)
                event.accept()
            else:            
                event.ignore()

    def dragMoveEvent(self, event):
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                dropIndex = self.indexAt(event.pos())
                if dropIndex.isValid():
                    if dropIndex not in self.selectedIndexes():
                        # Checks to do:
                        #    make sure the drop target is not one of the selected items
                        #    Make sure the drop target is not a child of a selected row
                        index = dropIndex
                        while index.row() != -1:
                            if index in self.selectedIndexes():
                                event.ignore()
                                return False
                            index = index.parent()
                            
                        event.setDropAction(Qt.MoveAction)
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

    def getDropDetails(self,event):
        dropIndex = self.indexAt(event.pos())
        if not dropIndex.isValid():
            # then make the index valid!
            # return the index of the last row
            dropIndex = self.model().index(self.model().rowCount()-1,0)
            append_or_insert = 'insert_below'
        else:
            append_or_insert = 'append'
            
            # Check whether we really wanted to place it between two rows, or make it a child of a row
            if dropIndex != self.indexAt(QPoint(event.pos().x(), event.pos().y()+5)):
                append_or_insert = 'insert_below'
            if dropIndex != self.indexAt(QPoint(event.pos().x(), event.pos().y()-5)):
                append_or_insert = 'insert_above'
        
        # covert to index of first column (same row)
        # This is because we add all the items in a row as children of the
        # first item in a row.
        dropIndex = self.model().index(dropIndex.row(),0,dropIndex.parent())
        
        return dropIndex, append_or_insert
    
    def getInsertLocation(self, dropIndex, append_or_insert):        
        if append_or_insert == 'append':
            itemToAddTo = self.model().itemFromIndex(dropIndex)
            return itemToAddTo, None
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
        
            return itemToAddTo, insert_position
            
    def dropEvent(self, event):
        try:
            type = event.mimeData().data("lyse.RoutineBox")
            if type == self._type:
                
                dropIndex, append_or_insert = self.getDropDetails(event)
                
                # Do checks:
                #    make sure the drop target is not one of the selected items
                #    Make sure the drop target is not a child of a selected row
                index = dropIndex
                while index.row() != -1:
                    if index in self.selectedIndexes():
                        event.ignore()
                        return False
                    index = index.parent()
                
                # if we get this far, then accept the event!
                event.accept()
                
                
                #     * Any children of an item that is selected, that are not themselves selected,
                #       should be moved to a new parent
                #                
                selected_indexes = self.selectedIndexes()
                selected_items = [self.model().itemFromIndex(index) for index in selected_indexes]
                while selected_items:
                #for index in selected_indexes:
                    item = selected_items[0]
                    if item.hasChildren():
                        rows_to_take = []
                        for child_row in range(item.rowCount()):
                            child_item = item.child(child_row,0)
                            child_index = self.model().indexFromItem(child_item)
                            if child_index not in selected_indexes:
                                rows_to_take.append(child_row)
                        
                        # Only move children to a new parent if part of the children are selected
                        # and part are not.
                        if len(rows_to_take) != item.rowCount():
                            # Do this in reverse so as not to mess up the indexes
                            for child_row in reversed(rows_to_take):
                                # TODO: preserve child expansion selection
                                child_items = item.takeRow(child_row)
                                
                                # find closest parent that is not selected:
                                current_item = item
                                parent_item = current_item.parent()
                                if parent_item:
                                    parent_index = self.model().indexFromItem(parent_item)
                                    while parent_index in selected_indexes:
                                        current_item = parent_item
                                        parent_item = current_item.parent()
                                            # TODO: if item has no parent, add to model!
                                        if parent_item:
                                            parent_index = self.model().indexFromItem(parent_item)
                                        else:
                                            parent_item = self.model()
                                            break                            
                                else:
                                    parent_item = self.model()
                                # now that we have a parent, add the items to it
                                parent_item.insertRow(current_item.row()+1,child_items)
                                #TODO: restore child expansion selection
                        
                    # Update the selected indexes (previous indexes will be invalid now!)                        
                    selected_items.remove(item)

                #
                #     * Now that the above step is complete, any items wil children selected should 
                #       have those children unselected temporarily (save what was selected though!)
                # 
                selected_indexes = self.selectedIndexes()                
                items_to_restore_selection_of = []
                for index in selected_indexes:
                    item = self.model().itemFromIndex(index)
                    # does this item has a parent that is selected? If yes, unselect it
                    parent_item = item.parent()
                    if parent_item:
                        parent_index = self.model().indexFromItem(parent_item)
                        if parent_index in selected_indexes:
                            # unselect this item temporarily
                            self.selectionModel().select(index,QItemSelectionModel.Deselect)
                            items_to_restore_selection_of.append(item)
                    
                    def unselect_children(item,items_to_restore_selection_of):                    
                        # Does this item have any children? If yes, unselect them (they should all be selected by now)
                        if item.hasChildren():
                            for child_row in range(item.rowCount()):
                                child_item = item.child(child_row,0)
                                child_index = self.model().indexFromItem(child_item)
                                if child_index in self.selectedIndexes():
                                    items_to_restore_selection_of.append(child_item)
                                    self.selectionModel().select(child_index,QItemSelectionModel.Deselect)
                                unselect_children(child_item,items_to_restore_selection_of)
                        return
                    unselect_children(item,items_to_restore_selection_of)
                
                # convert selected indices to a list of items
                # we'll look up the index from the items when we need to!
                # This is because index's will change when the model does
                # and so any ModelIndex we use will become invalid after the first model change
                # It is much better to use items as the reference point because they are not deleted
                selected_indexes = self.selectedIndexes()
                selected_item_list = []
                for index in sorted(selected_indexes):
                    if index.column() == 0:
                        selected_item_list.append(self.model().itemFromIndex(index))
                
                # Decide where we are inserting to, or if we are appending
                # and what item we are adding it to                
                itemToAddTo, insert_position = self.getInsertLocation(dropIndex, append_or_insert)
                
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
                
                #
                #     * restore selection state after move is complete
                #
                for item in items_to_restore_selection_of:
                    # get the model index for this item
                    self.selectionModel().select(self.model().indexFromItem(item),QItemSelectionModel.Select)
                
                    
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
    

    def rowsInserted(self, index, start, end):
        self.expandAll()
        return QTreeView.rowsInserted(self,index,start,end)
    
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