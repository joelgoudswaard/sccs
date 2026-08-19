# modules/toasts.py
import time
import logging
import uuid
from typing import Optional
from flask_socketio import SocketIO

logger = logging.getLogger("sccs")


class ToastManager:

    def __init__(self, config, socketio: SocketIO):
        self.config = config
        self.socketio = socketio

        # Load defaults from [toasts] section
        self.default_duration = config.getint('toasts', 'default_duration')
        self.error_duration   = config.getint('toasts', 'error_duration')
        self.warning_duration = config.getint('toasts', 'warning_duration')
        self.rooftop_safety_timeout = config.getint('toasts', 'rooftop_tent_safety_warning_timeout')

        logger.info("🍞 ToastManager initialized")

    def send_toast(
        self,
        message: str,
        toast_type: str = "info",
        duration: Optional[int] = None,
        title: Optional[str] = None,
        persistent: bool = False,
        broadcast: bool = True
    ):

        if toast_type not in ("success", "info", "warning", "error"):
            toast_type = "info"

        # Use config defaults if no duration is passed
        if duration is None:
            if toast_type == "error":
                duration = self.error_duration
            elif toast_type == "warning":
                duration = self.warning_duration
            else:
                duration = self.default_duration

        toast_data = {
            "id": f"toast_{uuid.uuid4().hex[:16]}",
            "message": message,
            "type": toast_type,
            "duration": 0 if persistent else duration,
            "title": title,
            "timestamp": time.time(),
            "persistent": persistent
        }

        if broadcast:
            self.socketio.emit("toast", toast_data)
            logger.debug(f"📢 Toast [{toast_type}] → {message[:120]}")
        else:
            self.socketio.emit("toast", toast_data)

    def success(self, message: str, **kwargs):
        self.send_toast(message, "success", **kwargs)

    def info(self, message: str, **kwargs):
        self.send_toast(message, "info", **kwargs)

    def warning(self, message: str, **kwargs):
        self.send_toast(message, "warning", **kwargs)

    def error(self, message: str, **kwargs):
        self.send_toast(message, "error", **kwargs)

    def rooftop_safety_warning(self, message: str):
        """Special method for rooftop tent safety toast"""
        self.send_toast(
            message,
            toast_type="warning",
            duration=self.rooftop_safety_timeout * 1000,
            title="Safety Warning"
        )


toast_manager: Optional[ToastManager] = None