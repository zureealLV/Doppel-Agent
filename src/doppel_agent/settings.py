"""Local provider settings with Windows DPAPI protection for API keys."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _protect(value: str) -> str:
    if os.name != "nt":
        raise RuntimeError("API key persistence currently requires Windows DPAPI")
    raw = value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source), "Doppel Agent API key", None, None, None, 0x1, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect(value: str) -> str:
    raw = base64.b64decode(value, validate=True)
    buffer = ctypes.create_string_buffer(raw)
    source = _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(target)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


class SettingsStore:
    def __init__(self, path: Path):
        self.path = path.resolve()

    def _read(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def public(self) -> dict:
        data = self._read()
        return {
            "provider": data.get("provider", "openai"),
            "preset": data.get("preset", "deepseek"),
            "base_url": data.get("base_url", "https://api.deepseek.com"),
            "model": data.get("model", "deepseek-flash"),
            "api_key_saved": bool(data.get("api_key_dpapi")),
            "key_protection": "Windows DPAPI" if os.name == "nt" else "unavailable",
        }

    def api_key(self) -> str:
        encrypted = self._read().get("api_key_dpapi")
        if not encrypted:
            return ""
        try:
            return _unprotect(encrypted)
        except (ValueError, OSError):
            raise RuntimeError("saved API key cannot be decrypted by this Windows user") from None

    def save(self, config: dict, *, api_key: str = "", forget_key: bool = False) -> dict:
        provider = config.get("provider")
        preset = config.get("preset", "deepseek")
        base_url = config.get("base_url", "")
        model = config.get("model", "")
        if provider not in ("openai", "mock") or not all(isinstance(item, str) for item in (preset, base_url, model, api_key)):
            raise ValueError("invalid provider settings")
        current = self._read()
        data = {"provider": provider, "preset": preset, "base_url": base_url, "model": model}
        if not forget_key and current.get("api_key_dpapi"):
            data["api_key_dpapi"] = current["api_key_dpapi"]
        if api_key and not forget_key:
            data["api_key_dpapi"] = _protect(api_key)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return self.public()
