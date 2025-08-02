#!/usr/bin/env python3
"""
Test script to verify hash field logging in Masumi status responses
"""
import asyncio
import logging
import sys
from extended_audience_profiles.masumi import MasumiClient

# Configure logging to see debug messages
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

async def test_status_polling():
    """Test status polling to see what hash fields are returned"""
    client = MasumiClient()
    
    # Get a test agent
    agents = client.get_available_agents()
    if not agents:
        print("No agents available")
        return
    
    test_agent = agents[0]
    print(f"\nTesting with agent: {test_agent['name']}")
    
    try:
        # Start a simple job
        print("\nStarting test job...")
        job_response = await client.start_job(
            agent_name=test_agent['name'],
            input_data={"question": "What is the capital of France?"}
        )
        
        job_id = job_response['job_id']
        print(f"Started job: {job_id}")
        
        # Poll status multiple times to see the progression
        print("\nPolling status (this will show debug logs)...")
        for i in range(5):
            print(f"\n--- Poll attempt {i+1} ---")
            try:
                status = await client.poll_job_status(job_id, test_agent['name'])
                print(f"Status: {status.get('status', 'unknown')}")
                
                # If completed, break
                if status.get('status') in ['completed', 'failed']:
                    break
                    
            except Exception as e:
                print(f"Error polling: {e}")
            
            await asyncio.sleep(2)
        
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_status_polling())