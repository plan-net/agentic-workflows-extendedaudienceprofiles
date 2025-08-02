#!/usr/bin/env python
"""Test the complete fan-out/fan-in flow"""
import asyncio
import os
from dotenv import load_dotenv
import ray
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


async def test_fanout_fanin():
    """Test the complete audience profile generation flow"""
    from extended_audience_profiles.agent import generate_audience_profile
    
    print("=== Fan-Out/Fan-In Test ===\n")
    
    # Initialize Ray if not already initialized
    if not ray.is_initialized():
        ray.init()
        print("Ray initialized\n")
    
    # Test audience description
    audience_description = "millennials interested in sustainable fashion and eco-friendly lifestyle brands"
    
    print(f"Audience Description: {audience_description}\n")
    print("Starting profile generation...")
    print("-" * 50)
    
    try:
        # Run the complete flow
        result = await generate_audience_profile(audience_description)
        
        if result['success']:
            print("\n✅ Profile generation completed successfully!\n")
            
            # Print metadata
            metadata = result['metadata']
            print("Execution Summary:")
            print(f"- Submission ID: {metadata['submission_id']}")
            print(f"- Total jobs: {metadata['total_jobs']}")
            print(f"- Completed: {metadata['completed_jobs']}")
            print(f"- Failed: {metadata['failed_jobs']}")
            print(f"- Duration: {metadata.get('total_duration', 'N/A'):.1f} seconds")
            
            print("\nJobs Submitted:")
            for job in metadata['jobs_submitted']:
                print(f"- {job['agent']} (ID: {job['job_id']})")
                print(f"  Research: {job['research_focus']}")
            
            print("\n--- FINAL AUDIENCE PROFILE ---")
            print(result['profile'])
            print("--- END PROFILE ---\n")
            
        else:
            print(f"\n❌ Profile generation failed: {result['error']}")
    
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Shutdown Ray
        if ray.is_initialized():
            ray.shutdown()
            print("\nRay shutdown complete")


if __name__ == "__main__":
    print("Note: This will actually spend money on the Masumi Network!")
    print("The process may take 5-20 minutes to complete.\n")
    response = input("Do you want to proceed? (yes/no): ")
    
    if response.lower() == 'yes':
        asyncio.run(test_fanout_fanin())
    else:
        print("Test cancelled.")