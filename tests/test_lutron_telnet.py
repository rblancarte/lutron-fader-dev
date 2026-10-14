"""Tests for LutronTelnetConnection."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'lutron-fader', 'custom_components', 'lutron_fader'))

from lutron_telnet import (
    ConnState,
    HubError,
    LutronError,
    LutronTelnetConnection,
    NoReply,
    NotConnected,
    _PendingReply,
)
from homeassistant.exceptions import HomeAssistantError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_reader(*lines: bytes) -> AsyncMock:
    """Return a mock StreamReader that yields lines then TimeoutError."""
    reader = AsyncMock()
    reader.read = AsyncMock(side_effect=list(lines))
    reader.readuntil = AsyncMock(side_effect=list(lines) + [asyncio.TimeoutError])
    return reader


def make_writer() -> MagicMock:
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


def make_login_reader() -> AsyncMock:
    reader = AsyncMock()
    reader.read = AsyncMock(side_effect=[b"login: ", b"password: ", b"GNET> "])
    return reader


def make_connection(**kwargs) -> LutronTelnetConnection:
    return LutronTelnetConnection(host="10.0.0.1", **kwargs)


def make_connected(**kwargs) -> LutronTelnetConnection:
    """A connection already in CONNECTED, without background tasks running."""
    conn = make_connection(**kwargs)
    conn._state = ConnState.CONNECTED
    conn._connected_event.set()
    conn._writer = make_writer()
    conn._reader = AsyncMock()
    return conn


@pytest.fixture
def no_background_tasks():
    """Stop CONNECTED from starting the reader and keep-alive tasks."""
    with patch.object(LutronTelnetConnection, '_start_background_tasks'):
        yield


def hub_replies(conn, *replies: str) -> None:
    """Make the mock writer answer each command with the next reply ("" = silence)."""
    remaining = iter(replies)

    def write(data):
        reply = next(remaining)
        if reply:
            pending = conn._pending
            asyncio.get_running_loop().call_soon(pending.future.set_result, reply)

    conn._writer.write = MagicMock(side_effect=write)


# ---------------------------------------------------------------------------
# _expect
# ---------------------------------------------------------------------------

class TestExpect:
    @pytest.mark.asyncio
    async def test_token_found_in_single_chunk(self):
        conn = make_connection()
        conn._reader = make_reader(b"login: ")
        await conn._expect(b"login:")

    @pytest.mark.asyncio
    async def test_token_split_across_chunks(self):
        conn = make_connection()
        conn._reader = AsyncMock()
        conn._reader.read = AsyncMock(side_effect=[b"log", b"in: "])
        await conn._expect(b"login:")

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        conn = make_connection()
        conn._reader = AsyncMock()
        conn._reader.read = AsyncMock(side_effect=asyncio.TimeoutError)
        with pytest.raises((asyncio.TimeoutError, Exception)):
            await conn._expect(b"login:", timeout=0.1)

    @pytest.mark.asyncio
    async def test_connection_close_raises(self):
        conn = make_connection()
        conn._reader = AsyncMock()
        conn._reader.read = AsyncMock(return_value=b"")  # EOF
        with pytest.raises(ConnectionError):
            await conn._expect(b"login:")


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class TestStateMachine:
    def test_starts_disconnected_and_unavailable(self):
        conn = make_connection()
        assert conn.state is ConnState.DISCONNECTED
        assert conn.available is False
        assert conn.is_connected is False

    @pytest.mark.asyncio
    @pytest.mark.parametrize("old,new", [
        (ConnState.DISCONNECTED, ConnState.CONNECTING),
        (ConnState.CONNECTING, ConnState.CONNECTED),
        (ConnState.CONNECTING, ConnState.DISCONNECTED),
        (ConnState.CONNECTED, ConnState.CONNECTING),
        (ConnState.CONNECTED, ConnState.DISCONNECTED),
    ])
    async def test_allowed_transitions(self, old, new, no_background_tasks):
        conn = make_connection()
        conn._state = old
        with patch.object(conn, '_connection_loop', AsyncMock()):
            conn._transition(new, "test")
        assert conn.state is new
        await conn.disconnect()

    @pytest.mark.parametrize("old,new", [
        (ConnState.DISCONNECTED, ConnState.CONNECTED),
        (ConnState.DISCONNECTED, ConnState.DISCONNECTED),
        (ConnState.CONNECTING, ConnState.CONNECTING),
        (ConnState.CONNECTED, ConnState.CONNECTED),
    ])
    def test_illegal_transitions_raise(self, old, new):
        conn = make_connection()
        conn._state = old
        with pytest.raises(RuntimeError):
            conn._transition(new, "test")
        assert conn.state is old

    @pytest.mark.asyncio
    async def test_availability_follows_connected(self, no_background_tasks):
        conn = make_connection()
        availability = []
        conn.add_availability_callback(availability.append)
        conn._state = ConnState.CONNECTING

        conn._transition(ConnState.CONNECTED, "test")
        assert conn.available is True
        assert conn._connected_event.is_set()

        conn._transition(ConnState.DISCONNECTED, "test")
        assert conn.available is False
        assert not conn._connected_event.is_set()

        assert availability == [True, False]

    @pytest.mark.asyncio
    async def test_repeat_loss_report_is_harmless(self):
        conn = make_connected()
        availability = []
        conn.add_availability_callback(availability.append)

        with patch.object(conn, '_connection_loop', AsyncMock()):
            conn._on_connection_lost("first")
            conn._on_connection_lost("second")

        assert conn.state is ConnState.CONNECTING
        assert availability == [False]
        await conn.disconnect()

    def test_loss_report_ignored_when_not_connected(self):
        conn = make_connection()
        conn._on_connection_lost("stray")
        assert conn.state is ConnState.DISCONNECTED


# ---------------------------------------------------------------------------
# connect
# ---------------------------------------------------------------------------

class TestConnect:
    @pytest.mark.asyncio
    async def test_successful_connect(self, no_background_tasks):
        conn = make_connection()

        with patch("asyncio.open_connection", return_value=(make_login_reader(), make_writer())):
            result = await conn.connect()

        assert result is True
        assert conn.state is ConnState.CONNECTED

    @pytest.mark.asyncio
    async def test_wrong_credentials_returns_false_and_keeps_retrying(self):
        conn = make_connection()
        reader = AsyncMock()
        reader.read = AsyncMock(side_effect=[b"login: ", b"password: ", b""])

        with patch("asyncio.open_connection", return_value=(reader, make_writer())):
            result = await conn.connect()

        assert result is False
        assert conn.state is ConnState.CONNECTING
        assert conn._connection_task is not None and not conn._connection_task.done()
        await conn.disconnect()
        assert conn.state is ConnState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_connect_failure_returns_false(self):
        conn = make_connection()
        with patch("asyncio.open_connection", side_effect=OSError("refused")):
            result = await conn.connect()

        assert result is False
        assert conn.is_connected is False
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_connect_failure_closes_socket(self):
        conn = make_connection()
        reader = AsyncMock()
        writer = make_writer()
        reader.read = AsyncMock(side_effect=[b"login: ", b"password: ", b""])

        with patch("asyncio.open_connection", return_value=(reader, writer)):
            assert await conn.connect() is False

        writer.close.assert_called_once()
        assert conn._writer is None
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_already_connected_returns_true(self):
        conn = make_connected()
        with patch("asyncio.open_connection") as mock_open:
            assert await conn.connect() is True
        mock_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_username_and_password(self, no_background_tasks):
        conn = make_connection(username="lutron", password="integration")
        writer = make_writer()

        with patch("asyncio.open_connection", return_value=(make_login_reader(), writer)):
            await conn.connect()

        written = b"".join(c.args[0] for c in writer.write.call_args_list)
        assert b"lutron\r\n" in written
        assert b"integration\r\n" in written

    @pytest.mark.asyncio
    async def test_successful_connect_restores_availability(self, no_background_tasks):
        conn = make_connection()
        availability = []
        conn.add_availability_callback(availability.append)

        with patch("asyncio.open_connection", return_value=(make_login_reader(), make_writer())):
            assert await conn.connect() is True

        assert conn.available is True
        assert availability == [True]


# ---------------------------------------------------------------------------
# Connection loop (backoff)
# ---------------------------------------------------------------------------

class TestConnectionLoop:
    @pytest.mark.asyncio
    async def test_first_connect_tries_immediately_then_backs_off(self, no_background_tasks):
        conn = make_connection()
        conn._state = ConnState.CONNECTING
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep), \
             patch.object(conn, '_open_and_login', AsyncMock(side_effect=[False, False, False, True])):
            await conn._connection_loop(0)

        assert sleeps == [5, 10, 20]
        assert conn.state is ConnState.CONNECTED

    @pytest.mark.asyncio
    async def test_reconnect_after_loss_backs_off_first(self, no_background_tasks):
        conn = make_connection()
        conn._state = ConnState.CONNECTING
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep), \
             patch.object(conn, '_open_and_login', AsyncMock(side_effect=[False, False, True])) as login:
            await conn._connection_loop(5)

        assert login.call_count == 3
        assert sleeps == [5, 10, 20]

    @pytest.mark.asyncio
    async def test_backoff_is_capped(self, no_background_tasks):
        conn = make_connection()
        conn._state = ConnState.CONNECTING
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        with patch("asyncio.sleep", side_effect=fake_sleep), \
             patch.object(conn, '_open_and_login', AsyncMock(side_effect=[False] * 8 + [True])):
            await conn._connection_loop(0)

        assert max(sleeps) == 300


# ---------------------------------------------------------------------------
# disconnect
# ---------------------------------------------------------------------------

class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self):
        conn = make_connected()

        await conn.disconnect()

        assert conn.state is ConnState.DISCONNECTED
        assert conn._writer is None
        assert conn._reader is None

    @pytest.mark.asyncio
    async def test_disconnect_cancels_tasks(self):
        conn = make_connected()
        ping_timer = MagicMock()
        reader_task = MagicMock()
        conn._keepalive_task = ping_timer
        conn._reader_task = reader_task

        await conn.disconnect()

        ping_timer.cancel.assert_called_once()
        reader_task.cancel.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected_is_safe(self):
        conn = make_connection()
        await conn.disconnect()  # Should not raise

    @pytest.mark.asyncio
    async def test_disconnect_stops_retrying_and_is_unavailable(self):
        conn = make_connection()
        with patch("asyncio.open_connection", side_effect=OSError("refused")):
            await conn.connect()
        task = conn._connection_task

        await conn.disconnect()
        await asyncio.sleep(0)

        assert task.cancelled() or task.done()
        assert conn.state is ConnState.DISCONNECTED
        assert conn.available is False


# ---------------------------------------------------------------------------
# Reply slot
# ---------------------------------------------------------------------------

class TestReplySlot:
    def _slot(self, zone):
        return _PendingReply(zone, asyncio.get_event_loop().create_future())

    @pytest.mark.asyncio
    async def test_matches_output_for_its_zone(self):
        slot = self._slot(28)
        assert slot.matches("~OUTPUT,28,1,40.00")
        assert not slot.matches("~OUTPUT,5,1,40.00")

    @pytest.mark.asyncio
    async def test_matches_any_output_without_zone(self):
        assert self._slot(None).matches("~OUTPUT,5,1,40.00")

    @pytest.mark.asyncio
    async def test_matches_error_regardless_of_zone(self):
        assert self._slot(28).matches("~ERROR,Enum=(2, 0x00000002)")

    @pytest.mark.asyncio
    async def test_ignores_other_lines(self):
        assert not self._slot(None).matches("~DEVICE,23,2,3")

    @pytest.mark.asyncio
    async def test_ignores_lines_once_answered(self):
        slot = self._slot(28)
        slot.future.set_result("~OUTPUT,28,1,40.00")
        assert not slot.matches("~OUTPUT,28,1,50.00")

    @pytest.mark.asyncio
    async def test_slot_is_empty_after_reply(self):
        conn = make_connected()
        hub_replies(conn, "~OUTPUT,25,1,50.00")
        await conn.send_command("?OUTPUT,25,1", expected_zone=25)
        assert conn._pending is None

    @pytest.mark.asyncio
    async def test_slot_is_empty_after_timeout(self):
        conn = make_connected()
        hub_replies(conn, "")
        with patch("lutron_telnet.REPLY_TIMEOUT", 0.05), \
             patch.object(conn, '_on_connection_lost'), \
             patch("lutron_telnet.COMMAND_ATTEMPTS", 1):
            with pytest.raises(NoReply):
                await conn.send_command("?OUTPUT,25,1", expected_zone=25)
        assert conn._pending is None

    @pytest.mark.asyncio
    async def test_teardown_fails_pending_reply_with_no_reply(self):
        conn = make_connected()
        conn._pending = self._slot(25)
        future = conn._pending.future

        with patch.object(conn, '_connection_loop', AsyncMock()):
            conn._on_connection_lost("test")

        assert isinstance(future.exception(), NoReply)
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_write_failure_leaves_no_orphaned_reply(self):
        """Found live: a failed write must not leave teardown failing a reply nobody awaits."""
        conn = make_connected()
        conn._writer.drain = AsyncMock(side_effect=ConnectionResetError("Connection lost"))
        slots = []
        conn._writer.write = MagicMock(side_effect=lambda data: slots.append(conn._pending))

        with patch.object(conn, '_connection_loop', AsyncMock()), \
             patch("lutron_telnet.COMMAND_ATTEMPTS", 1):
            with pytest.raises(NoReply):
                await conn.send_command("#OUTPUT,28,1,80,0", expected_zone=28)

        assert conn.state is ConnState.CONNECTING
        assert not slots[0].future.done()  # teardown never touched it
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_loss_while_waiting_raises_no_reply_without_second_report(self):
        conn = make_connected()
        lost_reasons = []

        def write(data):
            # The reader notices the socket died while we wait for the reply
            asyncio.get_running_loop().call_soon(conn._on_connection_lost, "hub closed the connection")

        conn._writer.write = MagicMock(side_effect=write)
        conn.add_availability_callback(lambda up: lost_reasons.append(up))

        with patch.object(conn, '_connection_loop', AsyncMock()), \
             patch("lutron_telnet.COMMAND_ATTEMPTS", 1):
            with pytest.raises(NoReply, match="connection lost"):
                await conn.send_command("?OUTPUT,25,1", expected_zone=25)

        assert lost_reasons == [False]
        await conn.disconnect()


# ---------------------------------------------------------------------------
# _reader_loop
# ---------------------------------------------------------------------------

class TestReaderLoop:
    async def _run_loop_with_lines(self, conn, lines: list[bytes]):
        """Feed lines into the reader loop then stop it."""
        conn._reader = AsyncMock()
        conn._state = ConnState.CONNECTED

        # After all lines, raise CancelledError to stop the loop cleanly
        conn._reader.readuntil = AsyncMock(
            side_effect=lines + [asyncio.CancelledError]
        )
        try:
            await conn._reader_loop()
        except asyncio.CancelledError:
            pass

    def _arm(self, conn, zone=None):
        conn._pending = _PendingReply(zone, asyncio.get_event_loop().create_future())
        return conn._pending.future

    @pytest.mark.asyncio
    async def test_routes_output_to_pending_reply(self):
        conn = make_connection()
        future = self._arm(conn)

        await self._run_loop_with_lines(conn, [b"~OUTPUT,5,1,50.00\n"])

        assert future.result() == "~OUTPUT,5,1,50.00"

    @pytest.mark.asyncio
    async def test_routes_error_to_pending_reply(self):
        conn = make_connection()
        future = self._arm(conn)

        await self._run_loop_with_lines(conn, [b"~ERROR\n"])

        assert future.result() == "~ERROR"

    @pytest.mark.asyncio
    async def test_push_event_with_no_pending_reply(self):
        """Unsolicited ~OUTPUT with no pending command should not crash."""
        conn = make_connection()
        pushes = []
        conn.add_push_callback(lambda line, source: pushes.append((line, source)))

        await self._run_loop_with_lines(conn, [b"~OUTPUT,5,1,75.00\n"])

        assert pushes == [("~OUTPUT,5,1,75.00", "external")]

    @pytest.mark.asyncio
    async def test_skips_bare_gnet_prompt(self):
        conn = make_connection()
        future = self._arm(conn)

        # Bare GNET> line should be skipped; ~OUTPUT resolves the reply
        await self._run_loop_with_lines(conn, [b"GNET>\n", b"~OUTPUT,5,1,25.00\n"])

        assert future.result() == "~OUTPUT,5,1,25.00"

    @pytest.mark.asyncio
    async def test_strips_gnet_prefix_from_response(self):
        conn = make_connection()
        future = self._arm(conn)

        # GNET> prefixed on the same line as ~OUTPUT
        await self._run_loop_with_lines(conn, [b"GNET> ~OUTPUT,5,1,100.00\n"])

        assert future.result() == "~OUTPUT,5,1,100.00"

    @pytest.mark.asyncio
    async def test_every_line_counts_as_activity(self):
        conn = make_connection()
        conn._last_activity = 0.0

        await self._run_loop_with_lines(conn, [b"~DEVICE,23,2,3\n"])

        assert conn._last_activity > 0.0


# ---------------------------------------------------------------------------
# send_command
# ---------------------------------------------------------------------------

class TestSendCommand:
    @pytest.mark.asyncio
    async def test_sends_command_with_crlf(self):
        conn = make_connected()
        hub_replies(conn, "~OUTPUT,25,1,50.00")

        result = await conn.send_command("#OUTPUT,25,1,50,0")

        written = b"".join(c.args[0] for c in conn._writer.write.call_args_list)
        assert written == b"#OUTPUT,25,1,50,0\r\n"
        assert result == "~OUTPUT,25,1,50.00"

    @pytest.mark.asyncio
    async def test_never_connects_on_its_own(self):
        conn = make_connection()

        with patch("asyncio.open_connection") as mock_open:
            with pytest.raises(NotConnected):
                await conn.send_command("#OUTPUT,25,1,50,0")

        mock_open.assert_not_called()
        assert conn.state is ConnState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_while_connecting_raises_not_connected(self):
        conn = make_connection()
        conn._state = ConnState.CONNECTING

        with pytest.raises(NotConnected):
            await conn.send_command("#OUTPUT,25,1,50,0")

    @pytest.mark.asyncio
    async def test_socket_error_reports_loss_then_gives_up(self):
        conn = make_connected()
        conn._writer.drain = AsyncMock(side_effect=OSError("broken pipe"))

        with patch.object(conn, '_connection_loop', AsyncMock()), \
             patch("lutron_telnet.RECONNECT_WAIT", 0.05):
            with pytest.raises(NotConnected):
                await conn.send_command("#OUTPUT,25,1,50,0")

        assert conn.state is ConnState.CONNECTING
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_timeout_reports_loss(self):
        conn = make_connected()
        hub_replies(conn, "")

        with patch("lutron_telnet.REPLY_TIMEOUT", 0.05), \
             patch.object(conn, '_on_connection_lost') as mock_lost, \
             patch("lutron_telnet.COMMAND_ATTEMPTS", 1):
            with pytest.raises(NoReply):
                await conn.send_command("?OUTPUT,25,1", expected_zone=25)

        mock_lost.assert_called_once()

    @pytest.mark.asyncio
    async def test_hub_error_is_raised_with_code_and_not_retried(self):
        conn = make_connected()
        hub_replies(conn, "~ERROR,Enum=(2, 0x00000002)")

        with pytest.raises(HubError) as exc:
            await conn.send_command("?OUTPUT,999,1", expected_zone=999)

        assert exc.value.code == 2
        assert conn._writer.write.call_count == 1
        assert conn.state is ConnState.CONNECTED  # link is fine

    @pytest.mark.asyncio
    async def test_no_reply_resends_after_reconnect(self):
        conn = make_connected()
        hub_replies(conn, "", "~OUTPUT,25,1,50.00")

        def fake_lost(reason):
            # The manager drops the link and comes straight back
            conn._connected_event.clear()
            asyncio.get_running_loop().call_soon(conn._connected_event.set)

        with patch("lutron_telnet.REPLY_TIMEOUT", 0.05), \
             patch.object(conn, '_on_connection_lost', side_effect=fake_lost):
            result = await conn.send_command("#OUTPUT,25,1,50,0", expected_zone=25)

        assert result == "~OUTPUT,25,1,50.00"
        assert conn._writer.write.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_gives_up_if_link_does_not_return(self):
        conn = make_connected()
        hub_replies(conn, "")

        def fake_lost(reason):
            conn._state = ConnState.CONNECTING
            conn._connected_event.clear()

        with patch("lutron_telnet.REPLY_TIMEOUT", 0.05), \
             patch.object(conn, '_on_connection_lost', side_effect=fake_lost), \
             patch("lutron_telnet.RECONNECT_WAIT", 0.05):
            with pytest.raises(NotConnected):
                await conn.send_command("#OUTPUT,25,1,50,0")

        assert conn._writer.write.call_count == 1

    @pytest.mark.asyncio
    async def test_commands_go_one_at_a_time(self):
        conn = make_connected()
        hub_replies(conn, "~OUTPUT,25,1,50.00", "~OUTPUT,26,1,60.00")
        in_flight = []

        def write(data, inner=conn._writer.write.side_effect):
            in_flight.append(conn._pending.zone)
            inner(data)

        conn._writer.write = MagicMock(side_effect=write)

        results = await asyncio.gather(
            conn.send_command("?OUTPUT,25,1", expected_zone=25),
            conn.send_command("?OUTPUT,26,1", expected_zone=26),
        )

        assert results == ["~OUTPUT,25,1,50.00", "~OUTPUT,26,1,60.00"]
        assert in_flight == [25, 26]

    @pytest.mark.parametrize("response,code", [
        ("~ERROR,2", 2),
        ("~ERROR,Enum=(2, 0x00000002)", 2),  # what a real Caseta hub sends
        ("~ERROR,Enum=(3, 0x00000003)", 3),
        ("~ERROR", None),
    ])
    def test_hub_error_code_parsing(self, response, code):
        assert HubError("?OUTPUT,250,1", response).code == code

    def test_errors_are_home_assistant_errors(self):
        for cls in (LutronError, NotConnected, NoReply, HubError):
            assert issubclass(cls, HomeAssistantError)


# ---------------------------------------------------------------------------
# set_light_level
# ---------------------------------------------------------------------------

class TestSecondsToLipTime:
    def test_zero(self):
        assert LutronTelnetConnection._seconds_to_lip_time(0) == "00:00:00"

    def test_under_60(self):
        assert LutronTelnetConnection._seconds_to_lip_time(10) == "00:00:10"
        assert LutronTelnetConnection._seconds_to_lip_time(59) == "00:00:59"

    def test_exactly_60(self):
        assert LutronTelnetConnection._seconds_to_lip_time(60) == "00:01:00"

    def test_minutes(self):
        assert LutronTelnetConnection._seconds_to_lip_time(90) == "00:01:30"
        assert LutronTelnetConnection._seconds_to_lip_time(1800) == "00:30:00"

    def test_hours(self):
        assert LutronTelnetConnection._seconds_to_lip_time(3600) == "01:00:00"
        assert LutronTelnetConnection._seconds_to_lip_time(14400) == "04:00:00"

    def test_mixed(self):
        assert LutronTelnetConnection._seconds_to_lip_time(3661) == "01:01:01"


class TestSetLightLevel:
    @pytest.mark.asyncio
    async def test_correct_command_format(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,50.00") as mock_send:
            await conn.set_light_level(zone_id=25, brightness=50, fade_time=1800)
        mock_send.assert_called_once_with("#OUTPUT,25,1,50,00:30:00", expected_zone=25)

    @pytest.mark.asyncio
    async def test_returns_none_on_success(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,50.00"):
            assert await conn.set_light_level(25, 50, 0) is None

    @pytest.mark.asyncio
    async def test_propagates_send_errors(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', AsyncMock(side_effect=NoReply("no reply"))):
            with pytest.raises(NoReply):
                await conn.set_light_level(25, 50, 0)

    @pytest.mark.asyncio
    async def test_default_fade_time_is_zero(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,50.00") as mock_send:
            await conn.set_light_level(25, 50)
        mock_send.assert_called_once_with("#OUTPUT,25,1,50,00:00:00", expected_zone=25)

    @pytest.mark.asyncio
    async def test_under_60s_uses_seconds_field(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,50.00") as mock_send:
            await conn.set_light_level(25, 50, 10)
        mock_send.assert_called_once_with("#OUTPUT,25,1,50,00:00:10", expected_zone=25)

    @pytest.mark.asyncio
    async def test_60s_formats_as_minutes(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,50.00") as mock_send:
            await conn.set_light_level(25, 50, 60)
        mock_send.assert_called_once_with("#OUTPUT,25,1,50,00:01:00", expected_zone=25)


# ---------------------------------------------------------------------------
# query_light_level
# ---------------------------------------------------------------------------

class TestQueryLightLevel:
    @pytest.mark.asyncio
    async def test_correct_command_format(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,75.00") as mock_send:
            await conn.query_light_level(25)
        mock_send.assert_called_once_with("?OUTPUT,25,1", expected_zone=25)

    @pytest.mark.asyncio
    async def test_parses_brightness(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,75.00"):
            result = await conn.query_light_level(25)
        assert result == 75.0

    @pytest.mark.asyncio
    async def test_parses_zero_brightness(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1,0.00"):
            result = await conn.query_light_level(25)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_propagates_send_errors(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', AsyncMock(side_effect=NotConnected("down"))):
            with pytest.raises(NotConnected):
                await conn.query_light_level(25)

    @pytest.mark.asyncio
    async def test_malformed_response_raises(self):
        conn = make_connection()
        with patch.object(conn, 'send_command', return_value="~OUTPUT,25,1"):
            with pytest.raises(LutronError):
                await conn.query_light_level(25)


# ---------------------------------------------------------------------------
# discover_zones
# ---------------------------------------------------------------------------

class TestDiscoverZones:
    @pytest.mark.asyncio
    async def test_missing_zones_are_skipped(self):
        conn = make_connection()

        async def fake_query(zone_id):
            if zone_id == 2:
                return 40.0
            raise HubError(f"?OUTPUT,{zone_id},1", "~ERROR,2")

        with patch.object(conn, 'query_light_level', side_effect=fake_query), \
             patch("asyncio.sleep", AsyncMock()):
            zones = await conn.discover_zones(max_zones=3)

        assert zones == {2: "Zone 2"}

    @pytest.mark.asyncio
    async def test_link_errors_abort_discovery(self):
        conn = make_connection()
        with patch.object(conn, 'query_light_level', AsyncMock(side_effect=NotConnected("down"))):
            with pytest.raises(NotConnected):
                await conn.discover_zones(max_zones=3)


# ---------------------------------------------------------------------------
# Connection loss / reconnect
# ---------------------------------------------------------------------------

class TestConnectionLoss:
    @pytest.mark.asyncio
    async def test_reader_reset_error_marks_lost_and_reconnects(self):
        """A non-IncompleteRead socket error must not leave a zombie 'connected' state."""
        conn = make_connected()
        conn._reader.readuntil = AsyncMock(side_effect=ConnectionResetError("reset by peer"))
        availability = []
        conn.add_availability_callback(availability.append)

        with patch.object(conn, '_connection_loop', AsyncMock()) as loop:
            await conn._reader_loop()

        assert conn.state is ConnState.CONNECTING
        assert conn.available is False
        assert conn._writer is None
        assert availability == [False]
        loop.assert_called_once_with(5)
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_incomplete_read_reconnects(self):
        conn = make_connected()
        conn._reader.readuntil = AsyncMock(side_effect=asyncio.IncompleteReadError(b"", 1))

        with patch.object(conn, '_connection_loop', AsyncMock()) as loop:
            await conn._reader_loop()

        assert conn.state is ConnState.CONNECTING
        loop.assert_called_once()
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_explicit_disconnect_does_not_reconnect(self):
        conn = make_connected()

        with patch.object(conn, '_connection_loop', AsyncMock()) as loop:
            await conn.disconnect()

        assert conn.state is ConnState.DISCONNECTED
        loop.assert_not_called()


# ---------------------------------------------------------------------------
# Keep-alive
# ---------------------------------------------------------------------------

class TestKeepAlive:
    async def _run(self, conn, sleeps_before_stop=1):
        """Run the keep-alive with sleep() recorded; stop it after N sleeps."""
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)
            if len(sleeps) > sleeps_before_stop:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=fake_sleep):
            await conn._keepalive_loop()
        return sleeps

    @pytest.mark.asyncio
    async def test_recent_traffic_means_no_ping(self):
        conn = make_connected()
        conn._last_activity = asyncio.get_running_loop().time()

        with patch.object(conn, '_send_once', AsyncMock()) as send:
            sleeps = await self._run(conn)

        send.assert_not_called()
        assert 59 < sleeps[0] <= 60

    @pytest.mark.asyncio
    async def test_pings_after_silence(self):
        conn = make_connected(ping_zone=28)
        conn._last_activity = asyncio.get_running_loop().time() - 61

        with patch.object(conn, '_send_once', AsyncMock(return_value="~OUTPUT,28,1,99.00")) as send:
            await self._run(conn, sleeps_before_stop=0)

        send.assert_awaited_once_with("?OUTPUT,28,1", 28)

    @pytest.mark.asyncio
    async def test_ping_hub_error_keeps_going(self):
        conn = make_connected(ping_zone=250)
        conn._last_activity = asyncio.get_running_loop().time() - 61

        with patch.object(conn, '_send_once', AsyncMock(side_effect=HubError("?OUTPUT,250,1", "~ERROR,2"))):
            sleeps = await self._run(conn, sleeps_before_stop=0)

        assert len(sleeps) == 1  # went back to sleep instead of exiting

    @pytest.mark.asyncio
    async def test_ping_no_reply_reports_loss_and_ends(self):
        conn = make_connected()
        conn._last_activity = asyncio.get_running_loop().time() - 61
        hub_replies(conn, "")

        with patch("lutron_telnet.REPLY_TIMEOUT", 0.05), \
             patch.object(conn, '_connection_loop', AsyncMock()):
            await conn._keepalive_loop()

        assert conn.state is ConnState.CONNECTING
        await conn.disconnect()

    @pytest.mark.asyncio
    async def test_commands_do_not_restart_keepalive(self, no_background_tasks):
        conn = make_connected()
        task = MagicMock()
        conn._keepalive_task = task
        hub_replies(conn, "~OUTPUT,25,1,50.00")

        await conn.send_command("?OUTPUT,25,1", expected_zone=25)

        assert conn._keepalive_task is task
        task.cancel.assert_not_called()


# ---------------------------------------------------------------------------
# Zone-matched replies
# ---------------------------------------------------------------------------

class TestZoneMatchedReplies:
    @pytest.mark.asyncio
    async def test_other_zone_push_does_not_answer_pending_query(self):
        """A Pico press on zone 5 mid-query must not be taken as zone 28's reply."""
        conn = make_connected()
        conn._pending = _PendingReply(28, asyncio.get_running_loop().create_future())
        future = conn._pending.future
        pushes = []
        conn.add_push_callback(lambda line, source: pushes.append((line, source)))

        conn._reader.readuntil = AsyncMock(side_effect=[
            b"~OUTPUT,5,1,100.00\n",
            b"~OUTPUT,28,1,40.00\n",
            asyncio.CancelledError,
        ])
        await conn._reader_loop()

        assert future.result() == "~OUTPUT,28,1,40.00"
        assert pushes == [
            ("~OUTPUT,5,1,100.00", "external"),
            ("~OUTPUT,28,1,40.00", "internal"),
        ]

    @pytest.mark.asyncio
    async def test_reply_slot_filled_before_write(self):
        """The reply slot must exist before the command hits the wire."""
        conn = make_connected()
        hub_replies(conn, "~OUTPUT,25,1,50.00")
        at_write = []
        inner = conn._writer.write.side_effect

        def write(data):
            at_write.append(conn._pending.zone if conn._pending else None)
            inner(data)

        conn._writer.write = MagicMock(side_effect=write)

        await conn.send_command("?OUTPUT,25,1", expected_zone=25)

        assert at_write == [25]
