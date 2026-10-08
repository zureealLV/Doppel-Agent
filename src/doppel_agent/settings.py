"""Local provider settings with Windows DPAPI protection for API keys."""

from __future__ import annotations

import base64
import ctypes
import json
import math
import os
import threading
from collections.abc import Callable
from contextlib import contextmanager
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from .billing_tariff import canonical_tariff


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


class SettingsPersistenceError(RuntimeError):
    """Fixed original settings failure, never private file/key/exception text."""

    def __init__(self, source: SettingsStore | None = None):
        super().__init__("provider_settings_persistence_unavailable")
        self.source = source  # Private original source, never API/report/provider metadata.


@dataclass
class _SettingsFileLifetime:
    stream: Any = None
    owner_thread: int = field(default_factory=threading.get_ident)
    open_attempted: bool = False
    enter_returned: bool = False
    close_attempted: bool = False
    close_returned: bool = False


class SettingsStore:
    def __init__(self, path: Path, *, failure: Callable[[], None] | None = None,
                 cleanup_failure: Callable[[SettingsStore], None] | None = None):
        self._failure, self._cleanup_failure = failure, cleanup_failure
        self._mutation_lock = threading.RLock()
        self._failed = threading.Event()
        self._cleanup_uncertain = threading.Event()
        self._resource_cleanup_uncertain = threading.Event()
        self._lifetime_lock = threading.Lock()
        self._unresolved_files: dict[int, _SettingsFileLifetime] = {}
        try:
            self.path = path.resolve()
        except OSError:
            self._unavailable()

    @property
    def cleanup_uncertain(self) -> bool:
        # Inherited conservative IO/publication gate, not physical resource proof.
        return self._cleanup_uncertain.is_set()

    @property
    def failed(self) -> bool:
        return self._failed.is_set()

    @property
    def resource_cleanup_uncertain(self) -> bool:
        return self._resource_cleanup_uncertain.is_set()

    def _mark_failed(self, *, cleanup: bool = False) -> None:
        if cleanup:
            self._cleanup_uncertain.set()
        if self.failed:
            return
        self._failed.set()
        if self._failure is not None:
            try:
                self._failure()  # Same original owner latch; no lookup/IO/reset.
            except BaseException:
                self._cleanup_uncertain.set()

    def _unavailable(self, *, cleanup: bool = False) -> NoReturn:
        self._mark_failed(cleanup=cleanup)
        raise SettingsPersistenceError(self) from None

    def _check_admission(self) -> None:
        if self.failed:
            raise SettingsPersistenceError(self)  # No same-source read/save/DPAPI reset authority.

    def _retain_uncertain(self, frame: _SettingsFileLifetime) -> None:
        self._resource_cleanup_uncertain.set()
        with self._lifetime_lock:
            self._unresolved_files[id(frame)] = frame
        self._mark_failed(cleanup=True)
        if self._cleanup_failure is not None:
            try:
                self._cleanup_failure(self)  # Exact original source/stream, no IO/second close.
            except BaseException:
                pass

    def check_resource_cleanup(self) -> None:
        if self.resource_cleanup_uncertain:
            raise SettingsPersistenceError(self)

    def _close_original(self, frame: _SettingsFileLifetime, details=(None, None, None)) -> None:
        if frame.close_attempted:
            if frame.close_returned:
                return
            raise SettingsPersistenceError(self)  # Never retry SAME unknown original exit/close.
        if frame.stream is None:
            self._retain_uncertain(frame)
            raise SettingsPersistenceError(self)  # Unreturned opaque handle has no known close method.
        frame.close_attempted = True  # BEFORE original exit/close protocol.
        try:
            if frame.enter_returned:
                frame.stream.__exit__(*details)
            else:
                frame.stream.close()  # Original enter failed, SAME allocated handle still needs close.
            frame.close_returned = True
        except BaseException:
            self._retain_uncertain(frame)
            raise SettingsPersistenceError(self) from None

    @contextmanager
    def _original_stream(self, path: Path, mode: str, *, missing_ok: bool = False):
        self._check_admission()
        frame = _SettingsFileLifetime()
        frame.open_attempted = True  # BEFORE SAME original Path.open factory.
        try:
            stream = path.open(mode, encoding='utf-8')
        except FileNotFoundError:
            if missing_ok:
                yield None  # Only missing AT original OPEN, never read/exit/replace.
                return
            self._retain_uncertain(frame)
            raise SettingsPersistenceError(self) from None
        except FileExistsError:
            if mode == 'x':
                self._unavailable(cleanup=True)  # Known exclusive-open refusal; preserve original temp.
            self._retain_uncertain(frame)
            raise SettingsPersistenceError(self) from None
        except BaseException:
            self._retain_uncertain(frame)  # Opaque original allocation did not return a handle.
            raise SettingsPersistenceError(self) from None
        frame.stream = stream
        try:
            original = stream.__enter__()
            frame.enter_returned = True
            if original is None:
                raise ValueError('settings_enter_not_acknowledged')  # Allocated source isn't OPEN absence.
        except BaseException:
            self._mark_failed(cleanup=True)  # Fault BEFORE SAME setup close.
            self._close_original(frame)
            self._unavailable(cleanup=True)
        try:
            yield original
        except BaseException as exc:
            cleanup = not (mode == 'r' and isinstance(exc, UnicodeError))
            self._mark_failed(cleanup=cleanup)  # Original IO/receipt fault BEFORE exit.
            self._close_original(frame, (type(exc), exc, exc.__traceback__))
            self._unavailable(cleanup=cleanup)  # Suppressing exit cannot invent known publication.
        else:
            self._close_original(frame)

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate settings field")
            result[key] = value
        return result

    @staticmethod
    def _reject_constant(_constant: str) -> NoReturn:
        raise ValueError("nonfinite settings value")

    def _read(self) -> dict:
        self._check_admission()
        # SAME Path.read_text open/read/exit protocol expanded for exact source
        # retention; a late FileNotFound must not fabricate empty defaults.
        with self._original_stream(self.path, 'r', missing_ok=True) as stream:
            if stream is None:
                return {}
            raw = stream.read()
            if type(raw) is not str:
                raise ValueError('settings_read_not_acknowledged')
        try:
            value = json.loads(raw, object_pairs_hook=self._unique_object, parse_constant=self._reject_constant)
        except (ValueError, RecursionError):
            self._unavailable()
        if not isinstance(value, dict):
            self._unavailable()
        return value

    def recovery_public(self) -> dict:
        """Original held-owner startup metadata, no stale temp deletion/adoption.

        A leftover original temp cannot prove replace/finish or become a retry
        grant. Known stale metadata quarantine is not this lifetime's file drain.
        """
        with self._mutation_lock:
            self._check_admission()
            try:
                self.path.with_suffix(".tmp").lstat()
            except FileNotFoundError:
                return self.public()
            except Exception:
                self._unavailable(cleanup=True)
            self._unavailable()

    def _normalized(self) -> dict:
        data = self._read()
        profiles = data.get("profiles")
        if "profiles" in data:
            if not isinstance(profiles, list) or not profiles or any(
                not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]
                for item in profiles
            ):
                self._unavailable()
            identities = {item["id"] for item in profiles}
            if len(identities) != len(profiles):
                self._unavailable()
            active = data.get("active_profile_id", profiles[0]["id"])
            if not isinstance(active, str) or active not in identities:
                self._unavailable()
            # Validate original public projection BEFORE any save/delete write.
            # Corrupt tariffs remain unknown; malformed legacy floats do not.
            for profile in profiles:
                self._public_profile(profile)
            return {"active_profile_id": active, "profiles": profiles}
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
        self._check_admission()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            # SAME original temp, exclusively acquired; never truncate another
            # original store's unsettled writer or a previous failed temp.
            with self._original_stream(temporary, 'x') as stream:
                payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
                written = stream.write(payload)
                if type(written) is not int or written != len(payload):
                    raise ValueError('settings_write_not_acknowledged')
                stream.flush()
                os.fsync(stream.fileno())
            # Original stream must close before replace/returned public response.
            # Keep failed original temporary source; no delete/retry/repair.
            temporary.replace(self.path)
        except SettingsPersistenceError:
            raise
        except BaseException:
            self._unavailable(cleanup=True)

    def _public_profile(self, profile: dict) -> dict:
        # Editable declaration belongs to private settings UI, not redacted
        # reports. Corrupt/legacy/default prices never upgrade to a tariff.
        try:
            tariff = canonical_tariff(profile["billing_tariff"]) if profile.get("billing_tariff") is not None else None
        except ValueError:
            tariff = None
        raw_prices = (profile.get("input_price", 0), profile.get("output_price", 0))
        if any(isinstance(value, bool) or value is not None and not isinstance(value, (int, float, str))
               for value in raw_prices):
            self._unavailable()
        try:
            input_price, output_price = (float(value or 0) for value in raw_prices)
        except (TypeError, ValueError, OverflowError):
            self._unavailable()
        if not math.isfinite(input_price) or not math.isfinite(output_price) or input_price < 0 or output_price < 0:
            self._unavailable()
        return {
            "id": profile["id"],
            "name": profile.get("name", profile.get("model", "未命名模型")),
            "provider": profile.get("provider", "openai"),
            "preset": profile.get("preset", "openai"),
            "base_url": profile.get("base_url", ""),
            "model": profile.get("model", ""),
            "input_price": input_price,
            "output_price": output_price,
            "billing_tariff": tariff,
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
        # SAME store/read-modify-write/public response, not just replace lock.
        with self._mutation_lock:
            self._check_admission()  # Before new private payload/key/DPAPI work.
            return self._save_profile(config, profile_id=profile_id, api_key=api_key, forget_key=forget_key)

    def _save_profile(
        self, config: dict, *, profile_id: str | None = None,
        api_key: str = "", forget_key: bool = False,
    ) -> dict:
        provider = config.get("provider")
        preset = config.get("preset", "deepseek")
        base_url = config.get("base_url", "")
        model = config.get("model", "")
        name = config.get("name", model or "未命名模型")
        tariff_supplied = "billing_tariff" in config
        declared = config.get("billing_tariff")
        if tariff_supplied and declared is not None:
            if config.get("billing_tariff_confirmed") is not True:
                raise ValueError("billing_tariff_confirmation_required")
            declared = canonical_tariff(declared)  # BEFORE original read/write/DPAPI
        elif not tariff_supplied and "billing_tariff_confirmed" in config:
            raise ValueError("billing_tariff_confirmation_required")
        # Compatibility numeric profile values are not a frozen billing receipt:
        # no currency/date/source/cache-price contract exists on these fields.
        # Reject nonfinite/bool/container values before any original state write.
        raw_prices = (config.get("input_price", 0), config.get("output_price", 0))
        if any(isinstance(value, bool) or value is not None and not isinstance(value, (int, float, str))
               for value in raw_prices):
            raise ValueError("model prices must be finite nonnegative numbers")
        try:
            input_price, output_price = (float(value or 0) for value in raw_prices)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("model prices must be finite nonnegative numbers") from exc
        if (
            provider not in ("openai", "mock")
            or not all(isinstance(item, str) for item in (preset, base_url, model, name, api_key))
            or not math.isfinite(input_price) or not math.isfinite(output_price)
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
        if not tariff_supplied and all(existing.get(key) == profile[key] for key in ("provider", "base_url", "model")):
            # Editing a name/compatibility float cannot change declared rates.
            # Identity changes cannot silently apply old tariffs to another model.
            declared = self._public_profile(existing)["billing_tariff"]
        profile["billing_tariff"] = declared
        if not forget_key and existing.get("api_key_dpapi"):
            profile["api_key_dpapi"] = existing["api_key_dpapi"]
        if api_key and not forget_key:
            profile["api_key_dpapi"] = _protect(api_key)
        data["profiles"] = [profile if item["id"] == wanted else item for item in data["profiles"]]
        data["active_profile_id"] = wanted
        self._write(data)
        return self.public()

    def delete_profile(self, profile_id: str) -> dict:
        with self._mutation_lock:
            self._check_admission()
            return self._delete_profile(profile_id)

    def _delete_profile(self, profile_id: str) -> dict:
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
