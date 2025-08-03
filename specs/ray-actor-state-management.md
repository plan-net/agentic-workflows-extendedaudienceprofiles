# Ray Actor-Based State Management Implementation Spec

## Overview

Migrate from Ray object reference-based state to a Ray Actor in `state.py`. This consolidates ALL state management (jobs, tasks, budget) into a single actor, fixing synchronization issues.

## Current Problems

1. Dual `_state_ref` globals causing desynchronization
2. Budget shows $20 after spending $9
3. Complex state passing through functions
4. No single source of truth

## Solution: StateActor in state.py

### Key Changes

1. **Convert StateManager to StateActor**
   - Remove all static methods
   - Make it a Ray actor with instance methods
   - Single global actor instance

2. **Direct method calls**
   - No more Ray.put()/get()
   - No more passing state references
   - Actor methods return data directly

### Implementation Tasks

#### 1. Update state.py

```python
@ray.remote
class StateActor:
    """All state in one actor."""
    
    def __init__(self, budget_config, agents_config):
        # Initialize all state here
        self.jobs = {}
        self.tasks = {}
        self.total_budget = ...
        self.total_spent = 0.0
        # etc
    
    def create_job(self, audience: str) -> str:
        # Create job, return job_id
    
    def record_cost(self, agent_name: str, cost: float):
        # Update budget directly
        self.total_spent += cost
```

#### 2. Global actor initialization

```python
# In state.py
_actor = None

def get_state_actor():
    global _actor
    if _actor is None:
        raise RuntimeError("StateActor not initialized")
    return _actor

def init_state_actor(budget_config, agents_config):
    global _actor
    _actor = StateActor.remote(budget_config, agents_config)
```

#### 3. Update all callers

**agent.py:**
- Remove `_state_ref` global
- Call `init_state_actor()` at start
- Replace `StateManager.method(state_ref, ...)` with `actor.method.remote(...)`

**tools.py:**
- Remove `_state_ref` global
- Use `get_state_actor()` 
- Call actor methods directly

**background.py:**
- No state passing
- Direct actor calls for updates

### Migration Checklist

- [ ] Convert StateManager class to StateActor (Ray remote)
- [ ] Add global actor instance management
- [ ] Remove all Ray.put()/get() calls
- [ ] Update agent.py to use actor
- [ ] Update tools.py to use actor  
- [ ] Update background.py to use actor
- [ ] Remove all state reference passing
- [ ] Test budget tracking works correctly

### Benefits

1. Single source of truth
2. No sync issues
3. Simpler code
4. Budget tracking will finally work

### Success Criteria

- Budget shows correct values after spending
- No state synchronization errors
- All tests pass