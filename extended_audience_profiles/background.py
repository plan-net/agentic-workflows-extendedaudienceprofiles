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
from .storage import storage

logger = logging.getLogger(__name__)


@ray.remote
def poll_masumi_jobs(job_execution_ref: ray.ObjectRef) -> ray.ObjectRef:
    """
    Poll all pending agent tasks in a job execution until they complete or fail.
    Updates the Ray state as tasks complete.
    
    Args:
        job_execution_ref: Reference to JobExecution in Ray object store
        
    Returns:
        Updated job execution reference with all tasks completed
    """
    # Ray remote tasks run the async code with asyncio.run
    return asyncio.run(_poll_masumi_jobs_async(job_execution_ref))


async def _poll_masumi_jobs_async(job_execution_ref: ray.ObjectRef, budget_ref: ray.ObjectRef = None, tracer=None) -> ray.ObjectRef:
    """Async implementation of job polling"""
    job_execution = StateManager.get_job_execution(job_execution_ref)
    logger.info(f"Starting background task polling for job execution {job_execution.job_id}")
    logger.info(f"Total agent tasks to poll: {len(job_execution.agent_tasks)}")
    
    if tracer:
        await tracer.markdown(f"📊 Found {len(job_execution.agent_tasks)} agent tasks to poll")
    
    client = MasumiClient(budget_ref=budget_ref)
    
    # Polling configuration
    initial_interval = 10.0  # Start with 10 seconds
    max_interval = 60.0     # Max 60 seconds between polls
    backoff_factor = 1.5    # Increase interval by 50% each time
    timeout = 1800.0        # 30 minutes total timeout
    
    start_time = asyncio.get_event_loop().time()
    poll_interval = initial_interval
    
    while True:
        job_execution = StateManager.get_job_execution(job_execution_ref)
        
        # Check for pending tasks
        pending_tasks = [task for task in job_execution.agent_tasks if task.status in ["pending", "running"]]
        
        if not pending_tasks:
            logger.info(f"All agent tasks complete for job execution {job_execution.job_id}")
            break
        
        # Check timeout
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            logger.error(f"Polling timeout after {elapsed:.0f} seconds")
            # Mark remaining tasks as failed
            for task in pending_tasks:
                job_execution_ref = StateManager.update_task_status(
                    job_execution_ref, 
                    task.job_id, 
                    "failed",
                    error=f"Timeout after {elapsed:.0f} seconds"
                )
            break
        
        # Poll each pending task
        for task in pending_tasks:
            try:
                logger.debug(f"Polling agent task {task.job_id} (status: {task.status})")
                
                # Update to running if still pending
                if task.status == "pending":
                    job_execution_ref = StateManager.update_task_status(
                        job_execution_ref, task.job_id, "running"
                    )
                
                # Poll the task status using the client with agent_name
                logger.debug(f"Polling status for job {task.job_id} from agent {task.agent_name}")
                status_result = await client.poll_job_status(task.job_id, task.agent_name)
                
                # Show the exact status in tracer - single line
                if tracer:
                    task_status = status_result.get('status', 'unknown')
                    await tracer.markdown(f"📊 Task {task.job_id[:8]}... status: **{task_status}**")
                
                if status_result['status'] == 'completed':
                    # Task completed successfully
                    result_content = status_result.get('result', '')
                    job_execution_ref = StateManager.update_task_status(
                        job_execution_ref, 
                        task.job_id, 
                        "completed",
                        result=result_content
                    )
                    logger.info(f"Agent task {task.job_id} completed successfully")
                    logger.info(f"Result preview: {result_content[:200]}..." if len(result_content) > 200 else f"Result: {result_content}")
                    
                    # Save result to filesystem
                    try:
                        # Extract any hash fields from the status result
                        hash_fields = {k: v for k, v in status_result.items() if 'hash' in k.lower()}
                        
                        metadata = {
                            'input_data': task.input_data,
                            'started_at': task.started_at,
                            'completed_at': time.time(),
                            'duration': time.time() - task.started_at if task.started_at else None
                        }
                        
                        # Add any hash fields found in the response
                        if hash_fields:
                            metadata['hashes'] = hash_fields
                            logger.info(f"Found hash fields in completed job {task.job_id}: {hash_fields}")
                        
                        await storage.save_agent_result(
                            job_id=job_execution.job_id,
                            masumi_job_id=task.job_id,
                            agent_name=task.agent_name,
                            content=result_content,
                            metadata=metadata
                        )
                    except Exception as e:
                        logger.error(f"Failed to save agent result to storage: {str(e)}")
                    
                elif status_result['status'] == 'failed':
                    # Task failed
                    error_msg = status_result.get('result', status_result.get('message', 'Unknown error'))
                    job_execution_ref = StateManager.update_task_status(
                        job_execution_ref, 
                        task.job_id, 
                        "failed",
                        error=error_msg
                    )
                    logger.error(f"Agent task {task.job_id} failed: {error_msg}")
                    
            except Exception as e:
                logger.error(f"Error polling agent task {task.job_id}: {str(e)}")
                # Don't mark as failed immediately, will retry on next poll
        
        # Show status update periodically
        if tracer and len(pending_tasks) > 0:
            job_execution = StateManager.get_job_execution(job_execution_ref)
            completed = len([t for t in job_execution.agent_tasks if t.status == "completed"])
            failed = len([t for t in job_execution.agent_tasks if t.status == "failed"])
            total = len(job_execution.agent_tasks)
            await tracer.markdown(f"⏳ Progress: {completed + failed}/{total} completed")
        
        # Wait before next poll with exponential backoff
        logger.debug(f"Waiting {poll_interval:.0f} seconds before next poll...")
        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * backoff_factor, max_interval)
    
    # Return final job execution reference
    final_job_execution = StateManager.get_job_execution(job_execution_ref)
    completed_tasks = final_job_execution.get_completed_tasks()
    failed_tasks = final_job_execution.get_failed_tasks()
    
    logger.info(f"Polling complete. Total agent tasks: {len(final_job_execution.agent_tasks)}, "
                f"Completed: {len(completed_tasks)}, "
                f"Failed: {len(failed_tasks)}")
    
    # Show final results summary
    if tracer:
        elapsed_minutes = (asyncio.get_event_loop().time() - start_time) / 60.0
        await tracer.markdown(f"✅ Polling complete ({elapsed_minutes:.1f} minutes)")
        
        if failed_tasks:
            # Summarize failures
            failure_reasons = {}
            for task in failed_tasks:
                reason = "Unknown error"
                if task.error:
                    if "429" in task.error or "rate limit" in task.error.lower():
                        reason = "API rate limit"
                    elif "timeout" in task.error.lower():
                        reason = "Timeout"
                    elif "budget" in task.error.lower():
                        reason = "Budget exceeded"
                    else:
                        reason = "Error"
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            
            failure_summary = ", ".join([f"{count} {reason}" for reason, count in failure_reasons.items()])
            await tracer.markdown(f"📋 Results: {len(completed_tasks)} successful, {len(failed_tasks)} failed ({failure_summary})")
        else:
            await tracer.markdown(f"📋 Results: {len(completed_tasks)} successful")
    
    return job_execution_ref


@ray.remote
def poll_single_agent_task(masumi_job_id: str, agent_name: str, timeout: float = 1800.0) -> Dict[str, Any]:
    """
    Poll a single agent task until completion. Useful for testing.
    
    Args:
        masumi_job_id: The Masumi job ID to poll
        agent_name: Name of the agent that's running the task
        timeout: Maximum time to wait in seconds
        
    Returns:
        Dict with task result or error
    """
    return asyncio.run(_poll_single_agent_task_async(masumi_job_id, agent_name, timeout))


async def _poll_single_agent_task_async(masumi_job_id: str, agent_name: str, timeout: float) -> Dict[str, Any]:
    """Async implementation of single agent task polling"""
    logger.info(f"Polling single agent task {masumi_job_id} from agent {agent_name}")
    client = MasumiClient()
    
    try:
        result = await client.wait_for_completion(masumi_job_id, agent_name, timeout=timeout)
        return {
            "success": True,
            "masumi_job_id": masumi_job_id,
            "agent_name": agent_name,
            "result": result.get('result', ''),
            "status": result.get('status', 'unknown')
        }
    except Exception as e:
        logger.error(f"Error polling agent task {masumi_job_id}: {str(e)}")
        return {
            "success": False,
            "masumi_job_id": masumi_job_id,
            "agent_name": agent_name,
            "error": str(e)
        }