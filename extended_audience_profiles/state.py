"""
State management using Ray's object store for distributed job tracking
"""
import ray
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
import time
import uuid
import logging

logger = logging.getLogger(__name__)


@dataclass
class Job:
    """Individual Masumi agent task within a job execution"""
    job_id: str  # Masumi job ID
    agent_name: str
    input_data: Dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class JobExecution:
    """Container for a complete job execution with multiple agent tasks"""
    job_id: str
    audience_description: str
    agent_tasks: List[Job]  # Individual Masumi agent tasks
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    def is_complete(self) -> bool:
        """Check if all agent tasks are complete (either completed or failed)"""
        return all(task.status in ["completed", "failed"] for task in self.agent_tasks)
    
    def get_completed_tasks(self) -> List[Job]:
        """Get all successfully completed agent tasks"""
        return [task for task in self.agent_tasks if task.status == "completed"]
    
    def get_failed_tasks(self) -> List[Job]:
        """Get all failed agent tasks"""
        return [task for task in self.agent_tasks if task.status == "failed"]


class StateManager:
    """Manages job execution state in Ray's object store"""
    
    @staticmethod
    def create_job_execution(audience_description: str) -> tuple[str, ray.ObjectRef]:
        """Create a new job execution and store in Ray"""
        job_id = str(uuid.uuid4())
        job_execution = JobExecution(
            job_id=job_id,
            audience_description=audience_description,
            agent_tasks=[]
        )
        ref = ray.put(job_execution)
        logger.info(f"Created job execution {job_id} with ref {ref}")
        return job_id, ref
    
    @staticmethod
    def add_agent_task(ref: ray.ObjectRef, task: Job) -> ray.ObjectRef:
        """Add an agent task to the job execution"""
        job_execution = ray.get(ref)
        job_execution.agent_tasks.append(task)
        new_ref = ray.put(job_execution)
        logger.info(f"Added agent task {task.job_id} to job execution {job_execution.job_id}")
        return new_ref
    
    @staticmethod
    def update_task_status(ref: ray.ObjectRef, task_id: str, status: str, 
                          result: Any = None, error: str = None) -> ray.ObjectRef:
        """Update the status of a specific agent task"""
        job_execution = ray.get(ref)
        
        for task in job_execution.agent_tasks:
            if task.job_id == task_id:
                task.status = status
                if status == "running" and task.started_at is None:
                    task.started_at = time.time()
                elif status in ["completed", "failed"]:
                    task.completed_at = time.time()
                    if result is not None:
                        task.result = result
                    if error is not None:
                        task.error = error
                break
        
        # Check if all tasks are complete
        if job_execution.is_complete() and job_execution.completed_at is None:
            job_execution.completed_at = time.time()
            logger.info(f"All agent tasks complete for job execution {job_execution.job_id}")
        
        new_ref = ray.put(job_execution)
        logger.info(f"Updated agent task {task_id} status to {status}")
        return new_ref
    
    @staticmethod
    def get_job_execution(ref: ray.ObjectRef) -> JobExecution:
        """Get the current job execution state"""
        return ray.get(ref)
    
    @staticmethod
    def get_results(ref: ray.ObjectRef) -> Dict[str, Any]:
        """Get all results from completed agent tasks"""
        job_execution = ray.get(ref)
        results = {}
        
        for task in job_execution.agent_tasks:
            if task.status == "completed" and task.result is not None:
                results[task.job_id] = {
                    "agent_name": task.agent_name,
                    "input_data": task.input_data,
                    "result": task.result,
                    "duration": task.completed_at - task.started_at if task.started_at else None
                }
        
        return {
            "job_id": job_execution.job_id,
            "audience_description": job_execution.audience_description,
            "total_jobs": len(job_execution.agent_tasks),
            "completed_jobs": len(job_execution.get_completed_tasks()),
            "failed_jobs": len(job_execution.get_failed_tasks()),
            "results": results,
            "is_complete": job_execution.is_complete(),
            "total_duration": job_execution.completed_at - job_execution.created_at if job_execution.completed_at else None
        }