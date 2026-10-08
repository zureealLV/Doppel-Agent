"""Pure strict JSON framing for stored effect evidence; callers own byte budgets."""

import json


def loads(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate evidence member")
            value[key] = item
        return value

    def constant(_):
        raise ValueError("non-finite evidence")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
