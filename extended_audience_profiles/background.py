"""
Background Ray tasks for polling Masumi job status
"""
import ray
import asyncio
import logging
import requests
import time
import json
from typing import List, Dict, Any, Optional
from .state import StateManager, Job
from .masumi import MasumiClient

logger = logging.getLogger(__name__)


@ray.remote
def poll_masumi_jobs(submission_ref: ray.ObjectRef) -> ray.ObjectRef:
    """
    Poll all pending jobs in a submission until they complete or fail.
    Updates the Ray state as jobs complete.
    
    Args:
        submission_ref: Reference to JobSubmission in Ray object store
        
    Returns:
        Updated submission reference with all jobs completed
    """
    # Ray remote tasks run the async code with asyncio.run
    return asyncio.run(_poll_masumi_jobs_async(submission_ref))


async def _poll_masumi_jobs_async(submission_ref: ray.ObjectRef, tracer=None) -> ray.ObjectRef:
    """Async implementation of job polling"""
    submission = StateManager.get_submission(submission_ref)
    logger.info(f"Starting background job polling for submission {submission.submission_id}")
    logger.info(f"Total jobs to poll: {len(submission.jobs)}")
    
    if tracer:
        await tracer.markdown(f"📊 Found {len(submission.jobs)} jobs to poll")
    
    client = MasumiClient()
    
    # Polling configuration
    initial_interval = 10.0  # Start with 10 seconds
    max_interval = 60.0     # Max 60 seconds between polls
    backoff_factor = 1.5    # Increase interval by 50% each time
    timeout = 1800.0        # 30 minutes total timeout
    
    start_time = asyncio.get_event_loop().time()
    poll_interval = initial_interval
    
    while True:
        submission = StateManager.get_submission(submission_ref)
        
        # Check for pending jobs
        pending_jobs = [job for job in submission.jobs if job.status in ["pending", "running"]]
        
        if not pending_jobs:
            logger.info(f"All jobs complete for submission {submission.submission_id}")
            break
        
        # Check timeout
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            logger.error(f"Polling timeout after {elapsed:.0f} seconds")
            # Mark remaining jobs as failed
            for job in pending_jobs:
                submission_ref = StateManager.update_job_status(
                    submission_ref, 
                    job.job_id, 
                    "failed",
                    error=f"Timeout after {elapsed:.0f} seconds"
                )
            break
        
        # Poll each pending job
        for job in pending_jobs:
            try:
                logger.debug(f"Polling job {job.job_id} (status: {job.status})")
                
                # Update to running if still pending
                if job.status == "pending":
                    submission_ref = StateManager.update_job_status(
                        submission_ref, job.job_id, "running"
                    )
                
                # Poll the job status using the client with agent_name
                status_result = await client.poll_job_status(job.job_id, job.agent_name)
                
                # Show the exact status in tracer - single line
                if tracer:
                    job_status = status_result.get('status', 'unknown')
                    await tracer.markdown(f"📊 Job {job.job_id[:8]}... status: **{job_status}**")
                
                if status_result['status'] == 'completed':
                    # Job completed successfully
                    result_content = status_result.get('result', '')
                    submission_ref = StateManager.update_job_status(
                        submission_ref, 
                        job.job_id, 
                        "completed",
                        result=result_content
                    )
                    logger.info(f"Job {job.job_id} completed successfully")
                    logger.info(f"Result preview: {result_content[:200]}..." if len(result_content) > 200 else f"Result: {result_content}")
                    
                elif status_result['status'] == 'failed':
                    # Job failed
                    error_msg = status_result.get('result', status_result.get('message', 'Unknown error'))
                    submission_ref = StateManager.update_job_status(
                        submission_ref, 
                        job.job_id, 
                        "failed",
                        error=error_msg
                    )
                    logger.error(f"Job {job.job_id} failed: {error_msg}")
                    
            except Exception as e:
                logger.error(f"Error polling job {job.job_id}: {str(e)}")
                # Don't mark as failed immediately, will retry on next poll
        
        # Show status update periodically
        if tracer and len(pending_jobs) > 0:
            submission = StateManager.get_submission(submission_ref)
            completed = len([j for j in submission.jobs if j.status == "completed"])
            failed = len([j for j in submission.jobs if j.status == "failed"])
            total = len(submission.jobs)
            await tracer.markdown(f"⏳ Progress: {completed + failed}/{total} completed")
        
        # Wait before next poll with exponential backoff
        logger.debug(f"Waiting {poll_interval:.0f} seconds before next poll...")
        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * backoff_factor, max_interval)
    
    # Return final submission reference
    final_submission = StateManager.get_submission(submission_ref)
    completed_jobs = final_submission.get_completed_jobs()
    failed_jobs = final_submission.get_failed_jobs()
    
    logger.info(f"Polling complete. Total jobs: {len(final_submission.jobs)}, "
                f"Completed: {len(completed_jobs)}, "
                f"Failed: {len(failed_jobs)}")
    
    # Show final results summary
    if tracer:
        elapsed_minutes = (asyncio.get_event_loop().time() - start_time) / 60.0
        await tracer.markdown(f"✅ Polling complete ({elapsed_minutes:.1f} minutes)")
        
        if failed_jobs:
            # Summarize failures
            failure_reasons = {}
            for job in failed_jobs:
                reason = "Unknown error"
                if job.error:
                    if "429" in job.error or "rate limit" in job.error.lower():
                        reason = "API rate limit"
                    elif "timeout" in job.error.lower():
                        reason = "Timeout"
                    elif "budget" in job.error.lower():
                        reason = "Budget exceeded"
                    else:
                        reason = "Error"
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            
            failure_summary = ", ".join([f"{count} {reason}" for reason, count in failure_reasons.items()])
            await tracer.markdown(f"📋 Results: {len(completed_jobs)} successful, {len(failed_jobs)} failed ({failure_summary})")
        else:
            await tracer.markdown(f"📋 Results: {len(completed_jobs)} successful")
    
    return submission_ref


@ray.remote
def poll_single_job(job_id: str, agent_name: str, timeout: float = 1800.0) -> Dict[str, Any]:
    """
    Poll a single job until completion. Useful for testing.
    
    Args:
        job_id: The job ID to poll
        agent_name: Name of the agent that's running the job
        timeout: Maximum time to wait in seconds
        
    Returns:
        Dict with job result or error
    """
    return asyncio.run(_poll_single_job_async(job_id, agent_name, timeout))


async def _poll_single_job_async(job_id: str, agent_name: str, timeout: float) -> Dict[str, Any]:
    """Async implementation of single job polling"""
    logger.info(f"Polling single job {job_id} from agent {agent_name}")
    client = MasumiClient()
    
    try:
        result = await client.wait_for_completion(job_id, agent_name, timeout=timeout)
        return {
            "success": True,
            "job_id": job_id,
            "agent_name": agent_name,
            "result": result.get('result', ''),
            "status": result.get('status', 'unknown')
        }
    except Exception as e:
        logger.error(f"Error polling job {job_id}: {str(e)}")
        return {
            "success": False,
            "job_id": job_id,
            "agent_name": agent_name,
            "error": str(e)
        }