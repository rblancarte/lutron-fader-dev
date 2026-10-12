"""Tests for service routing and entry unload in __init__.py."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lutron-fader'))

import custom_components.lutron_fader as integration
from custom_components.lutron_fader.const import DOMAIN, SERVICE_FADE_TO
from homeassistant.exceptions import ServiceValidationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeRegistry:
    """Minimal entity registry: {entity_id: (platform, unique_id)}."""

    def __init__(self, entities):
        self._entities = entities

    def async_get(self, entity_id):
        if entity_id not in self._entities:
            return None
        platform, unique_id = self._entities[entity_id]
        return SimpleNamespace(platform=platform, unique_id=unique_id)

    def async_get_entity_id(self, domain, platform, unique_id):
        for entity_id, (plat, uid) in self._entities.items():
            if plat == platform and uid == unique_id:
                return entity_id
        return None


def make_hass(entries=(), connection=None):
    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    for entry in entries:
        hass.data[DOMAIN][entry.entry_id] = {"connection": connection, "entry": entry}
    hass.config_entries.async_entries = MagicMock(return_value=list(entries))
    hass.services.has_service = MagicMock(return_value=False)
    hass.services.async_call = AsyncMock()
    return hass


def make_entry(entry_id="e1", data=None):
    return SimpleNamespace(entry_id=entry_id, data=data or {})


async def register_and_get_fade_to(hass, registry):
    with patch.object(integration.er, "async_get", return_value=registry):
        await integration._async_setup_services(hass)
    for call in hass.services.async_register.call_args_list:
        if call.args[1] == SERVICE_FADE_TO:
            return call.args[2]
    raise AssertionError("fade_to not registered")


async def call_fade_to(handler, registry, **data):
    with patch.object(integration.er, "async_get", return_value=registry):
        await handler(SimpleNamespace(data=data))


# ---------------------------------------------------------------------------
# fade_to routing
# ---------------------------------------------------------------------------

class TestFadeToRouting:
    @pytest.mark.asyncio
    async def test_own_entity_routes_through_light_service(self):
        registry = FakeRegistry({"light.bedroom": (DOMAIN, "lutron_fader_zone_28")})
        connection = AsyncMock()
        hass = make_hass([make_entry()], connection)
        handler = await register_and_get_fade_to(hass, registry)

        await call_fade_to(handler, registry, entity_id="light.bedroom", brightness=40, fade_time=600)

        hass.services.async_call.assert_awaited_once_with(
            "light", "turn_on",
            {"entity_id": "light.bedroom", "brightness_pct": 40, "transition": 600},
            blocking=True,
        )
        connection.set_light_level.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_brightness_routes_to_turn_off(self):
        registry = FakeRegistry({"light.bedroom": (DOMAIN, "lutron_fader_zone_28")})
        hass = make_hass([make_entry()], AsyncMock())
        handler = await register_and_get_fade_to(hass, registry)

        await call_fade_to(handler, registry, zone_id=28, brightness=0, fade_time=60)

        hass.services.async_call.assert_awaited_once_with(
            "light", "turn_off", {"entity_id": "light.bedroom", "transition": 60}, blocking=True,
        )

    @pytest.mark.asyncio
    async def test_caseta_entity_resolved_via_zone_mappings(self):
        registry = FakeRegistry({
            "light.caseta_lamp": ("lutron_caseta", "abc"),
            "light.fader_lamp": (DOMAIN, "lutron_fader_zone_5"),
        })
        entry = make_entry(data={"zone_mappings": {"light.caseta_lamp": 5}})
        hass = make_hass([entry], AsyncMock())
        handler = await register_and_get_fade_to(hass, registry)

        await call_fade_to(handler, registry, entity_id="light.caseta_lamp", brightness=70, fade_time=10)

        assert hass.services.async_call.await_args.args[2]["entity_id"] == "light.fader_lamp"

    @pytest.mark.asyncio
    async def test_unmapped_zone_falls_back_to_connection(self):
        registry = FakeRegistry({})
        connection = AsyncMock()
        connection.set_light_level = AsyncMock(return_value=True)
        hass = make_hass([make_entry()], connection)
        handler = await register_and_get_fade_to(hass, registry)

        await call_fade_to(handler, registry, zone_id=99, brightness=30, fade_time=5)

        connection.set_light_level.assert_awaited_once_with(99, 30, 5)
        hass.services.async_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_entity_raises_validation_error(self):
        registry = FakeRegistry({"light.other": ("hue", "x")})
        hass = make_hass([make_entry()], AsyncMock())
        handler = await register_and_get_fade_to(hass, registry)

        with pytest.raises(ServiceValidationError):
            await call_fade_to(handler, registry, entity_id="light.other", brightness=30, fade_time=5)

    @pytest.mark.asyncio
    async def test_services_registered_once(self):
        hass = make_hass()
        hass.services.has_service = MagicMock(return_value=True)

        await integration._async_setup_services(hass)

        hass.services.async_register.assert_not_called()


# ---------------------------------------------------------------------------
# Unload
# ---------------------------------------------------------------------------

class TestUnload:
    @pytest.mark.asyncio
    async def test_last_entry_unload_removes_services(self):
        entry = make_entry()
        connection = AsyncMock()
        hass = make_hass([entry], connection)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        assert await integration.async_unload_entry(hass, entry) is True

        connection.disconnect.assert_awaited_once()
        assert hass.services.async_remove.call_count == 4

    @pytest.mark.asyncio
    async def test_unload_keeps_services_when_other_entry_remains(self):
        e1, e2 = make_entry("e1"), make_entry("e2")
        hass = make_hass([e1, e2], AsyncMock())
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        await integration.async_unload_entry(hass, e1)

        hass.services.async_remove.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_platform_unload_keeps_connection(self):
        entry = make_entry()
        connection = AsyncMock()
        hass = make_hass([entry], connection)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        assert await integration.async_unload_entry(hass, entry) is False

        connection.disconnect.assert_not_called()
        assert entry.entry_id in hass.data[DOMAIN]

    @pytest.mark.asyncio
    async def test_get_connection_skips_unloaded_entries(self):
        conn = AsyncMock()
        loaded, unloaded = make_entry("loaded"), make_entry("gone")
        hass = make_hass([loaded], conn)
        hass.config_entries.async_entries = MagicMock(return_value=[unloaded, loaded])

        assert integration._get_connection(hass) is conn
