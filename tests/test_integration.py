"""
Integration tests for the fan-out/fan-in flow without Ray remote tasks
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from extended_audience_profiles.state import StateManager, Job
from extended_audience_profiles.masumi import MasumiClient


@pytest.mark.asyncio
async def test_masumi_client_job_submission():
    """Test MasumiClient job submission flow with mocks"""
    with patch('extended_audience_profiles.masumi.Config') as mock_config_class, \
         patch('extended_audience_profiles.masumi.Agent') as mock_agent_class, \
         patch('extended_audience_profiles.masumi.Payment') as mock_payment_class, \
         patch('extended_audience_profiles.masumi.Purchase') as mock_purchase_class:
        
        # Mock the SDK classes
        mock_config = Mock()
        mock_config_class.return_value = mock_config
        
        mock_agent = Mock()
        mock_agent_class.return_value = mock_agent
        mock_agent.check_availability = AsyncMock(return_value={'available': True})
        
        mock_payment = Mock()
        mock_payment_class.return_value = mock_payment
        
        mock_purchase = Mock()
        mock_purchase_class.return_value = mock_purchase
        
        # Create client
        client = MasumiClient()
        
        # Mock start_job response
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'success': True,
            'data': {
                'jobPinned': {
                    'job_id': 'test-job-789',
                    'payment_id': 'payment-456',
                    'blockchainIdentifier': 'blockchain-789'
                }
            }
        }
        
        with patch('requests.post', return_value=mock_response):
            # Test job submission
            result = await client.start_job('test-agent', {'question': 'test'})
            
            assert result['job_id'] == 'test-job-789'
            assert result['payment_id'] == 'payment-456'


@pytest.mark.asyncio
async def test_background_polling_logic():
    """Test the polling logic without Ray remote execution"""
    from extended_audience_profiles.background import _poll_masumi_jobs_async
    
    # Create submission with jobs
    submission_id, ref = StateManager.create_submission("test audience")
    
    job = Job(
        job_id="test-job-999",
        agent_name="test-agent",
        input_data={"question": "test question"}
    )
    ref = StateManager.add_job(ref, job)
    
    # Mock MasumiClient
    with patch('extended_audience_profiles.background.MasumiClient') as mock_client_class:
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Mock poll_job_status to return completed immediately
        mock_client.poll_job_status = AsyncMock(return_value={
            'status': 'completed',
            'job_id': 'test-job-999',
            'result': 'Test result from polling'
        })
        
        # Run polling logic directly (not as Ray task)
        final_ref = await _poll_masumi_jobs_async(ref)
        
        # Check results
        results = StateManager.get_results(final_ref)
        assert results['is_complete']
        assert results['completed_jobs'] == 1
        assert results['failed_jobs'] == 0
        assert 'test-job-999' in results['results']
        assert results['results']['test-job-999']['result'] == 'Test result from polling'


@pytest.mark.asyncio
async def test_job_submission_tool():
    """Test the job submission tool functionality"""
    # We need to test the actual function, not the FunctionTool wrapper
    # Import and patch at module level
    with patch('extended_audience_profiles.tools.MasumiClient') as mock_client_class:
        # Import after patching
        from extended_audience_profiles.tools import execute_agent_job
        
        mock_client = Mock()
        mock_client_class.return_value = mock_client
        
        # Mock the required methods
        mock_client.get_agent_remaining_budget.return_value = 10.0
        mock_client.start_job = AsyncMock(return_value={
            'job_id': 'test-job-tool',
            'payment_id': 'pay-tool',
            'blockchainIdentifier': 'blockchain-tool'
        })
        mock_client.create_purchase = AsyncMock(return_value={
            'success': True,
            'actual_cost': 0.75
        })
        mock_client.get_remaining_budget.return_value = 9.25
        
        # Get the actual async function
        if hasattr(execute_agent_job, '__wrapped__'):
            actual_func = execute_agent_job.__wrapped__
        else:
            # If no __wrapped__, try to access the function directly
            # This works for different versions of the decorator
            actual_func = execute_agent_job._func if hasattr(execute_agent_job, '_func') else execute_agent_job
        
        # Execute job (should return immediately)
        result = await actual_func("test-agent", {"question": "test from tool"})
        
        assert result['success']
        assert result['job_id'] == 'test-job-tool'
        assert result['status'] == 'submitted'
        assert 'Will complete in 5-20 minutes' in result['message']
        
        # Verify it didn't wait for completion
        assert not hasattr(mock_client, 'wait_for_completion') or not mock_client.wait_for_completion.called