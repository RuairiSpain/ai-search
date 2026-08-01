"""LEGACY module (the migration source). Flat key=value parsing, everything a
string, silent None on missing keys. Do not edit — the synthesis loop reads it."""


def parse_config(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def get(config, key):
    return config.get(key)
