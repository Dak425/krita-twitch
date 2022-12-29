from krita import Krita, Extension
from PyQt5.QtWidgets import QMessageBox
from kritatwitch.vendor import twitch
from .config import load_config
from .chat import handle_message

from pathlib import Path
import json

BASE_PATH = Path(__file__).parent
DEFAULT_CONFIG_PATH = (BASE_PATH / "config.default.json").resolve()
CONFIG_PATH = (BASE_PATH / "config.json").resolve()

class KritaTwitch(Extension):
    """
    Let twitch chat control Krita!
    """

    def __init__(self, parent):
        super(KritaTwitch, self).__init__(parent)
        self.config = load_config(CONFIG_PATH)
        self.helix = twitch.Helix(
            self.config["twitch"]["client_id"],
            self.config["twitch"]["client_secret"],
            use_cache=True
        )
        self.chat = twitch.Chat(
            channel=f'#{self.config["twitch"]["channel"]}',
            nickname='kritatwitch',
            oauth='oauth:xxxxxx',
            helix=self.helix
        )
        self.chat.subscribe(handle_message)

    def setup(self):
        pass

    def createActions(self, window):
        action = window.createAction("python_system_check", "System Check")
        action.triggered.connect(self.system_check)

    def system_check(self):
        displayName = self.helix.user(self.config["twitch"]["channel"]).display_name
        messageBox = QMessageBox()
        messageBox.setInformativeText(Application.version())
        messageBox.setWindowTitle('System Check')
        messageBox.setText(f"Hello {displayName}! Here is the version of Krita you are using.")
        messageBox.setStandardButtons(QMessageBox.Close)
        messageBox.setIcon(QMessageBox.Information)
        messageBox.exec()

Krita.instance().addExtension(KritaTwitch(Krita.instance()))