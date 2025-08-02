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


@dataclass
class Task:
    """Individual agent task"""
    id: str
    agent_name: str
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


@dataclass
class AppState:
    """Single application state combining jobs and budget"""
    # Budget tracking
    total_budget: float
    total_spent: float = 0.0
    agent_spending: Dict[str, float] = field(default_factory=dict)
    agent_limits: Dict[str, float] = field(default_factory=dict)
    agent_prices: Dict[str, float] = field(default_factory=dict)
    reserved_amounts: Dict[str, float] = field(default_factory=dict)
    
    # Active jobs
    jobs: Dict[str, Job] = field(default_factory=dict)


class StateManager:
    """Single state manager for the entire application"""
    
    @staticmethod
    def initialize(budget_config: Dict[str, Any], agents_config: List[Dict[str, Any]]) -> ray.ObjectRef:
        """Initialize application state"""
        state = AppState(
            total_budget=budget_config.get('total_budget', float('inf')),
            agent_prices={agent['name']: agent.get('price', 0.0) for agent in agents_config},
            agent_limits={agent['name']: agent.get('max_total_spend', float('inf')) for agent in agents_config}
        )
        ref = ray.put(state)
        logger.info(f"Initialized app state with budget: {state.total_budget}")
        return ref
    
    @staticmethod
    def create_job(ref: ray.ObjectRef, audience: str) -> Tuple[str, ray.ObjectRef]:
        """Create a new job"""
        state = ray.get(ref)
        job_id = str(uuid.uuid4())
        job = Job(id=job_id, audience=audience, tasks=[])
        state.jobs[job_id] = job
        new_ref = ray.put(state)
        logger.info(f"Created job {job_id}")
        return job_id, new_ref
    
    @staticmethod
    def add_task(ref: ray.ObjectRef, job_id: str, task: Task) -> ray.ObjectRef:
        """Add task to a job"""
        state = ray.get(ref)
        if job_id in state.jobs:
            state.jobs[job_id].tasks.append(task)
            logger.info(f"Added task {task.id} to job {job_id}")
        new_ref = ray.put(state)
        return new_ref
    
    @staticmethod
    def update_tasks(ref: ray.ObjectRef, job_id: str, updates: List[Dict[str, Any]]) -> ray.ObjectRef:
        """Batch update tasks"""
        state = ray.get(ref)
        
        if job_id not in state.jobs:
            logger.warning(f"Job {job_id} not found")
            return ray.put(state)
        
        job = state.jobs[job_id]
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
        
        new_ref = ray.put(state)
        logger.info(f"Updated {len(updates)} tasks in job {job_id}")
        return new_ref
    
    @staticmethod
    def reserve_budget(ref: ray.ObjectRef, agent_name: str, expected_cost: float) -> Tuple[bool, str, ray.ObjectRef]:
        """Reserve budget for a job"""
        state = ray.get(ref)
        
        # Check agent limit
        agent_limit = state.agent_limits.get(agent_name, float('inf'))
        agent_spent = state.agent_spending.get(agent_name, 0.0)
        agent_reserved = state.reserved_amounts.get(agent_name, 0.0)
        agent_remaining = agent_limit - agent_spent - agent_reserved
        
        # Check total budget
        total_reserved = sum(state.reserved_amounts.values())
        total_remaining = state.total_budget - state.total_spent - total_reserved
        
        # Can afford if both agent and total budget allow
        can_afford = expected_cost <= min(agent_remaining, total_remaining)
        
        if can_afford:
            if agent_name not in state.reserved_amounts:
                state.reserved_amounts[agent_name] = 0.0
            state.reserved_amounts[agent_name] += expected_cost
            reason = "Budget reserved"
            logger.info(f"Reserved {expected_cost} for {agent_name}")
        else:
            if expected_cost > agent_remaining:
                reason = f"Agent budget exceeded (remaining: {agent_remaining})"
            else:
                reason = f"Total budget exceeded (remaining: {total_remaining})"
            logger.warning(f"Cannot reserve {expected_cost} for {agent_name}: {reason}")
        
        new_ref = ray.put(state)
        return can_afford, reason, new_ref
    
    @staticmethod
    def record_cost(ref: ray.ObjectRef, agent_name: str, expected: float, actual: float) -> ray.ObjectRef:
        """Record actual cost and release reservation"""
        state = ray.get(ref)
        
        # Release reservation
        if agent_name in state.reserved_amounts:
            state.reserved_amounts[agent_name] = max(0, state.reserved_amounts[agent_name] - expected)
        
        # Record spending
        if agent_name not in state.agent_spending:
            state.agent_spending[agent_name] = 0.0
        state.agent_spending[agent_name] += actual
        state.total_spent += actual
        
        # Debug logging
        logger.info(f"Budget update for {agent_name}:")
        logger.info(f"  - Previous spending: {state.agent_spending[agent_name] - actual:.2f}")
        logger.info(f"  - New spending: {state.agent_spending[agent_name]:.2f}")
        logger.info(f"  - Total spent: {state.total_spent:.2f}")
        
        new_ref = ray.put(state)
        logger.info(f"Recorded cost {actual} for {agent_name} (expected: {expected})")
        return new_ref
    
    @staticmethod
    def release_reservation(ref: ray.ObjectRef, agent_name: str, amount: float) -> ray.ObjectRef:
        """Release a budget reservation if job fails"""
        state = ray.get(ref)
        
        if agent_name in state.reserved_amounts:
            state.reserved_amounts[agent_name] = max(0, state.reserved_amounts[agent_name] - amount)
            logger.info(f"Released reservation of {amount} for {agent_name}")
        
        return ray.put(state)
    
    @staticmethod
    def get_job(ref: ray.ObjectRef, job_id: str) -> Optional[Job]:
        """Get a specific job"""
        state = ray.get(ref)
        return state.jobs.get(job_id)
    
    @staticmethod
    def get_job_results(ref: ray.ObjectRef, job_id: str) -> Dict[str, Any]:
        """Get formatted results for a job"""
        state = ray.get(ref)
        job = state.jobs.get(job_id)
        
        if not job:
            return {"error": f"Job {job_id} not found"}
        
        results = {}
        for task in job.tasks:
            if task.status == "completed" and task.result:
                results[task.id] = {
                    "agent_name": task.agent_name,
                    "result": task.result,
                    "input_data": task.input_data if hasattr(task, 'input_data') else {},
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
    
    @staticmethod
    def get_job_summary(ref: ray.ObjectRef, job_id: str) -> Dict[str, Any]:
        """Get lightweight job summary"""
        state = ray.get(ref)
        job = state.jobs.get(job_id)
        
        if not job:
            return {"error": f"Job {job_id} not found"}
        
        return {
            "job_id": job_id,
            "total_tasks": len(job.tasks),
            "completed_tasks": len([t for t in job.tasks if t.status == "completed"]),
            "failed_tasks": len([t for t in job.tasks if t.status == "failed"]),
            "is_complete": job.is_complete()
        }
    
    @staticmethod
    def get_budget_summary(ref: ray.ObjectRef) -> Dict[str, Any]:
        """Get budget summary"""
        state = ray.get(ref)
        
        total_reserved = sum(state.reserved_amounts.values())
        total_remaining = state.total_budget - state.total_spent - total_reserved
        
        agents = {}
        for agent_name in set(list(state.agent_spending.keys()) + list(state.agent_limits.keys())):
            spent = state.agent_spending.get(agent_name, 0.0)
            reserved = state.reserved_amounts.get(agent_name, 0.0)
            limit = state.agent_limits.get(agent_name, float('inf'))
            remaining = limit - spent - reserved
            
            agents[agent_name] = {
                'spent': spent,
                'reserved': reserved,
                'remaining': min(remaining, total_remaining),
                'limit': limit,
                'expected_price': state.agent_prices.get(agent_name, 0.0)
            }
        
        return {
            'total_budget': state.total_budget,
            'total_spent': state.total_spent,
            'total_reserved': total_reserved,
            'total_remaining': total_remaining,
            'agents': agents
        }

