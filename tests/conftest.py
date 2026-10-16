"""Test setup: a real Home Assistant (pytest-homeassistant-custom-component)."""
import os
import sys

import pytest

# Make the integration importable as custom_components.lutron_fader
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lutron-fader"))

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load integrations from custom_components/."""
    yield
