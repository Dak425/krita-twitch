from PyQt5.QtWidgets import QDialog, QTableWidget, QWidget

class UI(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tabs = QTabWidget()
        