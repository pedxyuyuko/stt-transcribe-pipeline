import os

os.environ.setdefault("SKIP_AUTH", "1")


def pytest_load_initial_conftests(early_config, parser, args):
    """Runs before conftest.py is loaded, so before `from main import app`."""
    os.environ.setdefault("SKIP_AUTH", "1")
