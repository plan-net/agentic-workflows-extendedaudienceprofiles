"""
Unit tests for the fan-out/fan-in flow
"""
import pytest
import ray
import os
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from extended_audience_profiles.state import StateManager, Job, JobSubmission
from extended_audience_profiles.background import poll_masumi_jobs
import time


@pytest.fixture
def ray_fixture():
    """Initialize Ray for tests"""
    if not ray.is_initialized():
        ray.init()
    yield
    if ray.is_initialized():
        ray.shutdown()


def test_state_manager(ray_fixture):
    """Test basic state management operations"""
    # Create submission
    submission_id, ref = StateManager.create_submission("test audience")
    
    # Add jobs
    job1 = Job(
        job_id="job-1",
        agent_name="test-agent",
        input_data={"question": "test question 1"}
    )
    ref = StateManager.add_job(ref, job1)
    
    job2 = Job(
        job_id="job-2", 
        agent_name="test-agent",
        input_data={"question": "test question 2"}
    )
    ref = StateManager.add_job(ref, job2)
    
    # Get submission
    submission = StateManager.get_submission(ref)
    assert len(submission.jobs) == 2
    assert submission.audience_description == "test audience"
    assert not submission.is_complete()
    
    # Update job status
    ref = StateManager.update_job_status(ref, "job-1", "completed", result="Result 1")
    ref = StateManager.update_job_status(ref, "job-2", "failed", error="Test error")
    
    # Check completion
    submission = StateManager.get_submission(ref)
    assert submission.is_complete()
    assert len(submission.get_completed_jobs()) == 1
    assert len(submission.get_failed_jobs()) == 1
    
    # Get results
    results = StateManager.get_results(ref)
    assert results['total_jobs'] == 2
    assert results['completed_jobs'] == 1
    assert results['failed_jobs'] == 1
    assert 'job-1' in results['results']


@pytest.mark.asyncio
async def test_background_polling(ray_fixture, monkeypatch):
    """Test background polling with mocked Masumi client"""
    # Set required environment variables
    monkeypatch.setenv("PAYMENT_SERVICE_URL", "http://test")
    monkeypatch.setenv("PAYMENT_API_KEY", "test-key")
    
    # Create submission with jobs
    submission_id, ref = StateManager.create_submission("test audience")
    
    job = Job(
        job_id="test-job-123",
        agent_name="test-agent",
        input_data={"question": "test question"}
    )
    ref = StateManager.add_job(ref, job)
    
    # Mock the entire MasumiClient class
    mock_client = MagicMock()
    
    # Mock poll_job_status to return completed after first call
    mock_client.poll_job_status = AsyncMock(side_effect=[
        {'status': 'running', 'job_id': 'test-job-123'},
        {'status': 'completed', 'job_id': 'test-job-123', 'result': 'Test result'}
    ])
    
    with patch('extended_audience_profiles.background.MasumiClient', return_value=mock_client):
        # Run polling task
        final_ref = await poll_masumi_jobs.remote(ref)
        final_ref = await final_ref
        
        # Check results
        results = StateManager.get_results(final_ref)
        assert results['is_complete']
        assert results['completed_jobs'] == 1
        assert results['failed_jobs'] == 0
        assert 'test-job-123' in results['results']
        assert results['results']['test-job-123']['result'] == 'Test result'


@pytest.mark.asyncio
async def test_job_submission_flow():
    """Test the job submission flow by calling the function directly"""
    # Import the actual function to test
    from extended_audience_profiles.tools import execute_agent_job
    
    # We need to access the wrapped function directly to avoid pydantic issues
    # The @function_tool decorator wraps our function, we need the original
    actual_func = execute_agent_job.__wrapped__
    
    # Mock MasumiClient
    with patch('extended_audience_profiles.tools.MasumiClient') as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Mock the required methods
        mock_client.get_agent_remaining_budget.return_value = 10.0
        mock_client.start_job = AsyncMock(return_value={
            'job_id': 'test-job-456',
            'payment_id': 'pay-123',
            'blockchainIdentifier': 'blockchain-123'
        })
        mock_client.create_purchase = AsyncMock(return_value={
            'success': True,
            'actual_cost': 0.50
        })
        mock_client.get_remaining_budget.return_value = 9.50
        
        # Execute job (should return immediately)
        result = await actual_func("test-agent", {"question": "test"})
        
        assert result['success']
        assert result['job_id'] == 'test-job-456'
        assert result['status'] == 'submitted'
        assert 'Will complete in 5-20 minutes' in result['message']
        
        # Verify it didn't wait for completion
        mock_client.wait_for_completion.assert_not_called()