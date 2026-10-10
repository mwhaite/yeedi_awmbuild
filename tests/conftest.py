"""Conftest: make tests importable without a full HA runtime.

The integration's __init__.py and coordinator.py have lazy HA imports
(behind TYPE_CHECKING and inside __init__) so test collection works.
If you import a module that doesn't use lazy imports (like
homeassistant.config_entries directly), it will fail; we never do that here.
"""
import sys
import os

# Make `custom_components` importable as a top-level package from project root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
