"""Existing duplicated implementation; consolidate validation here."""


def normalize_name(name):
    if not isinstance(name, str):
        raise TypeError("name must be text")
    return name.strip().casefold()


def format_greeting(name, prefix="Hello"):
    if not isinstance(prefix, str):
        raise TypeError("prefix must be text")
    if not isinstance(name, str):
        raise TypeError("name must be text")
    return f"{prefix}: {name.strip().casefold()}"
