from krita import Krita, Extension
from PyQt5.QtWidgets import QMessageBox
from kritatwitch.vendor import twitch

class KritaTwitch(Extension):
    """
    Let twitch chat control Krita!
    """

    def __init__(self, parent):
        super(KritaTwitch, self).__init__(parent)

    def setup(self):
        self.helix = twitch.Helix('0aa1cri1n9qa1ij9ec43o9lt378jgo', 'aco2amtnc74aycfz9igxfsk9c51h0g')

    def createActions(self, window):
        action = window.createAction("python_system_check", "System Check")
        action.triggered.connect(self.system_check)

    def system_check(self):
        messageBox = QMessageBox()
        messageBox.setInformativeText(Application.version())
        messageBox.setWindowTitle('System Check')
        messageBox.setText("Hello! Here is the version of Krita you are using.")
        messageBox.setStandardButtons(QMessageBox.Close)
        messageBox.setIcon(QMessageBox.Information)
        messageBox.exec()

Krita.instance().addExtension(KritaTwitch(Krita.instance()))