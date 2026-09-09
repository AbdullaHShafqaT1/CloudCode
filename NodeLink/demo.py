import asyncio
import os
from nodelink import NodeLink

async def main():
    print("Initializing NodeLink...")
    # Initialize NodeLink
    nodelink = NodeLink()
    
    # 1. Connect to a mock target using the Generic Rest Connector
    print("\n--- Testing Generic REST Connection ---")
    config = {
        "endpoint": "https://httpbin.org",
        "auth_type": "BEARER_TOKEN",
        "token": "secret_demo_token",
        "adapter": "generic"
    }
    
    handle = nodelink.connect("test_target_01", config)
    print(f"Connected. Handle state: {handle.to_dict()}")
    
    # 2. Send a request
    print("\n--- Sending Request ---")
    res = await nodelink.send_request("test_target_01", "post", data={"msg": "Hello NodeLink"})
    print(f"Response Status: {res.status_code}")
    print(f"Response Data snippet: {str(res.data)[:100]}...")
    
    # 3. Disconnect
    print("\n--- Disconnecting ---")
    await nodelink.disconnect("test_target_01")
    print("Done.")

if __name__ == "__main__":
    # Suppress verbose logging for demo
    import logging
    logging.getLogger().setLevel(logging.WARNING)
    
    asyncio.run(main())
