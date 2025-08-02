# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Extended Audience Profiles** is a kodosumi-based AI agent that generates comprehensive, evidence-based audience profiles through multi-agent orchestration using the Masumi Network's Agent-2-Agent Protocol.

**Compatibility**: This project is optimized for kodosumi v0.9.3. Different versions may require code adjustments.

## Development Setup

### Prerequisites
- Python 3.12+ (pyenv recommended for version management)
- kodosumi v0.9.3
- Ray cluster (for distributed processing)
- Masumi Network API credentials

### Installation
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### Environment Variables
Set your Masumi Network credentials in `.env`:
```bash
PAYMENT_SERVICE_URL=your_masumi_payment_url
PAYMENT_API_KEY=your_masumi_api_key
```

## Commands

### Development
- `just start` - Start full service (Ray + kodosumi deployment + spooler + admin panel)
- `just stop` - Stop all services
- `just test` - Run unit tests (mocked, fast)
- `just test-integration` - Run integration tests (real API calls)
- `just test-all` - Run all tests

### Manual Development Workflow
```bash
source .venv/bin/activate

# Start Ray cluster
ray start --head --disable-usage-stats

# Deploy application
koco deploy -r

# Start execution spooler (REQUIRED for executions to run)
koco spool &

# Start admin panel
koco serve --register http://localhost:8001/-/routes

# Access admin panel at http://localhost:3370
```

### Production Deployment
Use kodosumi configuration files in `data/config/`:
- `config.yaml` - Main deployment configuration
- `extended_audience_profiles.yaml.example` - Service template
- `extended_audience_profiles.yaml` - Service-specific configuration (create from example)

## Architecture

### Core Components
- **Business Logic**: `extended_audience_profiles/agent.py` - 5-phase orchestration workflow
- **Service Wrapper**: `extended_audience_profiles/query.py` - Kodosumi integration
- **State Management**: `extended_audience_profiles/state.py` - Simplified unified state (jobs + budget)
- **Framework**: Kodosumi (Ray + FastAPI)
- **External API**: Masumi Network for distributed agent orchestration
- **Response Format**: Markdown with citations

### Key Dependencies
- `kodosumi` - Service framework and deployment
- `ray` - Distributed computing and task execution  
- `openai` - Client library for LLM interactions
- `python-dotenv` - Environment variable management
- `pytest` - Testing framework

## Code Organization

### File Structure
- `agent.py` - Core orchestration logic (5-phase workflow)
- `state.py` - Simplified unified state management (jobs + budget in single AppState)
- `context_management.py` - Token counting and smart truncation (merged from token_utils + truncation)
- `formatting.py` - Result formatting and token analysis
- `errors.py` - Error response handling
- `prompts/` - Externalized agent prompts
  - `orchestrator.txt` - Initial research planning
  - `refinement.txt` - Gap analysis and deep-dive planning
  - `consolidator.txt` - Final synthesis
- `background.py` - Async Masumi job polling
- `storage.py` - Filesystem persistence
- `tools.py` - OpenAI function calling tools (simplified, no Pydantic models)
- `masumi.py` - Masumi Network API client
- `query.py` - Kodosumi service integration

### Key Patterns
- **5-Phase Workflow**: Orchestration → Initial Collection → Refinement → Deep-Dive → Synthesis
- **Unified State**: Single `AppState` object manages both jobs and budget tracking
- **Context-Aware**: Automatic token counting and truncation for o3-mini (200k tokens)
- **Budget Management**: Real-time tracking with per-agent limits in USDM