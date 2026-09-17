from __future__ import annotations

import logging
import os
import sys
from typing import Any

log = logging.getLogger(__name__)


def _try_import(name: str) -> Any | None:
    try:
        return __import__(name)
    except ImportError:
        return None
    except Exception as e:
        log.debug("unexpected error importing %s: %s", name, e)
        return None


def is_mineru_sdk_available() -> bool:
    return _try_import("mineru") is not None


def is_markitdown_available() -> bool:
    return _try_import("markitdown") is not None


def is_markitdown_ocr_plugin_available() -> bool:
    return _try_import("markitdown_ocr") is not None


def is_network_available(timeout: float = 2.0) -> bool:
    import socket

    try:
        with socket.create_connection(("mineru.net", 443), timeout=timeout):
            return True
    except OSError:
        return False
    except Exception as e:
        log.debug("network check failed: %s", e)
        return False


def python_version_ok(major: int = 3, minor: int = 12) -> bool:
    return sys.version_info >= (major, minor)


def mineru_token_present() -> bool:
    return bool(os.environ.get("MINERU_TOKEN"))


def get_available_capability() -> dict[str, Any]:
    return {
        "python_312_plus": python_version_ok(),
        "mineru_sdk": is_mineru_sdk_available(),
        "mineru_token_present": mineru_token_present(),
        "markitdown": is_markitdown_available(),
        "markitdown_ocr_plugin": is_markitdown_ocr_plugin_available(),
        "network": is_network_available(),
    }
