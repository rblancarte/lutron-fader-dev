#!/usr/bin/env python3
"""Test how long the Lutron hub keeps a Telnet connection alive without activity."""
import asyncio
from datetime import datetime
from lutron_telnet import LutronTelnetConnection


# Test configuration
TEST_ZONE = 28  # Living Room Floor Lamp
TEST_HOST = "10.0.1.111"


async def test_connection_longevity():
    """Test connection longevity with increasing wait intervals."""
    print("=" * 70)
    print("LUTRON CONNECTION LONGEVITY TEST")
    print("=" * 70)
    print()
    print("This test will:")
    print("  1. Send a command")
    print("  2. Wait progressively longer intervals")
    print("  3. Try to send another command")
    print("  4. See if the connection is still alive")
    print()
    print("We'll test intervals: 30s, 60s, 90s, 120s, 150s, 180s, 210s, 240s, 270s, 300s, 330s, 360s")
    print("(stopping at 6 minutes or when connection drops)")
    print()
    
    # Intervals to test (in seconds) - up to 360 (6 minutes)
    intervals = [30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330, 360]
    
    connection = LutronTelnetConnection(TEST_HOST)
    
    print("Establishing initial connection...")
    success = await connection.connect()
    
    if not success:
        print("✗ Failed to establish initial connection!")
        return
    
    print(f"✓ Connected to Lutron hub at {TEST_HOST}")
    print()
    
    # Send initial command
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Sending initial command (Zone {TEST_ZONE} → 50%)...")
    success = await connection.set_light_level(TEST_ZONE, 50, 0)
    
    if not success:
        print("✗ Initial command failed!")
        await connection.disconnect()
        return
    
    print("✓ Initial command successful")
    print(f"✓ Connection status: {'Connected' if connection.is_connected else 'Disconnected'}")
    print()
    
    # Test each interval
    results = []
    
    for interval in intervals:
        print("-" * 70)
        print(f"Testing {interval} second wait interval...")
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Waiting {interval} seconds...")
        
        # Wait for the interval
        for i in range(interval):
            await asyncio.sleep(1)
            remaining = interval - i - 1
            if remaining > 0 and (remaining % 10 == 0 or remaining < 10):
                print(f"  {remaining}s remaining...", end='\r')
        
        print()  # New line after countdown
        
        # Check if still connected
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Checking connection status...")
        
        if not connection.is_connected:
            print("✗ Connection dropped during wait!")
            results.append({
                'interval': interval,
                'waited': interval,
                'success': False,
                'reason': 'Connection dropped during wait'
            })
            break
        
        print("✓ Still showing as connected")
        
        # Try to send a command
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Sending test command (Zone {TEST_ZONE} → 75%)...")
        
        try:
            success = await connection.set_light_level(TEST_ZONE, 75, 0)
            
            if success:
                print(f"✓ Command successful after {interval}s wait!")
                results.append({
                    'interval': interval,
                    'waited': interval,
                    'success': True,
                    'reason': 'Command sent successfully'
                })
            else:
                print(f"✗ Command failed after {interval}s wait!")
                results.append({
                    'interval': interval,
                    'waited': interval,
                    'success': False,
                    'reason': 'Command failed'
                })
                break
        except Exception as e:
            print(f"✗ Exception after {interval}s wait: {e}")
            results.append({
                'interval': interval,
                'waited': interval,
                'success': False,
                'reason': f'Exception: {e}'
            })
            break
        
        print()
    
    # Clean up
    print("-" * 70)
    print("Cleaning up...")
    await connection.disconnect()
    print("✓ Disconnected")
    print()
    
    # Summary
    print("=" * 70)
    print("TEST RESULTS SUMMARY")
    print("=" * 70)
    print()
    
    successful_intervals = [r['interval'] for r in results if r['success']]
    failed_intervals = [r for r in results if not r['success']]
    
    if successful_intervals:
        max_successful = max(successful_intervals)
        print(f"✓ Connection stayed alive for up to {max_successful} seconds ({max_successful / 60:.1f} minutes)")
        print(f"✓ Successfully tested intervals: {', '.join(map(str, successful_intervals))}s")
    
    if failed_intervals:
        first_failure = failed_intervals[0]
        print()
        print(f"✗ First failure at {first_failure['interval']} seconds ({first_failure['interval'] / 60:.1f} minutes)")
        print(f"  Reason: {first_failure['reason']}")
    
    if not failed_intervals and successful_intervals:
        print()
        print("✓ All tested intervals successful!")
        print("  The connection can stay idle for at least 6 minutes")
    
    print()
    print("Detailed Results:")
    print()
    
    for result in results:
        status = "✓ PASS" if result['success'] else "✗ FAIL"
        minutes = result['interval'] / 60
        print(f"  {status}  {result['interval']:3d}s ({minutes:.1f}m) wait - {result['reason']}")
    
    print()
    
    # Recommendations
    print("=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    print()
    
    if successful_intervals:
        max_safe = max(successful_intervals)
        if max_safe >= 360:
            print("✓ Connection is very stable - 6 minute auto-disconnect is safe")
            print("  Recommendation: Use 5-minute (300s) auto-disconnect with good margin")
        elif max_safe >= 300:
            print("✓ Connection is very stable - 5+ minute idle time works")
            print("  Recommendation: Use 4-minute (240s) auto-disconnect to be safe")
        elif max_safe >= 240:
            print("⚠ Connection stable for 4+ minutes")
            print("  Recommendation: Use 3-minute (180s) auto-disconnect to be safe")
        elif max_safe >= 180:
            print("⚠ Connection stable for 3+ minutes")
            print("  Recommendation: Use 2.5-minute (150s) auto-disconnect to be safe")
        elif max_safe >= 120:
            print("⚠ Connection stable for 2+ minutes")
            print("  Recommendation: Use 90s auto-disconnect to be safe")
        else:
            print("⚠ Connection drops quickly")
            print(f"  Recommendation: Use {max_safe - 10}s auto-disconnect")
    
    print()


async def test_ping_strategy():
    """Test if sending periodic 'pings' keeps connection alive longer."""
    print("=" * 70)
    print("BONUS TEST: Ping Strategy")
    print("=" * 70)
    print()
    print("Testing if periodic queries keep the connection alive...")
    print()
    
    connection = LutronTelnetConnection(TEST_HOST)
    
    print("Establishing connection...")
    await connection.connect()
    print("✓ Connected")
    print()
    
    # Test: Send a query every 60 seconds for 6 minutes
    duration = 360  # 6 minutes
    ping_interval = 60  # Every 60 seconds
    
    print(f"Sending query every {ping_interval}s for {duration}s ({duration / 60:.0f} minutes) total...")
    print()
    
    start_time = datetime.now()
    pings_sent = 0
    
    for elapsed in range(0, duration, ping_interval):
        remaining = duration - elapsed
        print(f"[{datetime.now().strftime('%H:%M:%S')}] Elapsed: {elapsed}s ({elapsed / 60:.1f}m), Remaining: {remaining}s ({remaining / 60:.1f}m)")
        
        # Send a query as a "ping"
        print(f"  Sending ping (query Zone {TEST_ZONE})...")
        level = await connection.query_light_level(TEST_ZONE)
        
        if level is not None:
            print(f"  ✓ Ping successful! Zone {TEST_ZONE} is at {level}%")
            pings_sent += 1
        else:
            print(f"  ✗ Ping failed!")
            break
        
        # Wait for next interval (unless this is the last one)
        if elapsed + ping_interval < duration:
            print(f"  Waiting {ping_interval}s until next ping...")
            await asyncio.sleep(ping_interval)
        
        print()
    
    await connection.disconnect()
    
    print("=" * 70)
    print(f"✓ Sent {pings_sent} successful pings over {duration}s ({duration / 60:.0f} minutes)")
    print("  Conclusion: Periodic queries DO keep the connection alive!")
    print()


async def main():
    """Run both tests."""
    print()
    
    # Test 1: Natural longevity
    await test_connection_longevity()
    
    # Ask if user wants to run the ping test
    print()
    print("=" * 70)
    print()
    
    try:
        response = input("Run bonus 'ping strategy' test? (y/n): ").strip().lower()
        if response == 'y':
            print()
            await test_ping_strategy()
    except KeyboardInterrupt:
        print("\nSkipped bonus test")
    
    print()
    print("All tests complete!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")