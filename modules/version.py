import os
from datetime import datetime

_ROOT = os.path.dirname(os.path.dirname(__file__))
_VERSION_FILE = os.path.join(_ROOT, "VERSION")


def _is_build_date(part: str) -> bool:
    return len(part) == 6 and part.isdigit()


def get_version() -> str:
    """Read full version string from VERSION file."""
    try:
        with open(_VERSION_FILE, encoding="utf-8") as f:
            version = f.read().strip()
            if version:
                return version
    except OSError:
        pass

    fallback = f"0.1.0.{datetime.now().strftime('%d%m%y')}"
    print(f"⚠️ Using fallback version: {fallback}")
    return fallback


def _parse_version_parts(full: str) -> tuple[str, str, str, str]:
    """Return major, minor, patch, and build date (DDMMYY)."""
    parts = [p.strip() for p in full.split(".") if p.strip()]

    if len(parts) >= 4:
        major, minor, patch, date = parts[0], parts[1], parts[2], parts[3]
    elif len(parts) == 3 and _is_build_date(parts[2]):
        major, minor, date = parts[0], parts[1], parts[2]
        patch = "0"
    elif len(parts) == 3:
        major, minor, patch = parts
        date = ""
    elif len(parts) == 2:
        major, minor = parts
        patch = "0"
        date = ""
    elif len(parts) == 1:
        major = parts[0]
        minor = patch = "0"
        date = ""
    else:
        major = minor = patch = "0"
        date = ""

    return major, minor, patch, date


def get_version_dict() -> dict:
    """Return version breakdown for templates and APIs."""
    full = get_version()
    major, minor, patch, date = _parse_version_parts(full)

    def _as_int(value: str) -> int | str:
        return int(value) if value.isdigit() else value

    return {
        "major": _as_int(major),
        "minor": _as_int(minor),
        "patch": _as_int(patch),
        "date": date,
        "full": full,
        "display": f"v{full}",
    }


APP_VERSION = get_version()
VERSION = get_version_dict()


if __name__ == "__main__":
    print(f"APP_VERSION = {APP_VERSION}")
    print(f"VERSION = {VERSION}")