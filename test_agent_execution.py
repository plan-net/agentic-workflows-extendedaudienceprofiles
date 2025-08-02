#!/usr/bin/env python
"""Test the agent execution flow using OpenAI Agents"""
import asyncio
import os
from dotenv import load_dotenv
from agents import Agent, Runner
from extended_audience_profiles.tools import list_available_agents, get_agent_input_schema, execute_agent_job

# Load environment variables
load_dotenv()


async def test_agent_execution():
    """Test the agent execution using OpenAI Agents framework"""
    print("=== OpenAI Agent Execution Test ===\n")
    
    # Create a simple test agent
    test_agent = Agent(
        name="test-orchestrator",
        instructions="""You are a test agent. Follow these steps exactly:
        1. First, list available agents
        2. Get the input schema for 'advanced-web-research' agent
        3. Execute a job with the advanced-web-research agent asking: "What are the key characteristics and behaviors of millennials interested in sustainable fashion?"
        4. Return the result
        """,
        model="gpt-4o",
        tools=[list_available_agents, get_agent_input_schema, execute_agent_job]
    )
    
    try:
        # Run the agent
        print("Running test agent...")
        print("-" * 50)
        
        result = await Runner.run(
            test_agent,
            "Execute the test sequence as instructed"
        )
        
        print("\n✅ Agent execution completed!\n")
        print("Final Output:")
        print("-" * 50)
        print(result.final_output)
        print("-" * 50)
        
        # Print tool calls made
        print("\nTool Calls Made:")
        for i, call in enumerate(result.tool_calls):
            print(f"{i+1}. {call.tool_name}({call.arguments})")
        
    except Exception as e:
        print(f"\n❌ Agent execution failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    print("Note: This will actually spend money on the Masumi Network!")
    print("Running in TEST MODE - proceeding automatically...\n")
    asyncio.run(test_agent_execution())