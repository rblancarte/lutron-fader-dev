#!/usr/bin/env python3
"""Test script for LutronTelnetConnection class."""
import asyncio
import sys
from lutron_telnet import LutronTelnetConnection


# Use Zone 28 - Living Room Floor Lamp for testing
TEST_ZONE = 28
TEST_ZONE_NAME = "Living Room Floor Lamp"


async def test_basic_connection():
    """Test basic connection to the Lutron hub."""
    print("=" * 60)
    print("TEST 1: Basic Connection")
    print("=" * 60)
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    print("Connecting to Lutron hub...")
    success = await connection.connect()
    
    if success:
        print("✓ Connection successful!")
        print(f"✓ Connected: {connection.is_connected}")
        await connection.disconnect()
        print("✓ Disconnected successfully")
    else:
        print("✗ Connection failed!")
        return False
    
    print()
    return True


async def test_set_light_level():
    """Test setting light level with short fade."""
    print("=" * 60)
    print("TEST 2: Set Light Level (Quick Fade)")
    print("=" * 60)
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    target_brightness = 50
    fade_time = 5  # 5 second fade
    
    print(f"Setting Zone {TEST_ZONE} ({TEST_ZONE_NAME}) to {target_brightness}% over {fade_time} seconds...")
    success = await connection.set_light_level(TEST_ZONE, target_brightness, fade_time)
    
    if success:
        print("✓ Command sent successfully!")
    else:
        print("✗ Command failed!")
        return False
    
    # Wait for fade to complete
    print(f"Waiting {fade_time} seconds for fade to complete...")
    await asyncio.sleep(fade_time + 1)
    
    print()
    return True


async def test_query_light_level():
    """Test querying current light level."""
    print("=" * 60)
    print("TEST 3: Query Light Level")
    print("=" * 60)
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    print(f"Querying Zone {TEST_ZONE} ({TEST_ZONE_NAME}) current level...")
    level = await connection.query_light_level(TEST_ZONE)
    
    if level is not None:
        print(f"✓ Zone {TEST_ZONE} is at {level}%")
    else:
        print("✗ Query failed!")
        return False
    
    print()
    return True


async def test_multiple_commands():
    """Test sending multiple commands in sequence."""
    print("=" * 60)
    print("TEST 4: Multiple Commands (Connection Reuse)")
    print("=" * 60)
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    # Send multiple commands - connection should stay alive
    print("Sending 3 commands in quick succession...")
    
    print(f"  Command 1: Zone {TEST_ZONE} → 75%")
    await connection.set_light_level(TEST_ZONE, 75, 2)
    
    await asyncio.sleep(0.5)
    
    print(f"  Command 2: Zone {TEST_ZONE} → 25%")
    await connection.set_light_level(TEST_ZONE, 25, 2)
    
    await asyncio.sleep(0.5)
    
    print(f"  Command 3: Zone {TEST_ZONE} → 100%")
    await connection.set_light_level(TEST_ZONE, 100, 2)
    
    print("✓ All commands sent!")
    print("  (Connection should still be alive)")
    
    # Wait a moment
    await asyncio.sleep(3)
    
    # Verify still connected
    if connection.is_connected:
        print("✓ Connection still active (as expected)")
    else:
        print("✗ Connection dropped unexpectedly")
        return False
    
    # Clean disconnect
    await connection.disconnect()
    
    print()
    return True


async def test_long_fade():
    """Test a longer fade (30 seconds for demo purposes)."""
    print("=" * 60)
    print("TEST 5: Long Fade (30 seconds)")
    print("=" * 60)
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    fade_time = 30
    
    print(f"Starting 30-second fade for Zone {TEST_ZONE} ({TEST_ZONE_NAME})...")
    print(f"  From: current brightness")
    print(f"  To:   0% (off)")
    print(f"  Time: {fade_time} seconds")
    
    await connection.set_light_level(TEST_ZONE, 0, fade_time)
    
    print("✓ Fade command sent!")
    print("  Watch your light - it should slowly dim over 30 seconds")
    print("  Press Ctrl+C to skip waiting...")
    
    try:
        for i in range(fade_time):
            await asyncio.sleep(1)
            remaining = fade_time - i - 1
            if remaining > 0:
                print(f"  {remaining} seconds remaining...", end='\r')
        print()
        print("✓ Fade complete!")
    except KeyboardInterrupt:
        print("\n⊗ Skipped waiting (fade continues in background)")
    
    await connection.disconnect()
    print()
    return True


async def test_ping_functionality():
    """Test ping functionality without resetting disconnect timer."""
    print("=" * 60)
    print("TEST 6: Ping Functionality (15s ping interval for demo)")
    print("=" * 60)

    # Create connection with shorter intervals for testing
    import lutron_telnet
    original_ping_interval = lutron_telnet.PING_INTERVAL
    original_disconnect_timeout = lutron_telnet.DISCONNECT_TIMEOUT

    # Set shorter intervals for faster testing
    lutron_telnet.PING_INTERVAL = 5  # Ping every 5 seconds
    lutron_telnet.DISCONNECT_TIMEOUT = 15  # Disconnect after 15 seconds

    connection = LutronTelnetConnection("10.0.1.111")

    print("Establishing connection...")
    await connection.connect()
    print(f"✓ Connected: {connection.is_connected}")
    print()

    print("Settings for this test:")
    print(f"  Ping interval: 5 seconds")
    print(f"  Disconnect timeout: 15 seconds")
    print()

    print("Monitoring connection for 18 seconds...")
    print("  Pings should occur at t=5s, t=10s, t=15s")
    print("  Auto-disconnect should occur around t=15s")
    print()

    # Monitor for 18 seconds
    ping_count = 0

    for i in range(19):
        await asyncio.sleep(1)
        elapsed = i + 1
        status = "Connected" if connection.is_connected else "Disconnected"

        # Estimate pings that should have occurred (every 5 seconds)
        expected_pings = elapsed // 5

        print(f"  t={elapsed:2d}s: {status:13s} (expected ~{expected_pings} pings so far)", end='\r')

        # If we disconnected, note when
        if not connection.is_connected and ping_count == 0:
            print()
            print(f"  → Disconnected at t={elapsed}s")
            break

    print()
    print()

    # Verify behavior
    success = True

    if not connection.is_connected:
        print("✓ Auto-disconnect occurred (as expected)")
    else:
        print("✗ Still connected after timeout (unexpected)")
        await connection.disconnect()
        success = False

    print()
    print("Key observations:")
    print("  1. Pings sent every 5 seconds (check debug logs)")
    print("  2. Pings did NOT reset the disconnect timer")
    print("  3. Connection auto-disconnected after ~15 seconds of user inactivity")

    # Restore original values
    lutron_telnet.PING_INTERVAL = original_ping_interval
    lutron_telnet.DISCONNECT_TIMEOUT = original_disconnect_timeout

    print()
    return success


async def test_auto_disconnect():
    """Test auto-disconnect after timeout."""
    print("=" * 60)
    print("TEST 7: Auto-Disconnect (10 second timeout for demo)")
    print("=" * 60)

    # Create connection with shorter timeout for testing
    # We'll modify the timeout temporarily
    import lutron_telnet
    original_timeout = lutron_telnet.DISCONNECT_TIMEOUT
    lutron_telnet.DISCONNECT_TIMEOUT = 10  # 10 seconds for testing

    connection = LutronTelnetConnection("10.0.1.111")

    print("Sending command to establish connection...")
    await connection.set_light_level(TEST_ZONE, 50, 0)
    print(f"✓ Connected: {connection.is_connected}")

    print("Waiting 10 seconds for auto-disconnect...")
    for i in range(11):  # Wait 11 seconds to be sure
        await asyncio.sleep(1)
        remaining = max(0, 10 - i)
        status = "Connected" if connection.is_connected else "Disconnected"
        print(f"  {remaining}s remaining... [{status}]", end='\r')

    print()

    # Give it one more second to finish disconnecting
    await asyncio.sleep(1)

    if not connection.is_connected:
        print("✓ Auto-disconnect worked!")
        success = True
    else:
        print("✗ Still connected after timeout")
        await connection.disconnect()
        success = False

    # Restore original timeout
    lutron_telnet.DISCONNECT_TIMEOUT = original_timeout

    print()
    return success


async def run_all_tests():
    """Run all tests."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 15 + "LUTRON TELNET TESTS" + " " * 24 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    print(f"Test Zone: {TEST_ZONE} ({TEST_ZONE_NAME})")
    print()
    
    tests = [
        ("Basic Connection", test_basic_connection),
        ("Set Light Level", test_set_light_level),
        ("Query Light Level", test_query_light_level),
        ("Multiple Commands", test_multiple_commands),
        ("Long Fade", test_long_fade),
        ("Ping Functionality", test_ping_functionality),
        ("Auto-Disconnect", test_auto_disconnect),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"✗ Test '{name}' crashed: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}  {name}")
    
    print()
    print(f"Results: {passed}/{total} tests passed")
    print()
    
    return passed == total


async def interactive_mode():
    """Interactive mode for manual testing."""
    print("\n")
    print("╔" + "=" * 58 + "╗")
    print("║" + " " * 12 + "LUTRON TELNET INTERACTIVE" + " " * 21 + "║")
    print("╚" + "=" * 58 + "╝")
    print()
    print("Commands:")
    print("  set <zone> <brightness> <fade_time>  - Set light level")
    print("  query <zone>                          - Query light level")
    print("  quit                                  - Exit")
    print()
    
    connection = LutronTelnetConnection("10.0.1.111")
    
    while True:
        try:
            cmd = input("lutron> ").strip().lower()
            
            if not cmd:
                continue
            
            if cmd == "quit" or cmd == "exit":
                print("Disconnecting...")
                await connection.disconnect()
                break
            
            parts = cmd.split()
            
            if parts[0] == "set" and len(parts) == 4:
                zone = int(parts[1])
                brightness = int(parts[2])
                fade_time = int(parts[3])
                
                print(f"Setting zone {zone} to {brightness}% over {fade_time}s...")
                success = await connection.set_light_level(zone, brightness, fade_time)
                print("✓ Done" if success else "✗ Failed")
                
            elif parts[0] == "query" and len(parts) == 2:
                zone = int(parts[1])
                
                print(f"Querying zone {zone}...")
                level = await connection.query_light_level(zone)
                if level is not None:
                    print(f"✓ Zone {zone} is at {level}%")
                else:
                    print("✗ Query failed")
            
            else:
                print("Invalid command")
        
        except KeyboardInterrupt:
            print("\nDisconnecting...")
            await connection.disconnect()
            break
        except Exception as e:
            print(f"Error: {e}")


async def main():
    """Main entry point."""
    if len(sys.argv) > 1 and sys.argv[1] == "interactive":
        await interactive_mode()
    else:
        success = await run_all_tests()
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(1)