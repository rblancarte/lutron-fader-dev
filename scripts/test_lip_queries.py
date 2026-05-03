#!/usr/bin/env python3
import asyncio
import sys
sys.path.append('../lutron_fader')
from lutron_telnet import LutronTelnetConnection

async def test_queries():
    conn = LutronTelnetConnection("10.0.1.111")
    await conn.connect()
    
    print("Testing LIP query commands:")
    print("=" * 60)
    
    # Test various query commands
    commands = [
        "?HELP",
        "?DEVICE",
        "?SYSTEM",  
        "#MONITORING,12,2",
        "?OUTPUT,28",  # Query specific zone
        "#DEVICE,28,9,2",  # Query device status
    ]
    
    for cmd in commands:
        print(f"\nCommand: {cmd}")
        response = await conn.send_command(cmd)
        print(f"Response: {response!r}")
        
        # Wait a bit longer and check for more data
        await asyncio.sleep(1)
        
        # Try to read any additional responses
        if conn._reader:
            try:
                while True:
                    line = await asyncio.wait_for(
                        conn._reader.readline(), 
                        timeout=0.5
                    )
                    if line:
                        print(f"  Additional: {line.decode().strip()!r}")
                    else:
                        break
            except asyncio.TimeoutError:
                pass
    
    await conn.disconnect()

asyncio.run(test_queries())