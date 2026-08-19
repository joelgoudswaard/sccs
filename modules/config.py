# modules/config.py
import configparser
import os
import re
import logging

logger = logging.getLogger("sccs.config")


class SccsConfig:
    def __init__(self):
        self._config = configparser.ConfigParser()
        self._config.optionxform = str

        # Path to sccs.conf
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.path = os.path.join(base_dir, 'config', 'sccs.conf')

        if os.path.exists(self.path):
            self._config.read(self.path, encoding='utf-8')
            logger.info(f"📋 Loaded config file: {self.path}")
        else:
            logger.warning(f"⚠️ Config file not found: {self.path}")

    def get(self, section, key, fallback=None):
        try:
            return self._config.get(section, key, fallback=fallback)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def getint(self, section, key, fallback=None):
        try:
            return self._config.getint(section, key, fallback=fallback)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            return fallback

    def getfloat(self, section, key, fallback=None):
        try:
            return self._config.getfloat(section, key, fallback=fallback)
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            if fallback is not None:
                return float(fallback)
            return None

    def getboolean(self, section, key, fallback=None):
        try:
            return self._config.getboolean(section, key, fallback=fallback)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return fallback

    def getlist(self, section, key, fallback=None):
        value = self.get(section, key, fallback)
        if not value:
            return []
        return [x.strip() for x in value.split(',')]

    def has_section(self, section):
        return self._config.has_section(section)

    def items(self, section):
        """Return section as dict"""
        if self.has_section(section):
            return self._config.items(section)
        return []

    def sections(self):
        return self._config.sections()

    def get_section(self, section: str) -> dict:
        """Convenience: return whole section as dict"""
        if self.has_section(section):
            return dict(self._config.items(section))
        return {}

    def set_option(self, section: str, key: str, value: str) -> None:
        """Update a single key in sccs.conf without rewriting comments/layout.

        Same surgical approach as install.sh conf_set: rewrite only the matching
        `key = …` line inside the named section, then refresh the in-memory parser.
        """
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found: {self.path}")

        text = open(self.path, encoding="utf-8").read()
        header = re.compile(rf"(?m)^\[{re.escape(section)}\][ \t]*\r?\n")
        match = header.search(text)
        if not match:
            raise KeyError(f"section [{section}] not found in {self.path}")

        start = match.start()
        nxt = re.compile(r"(?m)^\[[^\]]+\][ \t]*\r?\n")
        m2 = nxt.search(text, match.end())
        end = m2.start() if m2 else len(text)
        block = text[start:end]

        # Spaces/tabs only — \s would eat newlines and the next key.
        pat = re.compile(rf"(?m)^({re.escape(key)}[ \t]*=[ \t]*)(.*)$")
        value_str = "" if value is None else str(value)
        if pat.search(block):
            block = pat.sub(lambda mo, v=value_str: mo.group(1) + v, block, count=1)
        else:
            # Insert new key after the section header line.
            header_line_end = block.find("\n")
            if header_line_end < 0:
                block = f"{block}\n{key} = {value_str}\n"
            else:
                insert_at = header_line_end + 1
                block = block[:insert_at] + f"{key} = {value_str}\n" + block[insert_at:]

        open(self.path, "w", encoding="utf-8").write(text[:start] + block + text[end:])

        if not self._config.has_section(section):
            self._config.add_section(section)
        self._config.set(section, key, value_str)
        logger.info("📝 Updated [%s] %s in %s", section, key, self.path)

    def ensure_section(self, section: str, defaults: dict) -> None:
        """Create [section] with defaults if it is missing. Does not overwrite keys."""
        if self.has_section(section):
            return
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Config file not found: {self.path}")
        lines = [f"\n[{section}]\n"]
        for key, value in defaults.items():
            lines.append(f"{key} = {value}\n")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.writelines(lines)
        if not self._config.has_section(section):
            self._config.add_section(section)
        for key, value in defaults.items():
            self._config.set(section, key, str(value))
        logger.info("📝 Created [%s] in %s", section, self.path)

    @property
    def config(self):
        return self._config


# Global instance
config = SccsConfig()
