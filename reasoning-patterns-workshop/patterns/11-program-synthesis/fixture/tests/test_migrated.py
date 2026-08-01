"""Acceptance tests for the MIGRATED module `config.py` (which the synthesis
loop writes into the workspace). READ-ONLY: the harness checksums this file
every round — §16's guarantee that passing tests means the CODE moved, not
the goalposts."""
import pytest

import config


SAMPLE = """
# comment line
name = orders
retries=3
debug = true
db.host = local
db.port=5432
"""


def test_flat_string_preserved():
    c = config.parse_config(SAMPLE)
    assert config.get(c, "name") == "orders"

def test_int_typed():
    c = config.parse_config(SAMPLE)
    assert config.get(c, "retries") == 3

def test_bool_typed():
    c = config.parse_config(SAMPLE)
    assert config.get(c, "debug") is True

def test_nested_access():
    c = config.parse_config(SAMPLE)
    assert config.get(c, "db.host") == "local"
    assert config.get(c, "db.port") == 5432

def test_comments_and_blanks_ignored():
    c = config.parse_config("# only\n\n")
    assert c == {}

def test_missing_key_raises():
    c = config.parse_config(SAMPLE)
    with pytest.raises(KeyError):
        config.get(c, "nope")

def test_missing_key_default():
    c = config.parse_config(SAMPLE)
    assert config.get(c, "nope", default="x") == "x"

def test_malformed_line_raises():
    with pytest.raises(ValueError):
        config.parse_config("just words no equals")
