#!/usr/bin/env python3
"""Debug script to test query command."""
import asyncio
import logging
from lutron_telnet import LutronTelnetConnection

# Enable debug logging
logging.basicConfig(level=logging.DEBUG, format='%(name)s - %(levelname)s: %(message)s')

async def main():
    connection = LutronTelnetConnection("10.0.1.111")

    print("\n" + "="*60)
    print("Testing Query Command")
    print("="*60)

    # First, set the light to a known level
    print("\n1. Setting zone 28 to 75%...")
    success = await connection.set_light_level(28, 75, 0)
    print(f"   Set result: {success}")

    await asyncio.sleep(1)

    # Now query it
    print("\n2. Querying zone 28...")
    level = await connection.query_light_level(28)
    print(f"   Query result: {level}")

    await connection.disconnect()
    print("\n" + "="*60)

if __name__ == "__main__":
    asyncio.run(main())
