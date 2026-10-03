"""Update implementation and settings together, using reviewed atomic writes.

settings.json must select an eight-code-point name limit and trimming.
Read settings on each call; do not hard-code the cap in this module.
Preserve the greeting signature, prefix text and prefix-first TypeErrors.
Only service.py/settings.json are editable; tests/license are protected.
"""


def normalize_name(name):
    if not isinstance(name, str):
        raise TypeError("name must be text")
    return name.strip().casefold()[:20]


def format_greeting(name, prefix="Hello"):
    if not isinstance(prefix, str):
        raise TypeError("prefix must be text")
    return f"{prefix}: {normalize_name(name)}"
