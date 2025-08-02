"""
State management using Ray's object store for distributed job tracking
"""
import ray
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Tuple
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
    round: int = 1  # Track which round this job belongs to (1 = first, 2 = refinement)


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
    
    def get_tasks_by_round(self, round_num: int) -> List[Job]:
        """Get all tasks from a specific round"""
        return [task for task in self.agent_tasks if task.round == round_num]
    
    def get_completed_tasks_by_round(self, round_num: int) -> List[Job]:
        """Get completed tasks from a specific round"""
        return [task for task in self.agent_tasks 
                if task.round == round_num and task.status == "completed"]


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


@dataclass
class BudgetState:
    """Budget tracking state in Ray object store"""
    total_budget: float
    total_spent: float = 0.0
    agent_spending: Dict[str, float] = field(default_factory=dict)
    agent_limits: Dict[str, float] = field(default_factory=dict)  # max_total_spend per agent
    agent_prices: Dict[str, float] = field(default_factory=dict)  # Expected prices from yaml
    actual_costs: List[Dict[str, Any]] = field(default_factory=list)  # Track actual vs expected
    reserved_amounts: Dict[str, float] = field(default_factory=dict)  # Temporary reservations
    
    def get_agent_spent(self, agent_name: str) -> float:
        """Get total amount spent on a specific agent"""
        return self.agent_spending.get(agent_name, 0.0)
    
    def get_agent_remaining(self, agent_name: str) -> float:
        """Get remaining budget for a specific agent"""
        agent_limit = self.agent_limits.get(agent_name, float('inf'))
        agent_spent = self.get_agent_spent(agent_name)
        agent_reserved = self.reserved_amounts.get(agent_name, 0.0)
        total_remaining = self.total_budget - self.total_spent - sum(self.reserved_amounts.values())
        
        # Agent's remaining is the minimum of its own limit and total remaining
        agent_specific_remaining = agent_limit - agent_spent - agent_reserved
        return min(agent_specific_remaining, total_remaining)


class BudgetManager:
    """Manages budget state in Ray's object store"""
    
    @staticmethod
    def initialize_budget(budget_config: Dict[str, Any], agents_config: List[Dict[str, Any]]) -> ray.ObjectRef:
        """Initialize budget state from config"""
        budget_state = BudgetState(
            total_budget=budget_config.get('total_budget', float('inf')),
            agent_prices={agent['name']: agent.get('price', 0.0) for agent in agents_config},
            agent_limits={agent['name']: agent.get('max_total_spend', float('inf')) for agent in agents_config}
        )
        ref = ray.put(budget_state)
        logger.info(f"Initialized budget state with total budget: {budget_state.total_budget}")
        return ref
    
    @staticmethod
    def check_and_reserve_budget(ref: ray.ObjectRef, agent_name: str, agent_config: Dict[str, Any]) -> Tuple[bool, float, str, ray.ObjectRef]:
        """
        Check if budget allows job and reserve funds.
        
        Returns:
            Tuple of (can_afford, expected_cost, reason, new_ref)
        """
        budget_state = ray.get(ref)
        
        # Get expected price
        expected_cost = budget_state.agent_prices.get(agent_name, agent_config.get('price', 0.0))
        
        # Check agent remaining budget
        agent_remaining = budget_state.get_agent_remaining(agent_name)
        
        if agent_remaining <= 0:
            return False, expected_cost, f"No remaining budget for agent {agent_name}", ref
        
        if expected_cost > agent_remaining:
            return False, expected_cost, f"Expected cost {expected_cost} exceeds remaining budget {agent_remaining}", ref
        
        # Reserve the expected amount
        if agent_name not in budget_state.reserved_amounts:
            budget_state.reserved_amounts[agent_name] = 0.0
        budget_state.reserved_amounts[agent_name] += expected_cost
        
        new_ref = ray.put(budget_state)
        logger.info(f"Reserved {expected_cost} for {agent_name}, remaining: {agent_remaining - expected_cost}")
        return True, expected_cost, "Budget reserved", new_ref
    
    @staticmethod
    def record_actual_cost(ref: ray.ObjectRef, agent_name: str, expected: float, actual: float, job_id: str) -> ray.ObjectRef:
        """Record actual cost and update price estimates"""
        budget_state = ray.get(ref)
        
        # Remove reservation
        if agent_name in budget_state.reserved_amounts:
            budget_state.reserved_amounts[agent_name] = max(0, budget_state.reserved_amounts[agent_name] - expected)
        
        # Record actual spending
        if agent_name not in budget_state.agent_spending:
            budget_state.agent_spending[agent_name] = 0.0
        budget_state.agent_spending[agent_name] += actual
        budget_state.total_spent += actual
        
        # Track cost accuracy
        budget_state.actual_costs.append({
            'agent_name': agent_name,
            'job_id': job_id,
            'expected': expected,
            'actual': actual,
            'difference': actual - expected,
            'timestamp': time.time()
        })
        
        # Update price estimate if actual cost differs significantly (>10%)
        if expected > 0 and abs(actual - expected) / expected > 0.1:
            # Use weighted average of last 5 jobs
            recent_costs = [c for c in budget_state.actual_costs[-5:] if c['agent_name'] == agent_name]
            if recent_costs:
                avg_actual = sum(c['actual'] for c in recent_costs) / len(recent_costs)
                budget_state.agent_prices[agent_name] = avg_actual
                logger.info(f"Updated {agent_name} price estimate from {expected} to {avg_actual}")
        
        new_ref = ray.put(budget_state)
        logger.info(f"Recorded actual cost {actual} for {agent_name} (expected: {expected})")
        return new_ref
    
    @staticmethod
    def release_reservation(ref: ray.ObjectRef, agent_name: str, amount: float) -> ray.ObjectRef:
        """Release a budget reservation (e.g., if job fails to start)"""
        budget_state = ray.get(ref)
        
        if agent_name in budget_state.reserved_amounts:
            budget_state.reserved_amounts[agent_name] = max(0, budget_state.reserved_amounts[agent_name] - amount)
            logger.info(f"Released reservation of {amount} for {agent_name}")
        
        return ray.put(budget_state)
    
    @staticmethod
    def get_budget_summary(ref: ray.ObjectRef) -> Dict[str, Any]:
        """Get comprehensive budget summary"""
        budget_state = ray.get(ref)
        
        agent_summary = {}
        for agent_name in set(list(budget_state.agent_spending.keys()) + list(budget_state.agent_limits.keys())):
            agent_summary[agent_name] = {
                'spent': budget_state.get_agent_spent(agent_name),
                'reserved': budget_state.reserved_amounts.get(agent_name, 0.0),
                'remaining': budget_state.get_agent_remaining(agent_name),
                'limit': budget_state.agent_limits.get(agent_name, float('inf')),
                'expected_price': budget_state.agent_prices.get(agent_name, 0.0)
            }
        
        # Calculate cost accuracy
        cost_accuracy = None
        if budget_state.actual_costs:
            total_expected = sum(c['expected'] for c in budget_state.actual_costs if c['expected'] > 0)
            total_actual = sum(c['actual'] for c in budget_state.actual_costs)
            if total_expected > 0:
                accuracy_pct = ((total_actual - total_expected) / total_expected) * 100
                cost_accuracy = f"Actual costs were {accuracy_pct:+.1f}% vs estimates"
        
        return {
            'total_budget': budget_state.total_budget,
            'total_spent': budget_state.total_spent,
            'total_reserved': sum(budget_state.reserved_amounts.values()),
            'total_remaining': budget_state.total_budget - budget_state.total_spent - sum(budget_state.reserved_amounts.values()),
            'agents': agent_summary,
            'cost_accuracy': cost_accuracy,
            'recent_costs': budget_state.actual_costs[-10:]  # Last 10 transactions
        }