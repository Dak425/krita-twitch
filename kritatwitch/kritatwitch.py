from krita import Krita, Extension, Window
from PyQt5.QtWidgets import QVBoxLayout, QStatusBar, QDialog, QLabel, QCheckBox, QPushButton, QHBoxLayout
from .config import load_config, save_config, Config
from .kritautil import switch_colors, set_bg_color, set_fg_color, set_brush_size
from kritatwitch.vendor.twitchAPI.twitch import Twitch, InvalidTokenException
from kritatwitch.vendor.twitchAPI.chat import Chat, ChatEvent, ChatCommand
from kritatwitch.vendor.twitchAPI.oauth import UserAuthenticator, refresh_access_token
from kritatwitch.vendor.twitchAPI.types import AuthScope
from typing import Optional, List, Tuple, Callable
from datetime import datetime, timedelta
from pathlib import Path
from functools import wraps, partial
from math import ceil

import asyncio

BASE_PATH = Path(__file__).parent
DEFAULT_CONFIG_PATH = (BASE_PATH / "config.default.json").resolve()
CONFIG_PATH = (BASE_PATH / "config.json").resolve()
USER_SCOPE = [AuthScope.CHAT_READ, AuthScope.CHAT_EDIT]

class KritaTwitch(Extension):
    """
    Let twitch chat control Krita!
    """
    def __init__(self, parent):
        super().__init__(parent)
        self.config: Config = load_config(CONFIG_PATH)
        
        # Event loop for running async code
        self._twitch_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._twitch_loop)
        
        # Twitch Auth Related
        self.client_id: str = self.config.get("twitch_client_id")
        self.client_secret: str = self.config.get("twitch_client_secret")
        self.user_token: Optional[str] = None
        self.refresh_token: Optional[str] = self.config.get("twitch_refresh_token")
        self.twitch: Twitch = self._twitch_loop.run_until_complete(Twitch(self.client_id, self.client_secret))
        self.authenticator: UserAuthenticator = UserAuthenticator(self.twitch, USER_SCOPE)
        self._twitch_authenicated: bool = False
        self._authenticate()
        
        # Chat Related
        self.channel: str = self.config["twitch_channel"]
        self.chat_prefix: str = self.config.get("twitch_chat_prefix", "?")
        self.chat_enabled: bool = self.config.get("twitch_chat_enabled", False)
        self.cooldown_time: int = self.config.get("twitch_chat_cooldown", 30)
        self._cooldowns: Dict[str, datetime] = {}
        self._twitch_chat_connected: bool = False
        self.chat: Chat = self._twitch_loop.run_until_complete(Chat(self.twitch))
        self.chat.register_event(ChatEvent.READY, self._handle_on_ready)
        self.chat.set_prefix(self.chat_prefix)
        
        # Admin Commands
        self.admin_commands: List[Tuple] = [
            ("flushcooldowns", partial(self._run_command_with_admin_guard, handler=self._flush_cooldowns_command)),
        ]
        for command in self.admin_commands:
            self.chat.register_command(command[0], command[1])
        
        # Krita Commands
        self.chat_commands: List[Tuple] = [
            ("switchcolors", partial(self._run_command_with_cooldown_guard, handler=self._switch_color_command)),
            ("fgcolor", partial(self._run_command_with_cooldown_guard, handler=self._change_foreground_color_command)),
            ("bgcolor", partial(self._run_command_with_cooldown_guard, handler=self._change_background_color_command)),
            ("brushsize", partial(self._run_command_with_cooldown_guard, handler=self._change_brush_size_command))
        ]
        for command in self.chat_commands:
            self.chat.register_command(command[0], command[1])

        if self.chat_enabled:
            self._connect_to_chat()

    def _authenticate(self):
        if self._twitch_authenicated:
            return

        # If a refresh token is present, attempt to obtain a access token with it
        if self.refresh_token is not None:
            try:
                self.user_token, self.refresh_token = self._twitch_loop.run_until_complete(
                    refresh_access_token(self.refresh_token, self.client_id, self.client_secret)
                )
            except:
                pass

        if self.refresh_token is None or self.user_token is None:
            self.user_token, self.refresh_token = self._twitch_loop.run_until_complete(
                self.authenticator.authenticate()
            )

        self._twitch_loop.run_until_complete(
            self.twitch.set_user_authentication(
                self.user_token,
                USER_SCOPE,
                self.refresh_token
            )
        )
        
        self.config["twitch_refresh_token"] = self.refresh_token
        save_config(CONFIG_PATH, self.config)

        self._twitch_authenicated = True
        
    def _connect_to_chat(self):
        if self._twitch_chat_connected:
            return

        self.chat.start()
        
        self._twitch_chat_connected = True
        
    def _disconnect_from_chat(self):
        if not self._twitch_chat_connected:
            return

        self.chat.stop()

        self._twitch_chat_connected = False
        
    async def _handle_on_ready(self, ready_event):
        await self.chat.join_room(self.channel)
        
    async def _run_command_with_admin_guard(self, cmd: ChatCommand, handler: Callable):
       handler(cmd)
        
    async def _run_command_with_cooldown_guard(self, cmd: ChatCommand, handler: Callable):
        on_cooldown, remaining_time = self._user_is_on_cooldown(cmd)
        if on_cooldown:
            message = f"You must wait {remaining_time} seconds"
            await self._reply_to_command(cmd, message) 
            return
        
        handler(cmd)

        self._update_cooldown(cmd)
        
    # TODO: Don't add to cooldowns if command user is the authenticated user
    # TODO: Add logic for using different times for different roles (VIP, Mod, etc)
    def _update_cooldown(self, cmd: ChatCommand):
        self._cooldowns[cmd.user.name] = datetime.now()
    
    # TODO: Add logic for checking if the user is the authenticated user
    # TODO: Add logic for checking if the user is a mod
    # TODO: Add logic for checking if the user is a vip
    def _user_is_on_cooldown(self, cmd: ChatCommand) -> (bool, int):
        """
        Check if the user who submitted the command still needs to wait before using another command
        """
        user_time: datetime = self._cooldowns.get(cmd.user.name)
        if user_time is None:
            return False, 0

        tdelta: timedelta = datetime.now() - user_time
        if self.cooldown_time < tdelta.total_seconds():
            return False, 0

        time_remaining = ceil(self.cooldown_time - tdelta.total_seconds())
        return True, time_remaining
    
    def _flush_cooldowns_command(self, cmd: ChatCommand):
        self._cooldowns = {}
        
    # @_run_command_with_guard
    def _switch_color_command(self, cmd: ChatCommand):
        switch_colors() 
        
    async def _change_foreground_color_command(self, cmd: ChatCommand):
        params = cmd.parameter.split(" ")
        if len(params) != 3:
            message = f"Not Valid: Usage '{self.chat_prefix}fgcolor 255 255 255'" 
            await self._reply_to_command(cmd, message)
            return

        params = list(map(int, params))
        set_fg_color(params[0], params[1], params[2])
    
    async def _change_background_color_command(self, cmd: ChatCommand):
        params = cmd.parameter.split(" ")
        if len(params) != 3:
            message = f"Not Valid: Usage '{self.chat_prefix}bgcolor 255 255 255'" 
            await self._reply_to_command(cmd, message)
            return
        
        params = list(map(int, params))
        set_bg_color(params[0], params[1], params[2])

    async def _change_brush_size_command(self, cmd: ChatCommand):
        params = cmd.parameter.split(" ")
        if len(params) != 1:
            message = f"Not Valid: Usage '{self.chat_prefix}brushsize 50'" 
            await self._reply_to_command(cmd, message)
            return

        set_brush_size(int(params[0]))
        
    async def _reply_to_command(self, cmd: ChatCommand, message: str):
        await cmd.reply(f"(Krita 🖌️) {message}")

    def setup(self):
        pass

    def createActions(self, window: Window):
        action = window.createAction("python_krita_twitch", "Krita Twitch")
        action.triggered.connect(self.show_ui)
        
    def show_ui(self):
        self.dialog = QDialog()
        self.dialog.setWindowTitle("Krita Twitch")

        chat_toggle = QCheckBox("Enable/Disable Chat Bot")
        if self._twitch_chat_connected:
            chat_toggle.setChecked(True)
        chat_toggle.stateChanged.connect(self._toggle_chat)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self._close_ui)
        
        layout = QVBoxLayout()
        layout.addWidget(chat_toggle)
        h_layout = QHBoxLayout()
        h_layout.addStretch()
        h_layout.addWidget(close_button)
        layout.addLayout(h_layout)
        self.dialog.setLayout(layout)
        self.dialog.exec()

    def _toggle_chat(self, state):
        if state == 2:
            self._connect_to_chat()
            self.config["twitch_chat_enabled"] = True
            save_config(CONFIG_PATH, self.config)
            return

        self._disconnect_from_chat()
        self.config["twitch_chat_enabled"] = False
        save_config(CONFIG_PATH, self.config)

    def _close_ui(self):
        self.dialog.close()
