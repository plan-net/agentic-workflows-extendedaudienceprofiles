#!/usr/bin/env python3
"""
Test script to verify storage handles multiple calls to the same agent
"""
import asyncio
import json
from pathlib import Path
from extended_audience_profiles.storage import ResultStorage

async def test_multiple_agent_calls():
    """Test storing multiple results from the same agent"""
    
    # Use a test directory
    storage = ResultStorage(base_dir="data/test_results")
    
    job_id = "test-multiple-agents-123"
    agent_name = "audience-insights-gwi"
    
    # Simulate saving two results from the same agent
    print(f"Testing storage for job {job_id}")
    
    # First call to audience-insights-gwi
    await storage.save_agent_result(
        job_id=job_id,
        masumi_job_id="3bfa27de-7c02-4da3-9fa5-abac8c482c81",
        agent_name=agent_name,
        content="First research result about millennials and sports",
        metadata={
            'input_data': {'question': 'What are millennials sports interests?'},
            'started_at': 1754111571.0,
            'completed_at': 1754111946.0,
            'duration': 375.0,
            'hashes': {'output_hash': 'hash123', 'decision_log_hash': 'hash456'}
        }
    )
    print(f"✓ Saved first result for {agent_name}")
    
    # Second call to the same agent
    await storage.save_agent_result(
        job_id=job_id,
        masumi_job_id="64f88b4c-5568-4c41-8334-1f0356e17acc",
        agent_name=agent_name,
        content="Second research result with different focus",
        metadata={
            'input_data': {'question': 'What are Gen Z sports preferences?'},
            'started_at': 1754111571.0,
            'completed_at': 1754111946.0,
            'duration': 375.0,
            'hashes': {'output_hash': 'hash789', 'decision_log_hash': 'hashABC'}
        }
    )
    print(f"✓ Saved second result for {agent_name}")
    
    # Also save a result from a different agent
    await storage.save_agent_result(
        job_id=job_id,
        masumi_job_id="0857e507-6011-4cbf-8e16-750cc4534ad0",
        agent_name="advanced-web-research",
        content="Web research about sports trends",
        metadata={
            'input_data': {'question': 'Latest sports trends 2025'},
            'started_at': 1754111571.0,
            'completed_at': 1754111946.0,
            'duration': 300.0
        }
    )
    print("✓ Saved result for advanced-web-research")
    
    # Verify the storage structure
    agent_dir = Path(f"data/test_results/jobs/{job_id}/{agent_name}")
    
    # Check files exist
    print(f"\nChecking files in {agent_dir}:")
    if agent_dir.exists():
        for file in agent_dir.iterdir():
            print(f"  - {file.name}")
    
    # Load and display metadata
    metadata_file = agent_dir / "metadata.json"
    if metadata_file.exists():
        with open(metadata_file, 'r') as f:
            metadata = json.loads(f.read())
        print(f"\nMetadata for {agent_name}:")
        print(f"  Agent: {metadata.get('agent_name')}")
        print(f"  Number of jobs: {len(metadata.get('jobs', []))}")
        for i, job in enumerate(metadata.get('jobs', [])):
            print(f"\n  Job {i+1}:")
            print(f"    Masumi Job ID: {job['masumi_job_id']}")
            print(f"    Question: {job['input'].get('question', 'N/A')}")
            print(f"    Hashes: {job.get('hashes', {})}")
    
    # Test retrieval
    print("\n\nTesting retrieval of all results:")
    results = await storage.get_job_results(job_id)
    
    print(f"Total agent results found: {len(results['agent_results'])}")
    for result in results['agent_results']:
        print(f"\n- Agent: {result['agent_name']}")
        print(f"  Job ID: {result['masumi_job_id']}")
        print(f"  Has content: {'Yes' if 'content' in result else 'No'}")
        print(f"  Hashes: {result.get('hashes', {})}")
    
    # Clean up test directory
    import shutil
    shutil.rmtree("data/test_results", ignore_errors=True)
    print("\n✓ Test completed and cleaned up")

if __name__ == "__main__":
    asyncio.run(test_multiple_agent_calls())