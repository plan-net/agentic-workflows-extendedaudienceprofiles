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
from .state import StateManager, Task
from .masumi import MasumiClient
from .storage import storage

logger = logging.getLogger(__name__)


@ray.remote
def poll_masumi_jobs(state_ref: ray.ObjectRef, job_id: str) -> ray.ObjectRef:
    """
    Poll all pending agent tasks in a job until they complete or fail.
    Updates the Ray state as tasks complete.
    
    Args:
        state_ref: Reference to AppState in Ray object store
        job_id: ID of the job to poll
        
    Returns:
        Updated state reference with all tasks completed
    """
    # Ray remote tasks run the async code with asyncio.run
    return asyncio.run(_poll_masumi_jobs_async(state_ref, job_id))


async def _poll_masumi_jobs_async(state_ref: ray.ObjectRef, job_id: str, tracer=None) -> ray.ObjectRef:
    """Async implementation of job polling"""
    job = StateManager.get_job(state_ref, job_id)
    if not job:
        logger.error(f"Job {job_id} not found")
        return state_ref
    logger.info(f"Starting background task polling for job {job_id}")
    logger.info(f"Total agent tasks to poll: {len(job.tasks)}")
    
    if tracer:
        await tracer.markdown(f"📊 Found {len(job.tasks)} agent tasks to poll")
    
    client = MasumiClient()
    
    # Polling configuration
    initial_interval = 10.0  # Start with 10 seconds
    max_interval = 60.0     # Max 60 seconds between polls
    backoff_factor = 1.5    # Increase interval by 50% each time
    timeout = 1800.0        # 30 minutes total timeout
    
    start_time = asyncio.get_event_loop().time()
    poll_interval = initial_interval
    
    while True:
        # Get job for checking completion
        job = StateManager.get_job(state_ref, job_id)
        if not job:
            logger.error(f"Job {job_id} not found during polling")
            break
        
        if job.is_complete():
            logger.info(f"All agent tasks complete for job {job_id}")
            break
        
        pending_tasks = [task for task in job.tasks if task.status in ["pending", "running"]]
        
        # Check timeout
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            logger.error(f"Polling timeout after {elapsed:.0f} seconds")
            # Batch update remaining tasks as failed
            timeout_updates = [
                {
                    'task_id': task.id,
                    'status': 'failed',
                    'error': f'Timeout after {elapsed:.0f} seconds'
                }
                for task in pending_tasks
            ]
            state_ref = StateManager.update_tasks(state_ref, job_id, timeout_updates)
            break
        
        # Collect updates to apply in batch
        task_updates = []
        
        # First, mark all pending tasks as running
        pending_to_running = [
            task for task in pending_tasks if task.status == "pending"
        ]
        if pending_to_running:
            running_updates = [
                {'task_id': task.id, 'status': 'running'}
                for task in pending_to_running
            ]
            state_ref = StateManager.update_tasks(state_ref, job_id, running_updates)
        
        # Poll each task and collect results
        for task in pending_tasks:
            try:
                logger.debug(f"Polling agent task {task.id} (status: {task.status})")
                
                # Poll the task status using the client with agent_name
                logger.debug(f"Polling status for job {task.id} from agent {task.agent_name}")
                status_result = await client.poll_job_status(task.id, task.agent_name)
                
                # Show the exact status in tracer - single line
                if tracer:
                    task_status = status_result.get('status', 'unknown')
                    await tracer.markdown(f"📊 Task {task.id[:8]}... status: **{task_status}**")
                
                if status_result['status'] == 'completed':
                    # Task completed successfully
                    result_content = status_result.get('result', '')
                    
                    # Collect update for batch processing
                    task_updates.append({
                        'task_id': task.id,
                        'status': 'completed',
                        'result': result_content
                    })
                    
                    logger.info(f"Agent task {task.id} completed successfully")
                    logger.info(f"Result preview: {result_content[:200]}..." if len(result_content) > 200 else f"Result: {result_content}")
                    
                    # Save result to filesystem (can be done independently)
                    try:
                        # Extract any hash fields from the status result
                        hash_fields = {k: v for k, v in status_result.items() if 'hash' in k.lower()}
                        
                        metadata = {
                            'input_data': task.input_data,
                            'started_at': task.started_at,
                            'completed_at': time.time(),
                            'duration': time.time() - task.started_at if task.started_at else None,
                            # 'round': task.round  # Removed - not part of new Task model
                        }
                        
                        # Add any hash fields found in the response
                        if hash_fields:
                            metadata['hashes'] = hash_fields
                            logger.info(f"Found hash fields in completed job {task.id}: {hash_fields}")
                        
                        await storage.save_agent_result(
                            job_id=job_id,
                            masumi_job_id=task.id,
                            agent_name=task.agent_name,
                            content=result_content,
                            metadata=metadata
                        )
                    except Exception as e:
                        logger.error(f"Failed to save agent result to storage: {str(e)}")
                    
                elif status_result['status'] == 'failed':
                    # Task failed
                    error_msg = status_result.get('result', status_result.get('message', 'Unknown error'))
                    
                    # Collect update for batch processing
                    task_updates.append({
                        'task_id': task.id,
                        'status': 'failed',
                        'error': error_msg
                    })
                    
                    logger.error(f"Agent task {task.id} failed: {error_msg}")
                    
            except Exception as e:
                logger.error(f"Error polling agent task {task.id}: {str(e)}")
                # Don't mark as failed immediately, will retry on next poll
        
        # Apply all collected updates in a single batch operation
        if task_updates:
            state_ref = StateManager.update_tasks(state_ref, job_id, task_updates)
            logger.info(f"Applied {len(task_updates)} task updates in batch")
        
        # Show status update periodically
        if tracer and len(pending_tasks) > 0:
            job_summary = StateManager.get_job_summary(state_ref, job_id)
            completed = job_summary['completed_tasks']
            failed = job_summary['failed_tasks']
            total = job_summary['total_tasks']
            await tracer.markdown(f"⏳ Progress: {completed + failed}/{total} completed")
        
        # Wait before next poll with exponential backoff
        logger.debug(f"Waiting {poll_interval:.0f} seconds before next poll...")
        await asyncio.sleep(poll_interval)
        poll_interval = min(poll_interval * backoff_factor, max_interval)
    
    # Return final state reference
    final_job = StateManager.get_job(state_ref, job_id)
    if not final_job:
        logger.error(f"Job {job_id} not found at end of polling")
        return state_ref
        
    completed_tasks = [t for t in final_job.tasks if t.status == "completed"]
    failed_tasks = [t for t in final_job.tasks if t.status == "failed"]
    
    logger.info(f"Polling complete. Total agent tasks: {len(final_job.tasks)}, "
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
    
    return state_ref


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