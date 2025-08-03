"""
Error handling utilities for Extended Audience Profiles
"""
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


async def handle_job_submission_error(
    result: Dict[str, Any], 
    items: List[Any], 
    index: int, 
    tracer,
    find_agent_name_func=None
) -> None:
    """Handle errors from job submission attempts."""
    from .formatting import find_agent_name_from_previous_calls
    
    if find_agent_name_func is None:
        find_agent_name_func = find_agent_name_from_previous_calls
        
    agent_name = result.get('agent_name') or find_agent_name_func(
        items, index
    ) or 'Unknown agent'
    
    error_msg = result.get('error', 'Unknown error')
    error_type = result.get('error_type', 'unknown')
    
    logger.error(f"Failed to submit job to {agent_name}: {error_msg} (type: {error_type})")
    
    if tracer:
        # Special handling for known error types
        if error_type == 'budget_exceeded':
            await tracer.markdown(f"❌ **Budget exceeded** for {agent_name}")
            if 'budget_info' in result:
                budget_info = result['budget_info']
                await tracer.markdown(f"   - Expected cost: {budget_info.get('expected_cost', 0):.1f} USDM")
                await tracer.markdown(f"   - Agent remaining: {budget_info.get('remaining', 0):.1f} USDM")
        elif 'ask-the-crowd' in str(agent_name) and 'array for option field' in error_msg:
            await tracer.markdown(f"⚠️ Skipped {agent_name}: requires array format for options")
        else:
            await tracer.markdown(f"❌ Failed to submit to {agent_name}: {error_msg[:200]}... (type: {error_type})")


def create_error_response(audience_description: str, error: str) -> Dict[str, Any]:
    """Create a standardized error response."""
    return {
        "audience_description": audience_description,
        "profile": None,
        "success": False,
        "error": error
    }


def create_success_response(
    audience_description: str,
    profile: str,
    job_id: str,
    results: Dict[str, Any],
    submitted_tasks: List[Dict[str, str]],
    second_round_tasks: List[Dict[str, str]] = None,
    state_ref: Optional[Any] = None
) -> Dict[str, Any]:
    """Create a standardized success response with metadata."""
    if second_round_tasks is None:
        second_round_tasks = []
    
    response = {
        "audience_description": audience_description,
        "profile": profile,
        "metadata": {
            "job_id": job_id,
            "total_jobs": results['total_jobs'],
            "completed_jobs": results['completed_jobs'],
            "failed_jobs": results['failed_jobs'],
            "total_duration": results['total_duration'],
            "first_round_tasks": submitted_tasks,
            "second_round_tasks": second_round_tasks,
            "tasks_submitted": submitted_tasks + second_round_tasks,  # All tasks for compatibility
            "orchestrator_agent": "audience-profile-orchestrator",
            "refinement_agent": "audience-profile-refinement",
            "consolidator_agent": "audience-profile-consolidator"
        },
        "success": True,
        "error": None
    }
    
    # Note: budget summary is now handled at the actor level, not passed through state_ref
    
    return response