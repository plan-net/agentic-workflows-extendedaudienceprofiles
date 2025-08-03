"""
Simplified state management using Ray's object store
"""
import ray
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
import time
import uuid
import logging

logger = logging.getLogger(__name__)

# Global actor instance
_actor = None

def get_state_actor():
    """Get the state actor."""
    global _actor
    if _actor is None:
        raise RuntimeError("StateActor not initialized. Call init_state_actor() first.")
    return _actor

def init_state_actor(budget_config: Dict[str, Any], agents_config: List[Dict[str, Any]]):
    """Initialize the state actor."""
    global _actor
    if _actor is not None:
        logger.warning("StateActor already initialized")
        return _actor
    
    _actor = StateActor.remote(budget_config, agents_config)
    logger.info("StateActor initialized")
    return _actor


@dataclass
class Task:
    """Individual agent task"""
    id: str
    agent_name: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"  # pending, running, completed, failed
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class Job:
    """Job execution with multiple agent tasks"""
    id: str
    audience: str
    tasks: List[Task]
    created_at: float = field(default_factory=time.time)
    completed_at: Optional[float] = None
    
    def is_complete(self) -> bool:
        """Check if all tasks are done"""
        return all(t.status in ["completed", "failed"] for t in self.tasks)



@ray.remote(max_restarts=3)
class StateActor:
    """Centralized state management actor for the entire application"""
    
    def __init__(self, budget_config: Dict[str, Any], agents_config: List[Dict[str, Any]]):
        """Initialize application state"""
        # Budget state
        self.total_budget = budget_config.get('total_budget', float('inf'))
        self.total_spent = 0.0
        self.agent_spending = {}
        self.agent_limits = {agent['name']: agent.get('max_total_spend', float('inf')) for agent in agents_config}
        self.agent_prices = {agent['name']: agent.get('price', 0.0) for agent in agents_config}
        self.reserved_amounts = {}
        
        # Job state
        self.jobs = {}
        
        logger.info(f"StateActor initialized with budget: {self.total_budget}")
    
    def create_job(self, audience: str) -> str:
        """Create a new job"""
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, audience=audience, tasks=[])
        self.jobs[job_id] = job
        logger.info(f"Created job {job_id}")
        return job_id
    
    def add_task(self, job_id: str, task: Task) -> bool:
        """Add task to a job"""
        if job_id in self.jobs:
            self.jobs[job_id].tasks.append(task)
            logger.info(f"Added task {task.id} to job {job_id}")
            return True
        return False
    
    def update_tasks(self, job_id: str, updates: List[Dict[str, Any]]) -> bool:
        """Batch update tasks"""
        if job_id not in self.jobs:
            logger.warning(f"Job {job_id} not found")
            return False
        
        job = self.jobs[job_id]
        task_map = {task.id: task for task in job.tasks}
        
        for update in updates:
            task_id = update['task_id']
            if task_id in task_map:
                task = task_map[task_id]
                task.status = update['status']
                
                if update['status'] == "running" and task.started_at is None:
                    task.started_at = time.time()
                elif update['status'] in ["completed", "failed"]:
                    task.completed_at = time.time()
                    if 'result' in update:
                        task.result = update['result']
                    if 'error' in update:
                        task.error = update['error']
        
        # Check if job is complete
        if job.is_complete() and job.completed_at is None:
            job.completed_at = time.time()
            logger.info(f"Job {job_id} completed")
        
        logger.info(f"Updated {len(updates)} tasks in job {job_id}")
        return True
    
    def reserve_budget(self, agent_name: str, expected_cost: float) -> Tuple[bool, str, Dict[str, Any]]:
        """Reserve budget for a job"""
        # Check agent limit
        agent_limit = self.agent_limits.get(agent_name, float('inf'))
        agent_spent = self.agent_spending.get(agent_name, 0.0)
        agent_reserved = self.reserved_amounts.get(agent_name, 0.0)
        agent_remaining = agent_limit - agent_spent - agent_reserved
        
        # Check total budget
        total_reserved = sum(self.reserved_amounts.values())
        total_remaining = self.total_budget - self.total_spent - total_reserved
        
        # Can afford if both agent and total budget allow
        can_afford = expected_cost <= min(agent_remaining, total_remaining)
        
        if can_afford:
            if agent_name not in self.reserved_amounts:
                self.reserved_amounts[agent_name] = 0.0
            self.reserved_amounts[agent_name] += expected_cost
            reason = "Budget reserved"
            logger.info(f"Reserved {expected_cost} for {agent_name}")
        else:
            if expected_cost > agent_remaining:
                reason = f"Agent budget exceeded (remaining: {agent_remaining})"
            else:
                reason = f"Total budget exceeded (remaining: {total_remaining})"
            logger.warning(f"Cannot reserve {expected_cost} for {agent_name}: {reason}")
        
        return can_afford, reason, self.get_budget_summary()
    
    def record_cost(self, agent_name: str, expected: float, actual: float) -> Dict[str, Any]:
        """Record actual cost and release reservation"""
        # Release reservation
        if agent_name in self.reserved_amounts:
            self.reserved_amounts[agent_name] = max(0, self.reserved_amounts[agent_name] - expected)
        
        # Record spending
        if agent_name not in self.agent_spending:
            self.agent_spending[agent_name] = 0.0
        self.agent_spending[agent_name] += actual
        self.total_spent += actual
        
        # Debug logging
        logger.info(f"Budget update for {agent_name}:")
        logger.info(f"  - Previous spending: {self.agent_spending[agent_name] - actual:.2f}")
        logger.info(f"  - New spending: {self.agent_spending[agent_name]:.2f}")
        logger.info(f"  - Total spent: {self.total_spent:.2f}")
        
        logger.info(f"Recorded cost {actual} for {agent_name} (expected: {expected})")
        return self.get_budget_summary()
    
    def release_reservation(self, agent_name: str, amount: float) -> None:
        """Release a budget reservation if job fails"""
        if agent_name in self.reserved_amounts:
            self.reserved_amounts[agent_name] = max(0, self.reserved_amounts[agent_name] - amount)
            logger.info(f"Released reservation of {amount} for {agent_name}")
    
    def get_job(self, job_id: str) -> Optional[Job]:
        """Get a specific job"""
        return self.jobs.get(job_id)
    
    def get_job_results(self, job_id: str) -> Dict[str, Any]:
        """Get formatted results for a job"""
        job = self.jobs.get(job_id)
        
        if not job:
            return {"error": f"Job {job_id} not found"}
        
        results = {}
        for task in job.tasks:
            if task.status == "completed" and task.result:
                results[task.id] = {
                    "agent_name": task.agent_name,
                    "result": task.result,
                    "input_data": task.input_data,
                    "duration": task.completed_at - task.started_at if task.started_at else None
                }
        
        return {
            "job_id": job_id,
            "audience_description": job.audience,
            "total_jobs": len(job.tasks),
            "completed_jobs": len([t for t in job.tasks if t.status == "completed"]),
            "failed_jobs": len([t for t in job.tasks if t.status == "failed"]),
            "results": results,
            "is_complete": job.is_complete(),
            "total_duration": job.completed_at - job.created_at if job.completed_at else None
        }
    
    def get_job_summary(self, job_id: str) -> Dict[str, Any]:
        """Get lightweight job summary"""
        job = self.jobs.get(job_id)
        
        if not job:
            return {"error": f"Job {job_id} not found"}
        
        return {
            "job_id": job_id,
            "total_tasks": len(job.tasks),
            "completed_tasks": len([t for t in job.tasks if t.status == "completed"]),
            "failed_tasks": len([t for t in job.tasks if t.status == "failed"]),
            "is_complete": job.is_complete()
        }
    
    def get_budget_summary(self) -> Dict[str, Any]:
        """Get budget summary"""
        total_reserved = sum(self.reserved_amounts.values())
        total_remaining = self.total_budget - self.total_spent - total_reserved
        
        agents = {}
        for agent_name in set(list(self.agent_spending.keys()) + list(self.agent_limits.keys())):
            spent = self.agent_spending.get(agent_name, 0.0)
            reserved = self.reserved_amounts.get(agent_name, 0.0)
            limit = self.agent_limits.get(agent_name, float('inf'))
            remaining = limit - spent - reserved
            
            agents[agent_name] = {
                'spent': spent,
                'reserved': reserved,
                'remaining': min(remaining, total_remaining),
                'limit': limit,
                'expected_price': self.agent_prices.get(agent_name, 0.0)
            }
        
        return {
            'total_budget': self.total_budget,
            'total_spent': self.total_spent,
            'total_reserved': total_reserved,
            'total_remaining': total_remaining,
            'agents': agents
        }

