"""Persist small UI preference files (dark mode, theme, etc.)."""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger("sccs")


class ConfigManager:
    def __init__(self, filename: str, default: dict):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(base_dir, "config", filename)
        self.default = default
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def load(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    return {**self.default, **json.load(f)}
        except Exception as e:
            logger.error(f"Failed to load config {self.path}: {e}")
        return self.default.copy()

    def save(self, data: dict) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config {self.path}: {e}")