#!/usr/bin/env python
"""End-to-end test of the job execution flow"""
import asyncio
import os
from dotenv import load_dotenv
import json

# Load environment variables
load_dotenv()


async def test_end_to_end():
    from extended_audience_profiles.tools import execute_agent_job
    
    print("=== End-to-End Job Execution Test ===\n")
    
    # Test with advanced-web-research agent
    agent_name = "advanced-web-research"
    test_input = {
        "question": "What are the key characteristics and behaviors of millennials interested in sustainable fashion?"
    }
    
    print(f"Agent: {agent_name}")
    print(f"Input: {json.dumps(test_input, indent=2)}\n")
    
    print("Starting job execution flow...")
    print("-" * 50)
    
    try:
        # Execute the job using the tool's underlying function
        # FunctionTool objects have a .func attribute that contains the original function
        result = await execute_agent_job.func(agent_name, test_input)
        
        if result['success']:
            print("\n✅ Job completed successfully!\n")
            print("Result Details:")
            print(f"- Job ID: {result['job_id']}")
            print(f"- Agent: {result['agent_name']}")
            print(f"- Cost: ${result['budget_info']['job_cost']}")
            print(f"- Remaining budget: ${result['budget_info']['total_remaining']}")
            
            print("\n--- AGENT RESPONSE ---")
            print(result['result'])
            print("--- END RESPONSE ---\n")
            
            print("Metadata:")
            print(json.dumps(result['metadata'], indent=2))
        else:
            print(f"\n❌ Job failed: {result['error']}")
            print(f"Error type: {result.get('error_type', 'unknown')}")
            if 'budget_info' in result:
                print(f"Budget info: {json.dumps(result['budget_info'], indent=2)}")
    
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        print(f"Error type: {type(e).__name__}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("Note: This will actually spend money on the Masumi Network!")
    print("Running in TEST MODE - proceeding automatically...\n")
    asyncio.run(test_end_to_end())