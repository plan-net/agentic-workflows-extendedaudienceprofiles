#!/usr/bin/env python3
"""
Test script for Langfuse integration in Extended Audience Profiles
"""
import os
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_langfuse_integration():
    """Test all components of the Langfuse integration."""
    print("🧪 Testing Langfuse Integration for Extended Audience Profiles")
    print("=" * 60)
    
    # Test 1: Environment variables
    print("\n1️⃣ Testing environment variables...")
    required_vars = [
        "LANGFUSE_PUBLIC_KEY", 
        "LANGFUSE_SECRET_KEY", 
        "LANGFUSE_HOST"
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
        else:
            print(f"   ✅ {var}: Set")
    
    if missing_vars:
        print(f"   ❌ Missing variables: {missing_vars}")
        print("   Please set these environment variables before testing.")
        return False
    
    # Test 2: Prompt manager
    print("\n2️⃣ Testing prompt manager...")
    try:
        from extended_audience_profiles.langfuse_prompt_manager import ExtendedAudienceProfilesPromptManager
        manager = ExtendedAudienceProfilesPromptManager()
        
        if manager.langfuse:
            print("   ✅ Langfuse client initialized successfully")
            
            # Test prompt upload
            print("   📤 Testing prompt upload...")
            versions = manager.sync_local_prompts_to_langfuse("test")
            print(f"   ✅ Uploaded {len(versions)} prompts: {versions}")
            
            # Test prompt retrieval
            print("   📥 Testing prompt retrieval...")
            agent_names = ["orchestrator", "refinement", "consolidator"]
            prompts = manager.get_prompts_from_langfuse(agent_names, "test")
            print(f"   ✅ Retrieved {len(prompts)} prompts from Langfuse")
            
        else:
            print("   ❌ Failed to initialize Langfuse client")
            return False
            
    except Exception as e:
        print(f"   ❌ Prompt manager test failed: {e}")
        return False
    
    # Test 3: Tracing setup
    print("\n3️⃣ Testing tracing setup...")
    try:
        from extended_audience_profiles.tracing import setup_langfuse_tracing
        success = setup_langfuse_tracing()
        if success:
            print("   ✅ Tracing configured successfully")
        else:
            print("   ❌ Tracing setup failed")
            return False
    except Exception as e:
        print(f"   ❌ Tracing setup failed: {e}")
        return False
    
    # Test 4: Agent creation with Langfuse
    print("\n4️⃣ Testing agent creation...")
    try:
        # Set environment to use Langfuse prompts
        os.environ["USE_LANGFUSE_PROMPTS"] = "true"
        
        from extended_audience_profiles.agent import create_agents_with_langfuse
        agents = create_agents_with_langfuse("test", None)
        
        if len(agents) == 3:
            print("   ✅ Successfully created 3 agents with Langfuse prompts")
            print(f"   📋 Agent names: {[agent.name for agent in agents]}")
        else:
            print(f"   ❌ Expected 3 agents, got {len(agents)}")
            return False
            
    except Exception as e:
        print(f"   ❌ Agent creation test failed: {e}")
        return False
    
    print("\n🎉 All tests passed! Langfuse integration is working correctly.")
    return True

if __name__ == "__main__":
    asyncio.run(test_langfuse_integration())
