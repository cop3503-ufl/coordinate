import os

import pytest

from src.env import ensure_string


def test_basic_env():
    os.environ["TEST_ENV"] = "test"
    assert ensure_string("TEST_ENV") == "test"
    assert ensure_string(["X", "TEST_ENV"]) == "test"
    assert ensure_string(["TEST_ENV", "X"]) == "test"


def test_exception():
    with pytest.raises(ValueError):
        ensure_string("X")
    with pytest.raises(ValueError):
        ensure_string(["X", "Y"])
    with pytest.raises(ValueError):
        ensure_string(["X", "Y"], required=True)


def test_non_required():
    assert ensure_string("X", required=False) is None
    assert ensure_string(["X", "Y"], required=False) is None
    assert ensure_string(["X", "Y"], required=False) is None
    os.environ["TEST_ENV_TWO"] = "test_two"
    assert ensure_string("TEST_ENV_TWO", required=False) == "test_two"
