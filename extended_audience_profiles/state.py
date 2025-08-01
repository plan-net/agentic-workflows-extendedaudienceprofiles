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
    """Individual job information"""
    job_id: str
    agent_name: str
    input_data: Dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[Any] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class JobSubmission:
    """Container for a group of related jobs"""
    submission_id: str
    audience_description: str
    jobs: List[Job]
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    def is_complete(self) -> bool:
        """Check if all jobs are complete (either completed or failed)"""
        return all(job.status in ["completed", "failed"] for job in self.jobs)
    
    def get_completed_jobs(self) -> List[Job]:
        """Get all successfully completed jobs"""
        return [job for job in self.jobs if job.status == "completed"]
    
    def get_failed_jobs(self) -> List[Job]:
        """Get all failed jobs"""
        return [job for job in self.jobs if job.status == "failed"]


class StateManager:
    """Manages job state in Ray's object store"""
    
    @staticmethod
    def create_submission(audience_description: str) -> tuple[str, ray.ObjectRef]:
        """Create a new job submission and store in Ray"""
        submission_id = str(uuid.uuid4())
        submission = JobSubmission(
            submission_id=submission_id,
            audience_description=audience_description,
            jobs=[]
        )
        ref = ray.put(submission)
        logger.info(f"Created submission {submission_id} with ref {ref}")
        return submission_id, ref
    
    @staticmethod
    def add_job(ref: ray.ObjectRef, job: Job) -> ray.ObjectRef:
        """Add a job to the submission"""
        submission = ray.get(ref)
        submission.jobs.append(job)
        new_ref = ray.put(submission)
        logger.info(f"Added job {job.job_id} to submission {submission.submission_id}")
        return new_ref
    
    @staticmethod
    def update_job_status(ref: ray.ObjectRef, job_id: str, status: str, 
                          result: Any = None, error: str = None) -> ray.ObjectRef:
        """Update the status of a specific job"""
        submission = ray.get(ref)
        
        for job in submission.jobs:
            if job.job_id == job_id:
                job.status = status
                if status == "running" and job.started_at is None:
                    job.started_at = time.time()
                elif status in ["completed", "failed"]:
                    job.completed_at = time.time()
                    if result is not None:
                        job.result = result
                    if error is not None:
                        job.error = error
                break
        
        # Check if all jobs are complete
        if submission.is_complete() and submission.completed_at is None:
            submission.completed_at = time.time()
            logger.info(f"All jobs complete for submission {submission.submission_id}")
        
        new_ref = ray.put(submission)
        logger.info(f"Updated job {job_id} status to {status}")
        return new_ref
    
    @staticmethod
    def get_submission(ref: ray.ObjectRef) -> JobSubmission:
        """Get the current submission state"""
        return ray.get(ref)
    
    @staticmethod
    def get_results(ref: ray.ObjectRef) -> Dict[str, Any]:
        """Get all results from completed jobs"""
        submission = ray.get(ref)
        results = {}
        
        for job in submission.jobs:
            if job.status == "completed" and job.result is not None:
                results[job.job_id] = {
                    "agent_name": job.agent_name,
                    "input_data": job.input_data,
                    "result": job.result,
                    "duration": job.completed_at - job.started_at if job.started_at else None
                }
        
        return {
            "submission_id": submission.submission_id,
            "audience_description": submission.audience_description,
            "total_jobs": len(submission.jobs),
            "completed_jobs": len(submission.get_completed_jobs()),
            "failed_jobs": len(submission.get_failed_jobs()),
            "results": results,
            "is_complete": submission.is_complete(),
            "total_duration": submission.completed_at - submission.created_at if submission.completed_at else None
        }