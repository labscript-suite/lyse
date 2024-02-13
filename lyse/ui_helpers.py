from qtutils.qt import QtCore

class RoutineBoxData():
    
    COL_ACTIVE = 0
    COL_STATUS = 1
    COL_NAME = 2
    ROLE_FULLPATH = QtCore.Qt.UserRole + 1
    # This data (stored in the name item) does not necessarily match
    # the position in the model. It will be set just
    # prior to sort() being called with this role as the sort data.
    # This is how we will reorder the model's rows instead of
    # using remove/insert.
    ROLE_SORTINDEX = QtCore.Qt.UserRole + 2

def get_screen_geometry(qapplication):
    """Return the a list of the geometries of each screen: each a tuple of
    left, top, width and height"""
    geoms = []
    desktop = qapplication.desktop()
    for i in range(desktop.screenCount()):
        sg = desktop.screenGeometry(i)
        geoms.append((sg.left(), sg.top(), sg.width(), sg.height()))
    return geoms