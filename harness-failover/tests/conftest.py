import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))


import time

import pytest


@pytest.fixture
def host_tz(monkeypatch):
    """Pin the host time zone (codex prints its reset time in host local time). Restored after the test."""
    def set_tz(name):
        monkeypatch.setenv("TZ", name)
        time.tzset()
    yield set_tz
    monkeypatch.undo()
    time.tzset()
