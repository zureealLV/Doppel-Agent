"""Immutable JSON inputs shared by protocol, fixtures and result provenance."""

from collections.abc import Mapping
import json
from types import MappingProxyType


def freeze_json(value):
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("JSON input keys must be strings")
        return MappingProxyType({key: freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json(item) for item in value)
    if value is None or type(value) in {str, bool, int, float}:
        # Canonical serialization separately rejects non-finite numbers.
        return value
    raise ValueError("unsupported JSON input type")


def thaw_json(value):
    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [thaw_json(item) for item in value]
    return value


def canonical_json(value):
    return json.dumps(thaw_json(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
