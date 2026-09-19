"""Local provider settings with Windows DPAPI protection for API keys."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from uuid import uuid4


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

    def _normalized(self) -> dict:
        data = self._read()
        profiles = data.get("profiles")
        if isinstance(profiles, list) and profiles:
            valid = [item for item in profiles if isinstance(item, dict) and isinstance(item.get("id"), str)]
            if valid:
                active = data.get("active_profile_id")
                if active not in {item["id"] for item in valid}:
                    active = valid[0]["id"]
                return {"active_profile_id": active, "profiles": valid}
        legacy = {
            "id": "default",
            "name": "DeepSeek",
            "provider": data.get("provider", "openai"),
            "preset": data.get("preset", "deepseek"),
            "base_url": data.get("base_url", "https://api.deepseek.com"),
            "model": data.get("model", "deepseek-flash"),
            "input_price": 0.0,
            "output_price": 0.0,
        }
        if data.get("api_key_dpapi"):
            legacy["api_key_dpapi"] = data["api_key_dpapi"]
        return {"active_profile_id": "default", "profiles": [legacy]}

    def _write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _public_profile(profile: dict) -> dict:
        return {
            "id": profile["id"],
            "name": profile.get("name", profile.get("model", "未命名模型")),
            "provider": profile.get("provider", "openai"),
            "preset": profile.get("preset", "openai"),
            "base_url": profile.get("base_url", ""),
            "model": profile.get("model", ""),
            "input_price": float(profile.get("input_price", 0) or 0),
            "output_price": float(profile.get("output_price", 0) or 0),
            "api_key_saved": bool(profile.get("api_key_dpapi")),
        }

    def public(self) -> dict:
        data = self._normalized()
        profiles = [self._public_profile(item) for item in data["profiles"]]
        active = next(item for item in profiles if item["id"] == data["active_profile_id"])
        return {
            **active,
            "active_profile_id": active["id"],
            "profiles": profiles,
            "key_protection": "Windows DPAPI" if os.name == "nt" else "unavailable",
        }

    def profile(self, profile_id: str | None = None) -> dict:
        data = self._normalized()
        wanted = profile_id or data["active_profile_id"]
        profile = next((item for item in data["profiles"] if item["id"] == wanted), None)
        if profile is None:
            raise ValueError("model profile not found")
        result = self._public_profile(profile)
        result["api_key"] = self.api_key(profile["id"])
        return result

    def api_key(self, profile_id: str | None = None) -> str:
        data = self._normalized()
        wanted = profile_id or data["active_profile_id"]
        profile = next((item for item in data["profiles"] if item["id"] == wanted), None)
        if profile is None:
            raise ValueError("model profile not found")
        encrypted = profile.get("api_key_dpapi")
        if not encrypted:
            return ""
        try:
            return _unprotect(encrypted)
        except (ValueError, OSError):
            raise RuntimeError("saved API key cannot be decrypted by this Windows user") from None

    def save(self, config: dict, *, api_key: str = "", forget_key: bool = False) -> dict:
        return self.save_profile(config, api_key=api_key, forget_key=forget_key)

    def save_profile(
        self, config: dict, *, profile_id: str | None = None,
        api_key: str = "", forget_key: bool = False,
    ) -> dict:
        provider = config.get("provider")
        preset = config.get("preset", "deepseek")
        base_url = config.get("base_url", "")
        model = config.get("model", "")
        name = config.get("name", model or "未命名模型")
        try:
            input_price = float(config.get("input_price", 0) or 0)
            output_price = float(config.get("output_price", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("model prices must be numbers") from exc
        if (
            provider not in ("openai", "mock")
            or not all(isinstance(item, str) for item in (preset, base_url, model, name, api_key))
            or input_price < 0 or output_price < 0
        ):
            raise ValueError("invalid provider settings")
        data = self._normalized()
        wanted = profile_id or config.get("id") or data["active_profile_id"]
        existing = next((item for item in data["profiles"] if item["id"] == wanted), None)
        if existing is None:
            wanted = uuid4().hex
            existing = {"id": wanted}
            data["profiles"].append(existing)
        profile = {
            "id": wanted, "name": " ".join(name.split())[:60] or model,
            "provider": provider, "preset": preset, "base_url": base_url,
            "model": model, "input_price": input_price, "output_price": output_price,
        }
        if not forget_key and existing.get("api_key_dpapi"):
            profile["api_key_dpapi"] = existing["api_key_dpapi"]
        if api_key and not forget_key:
            profile["api_key_dpapi"] = _protect(api_key)
        data["profiles"] = [profile if item["id"] == wanted else item for item in data["profiles"]]
        data["active_profile_id"] = wanted
        self._write(data)
        return self.public()

    def delete_profile(self, profile_id: str) -> dict:
        data = self._normalized()
        remaining = [item for item in data["profiles"] if item["id"] != profile_id]
        if len(remaining) == len(data["profiles"]):
            raise ValueError("model profile not found")
        if not remaining:
            raise ValueError("at least one model profile is required")
        data["profiles"] = remaining
        if data["active_profile_id"] == profile_id:
            data["active_profile_id"] = remaining[0]["id"]
        self._write(data)
        return self.public()
