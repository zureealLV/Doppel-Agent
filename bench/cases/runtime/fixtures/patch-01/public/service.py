def normalize_name(name):
    if not isinstance(name, str):
        raise TypeError("name must be text")
    return name.strip()
