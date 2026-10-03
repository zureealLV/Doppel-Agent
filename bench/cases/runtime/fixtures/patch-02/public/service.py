"""Public facade: refactor both files without changing signatures or behavior.

Use dynamic helpers.normalize_name and helpers.format_greeting delegation.
Consolidate repeated text validation in helpers; preserve prefix-first errors.
Only service.py/helpers.py are editable; tests and license stay unchanged.
"""


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
