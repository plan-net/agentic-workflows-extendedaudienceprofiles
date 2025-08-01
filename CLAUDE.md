# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Extended Audience Profiles** is a kodosumi-based AI agent that generates extended audience profiles with citations using Exa.ai's web search capabilities.

**Compatibility**: This project is optimized for kodosumi v0.9.3. Different versions may require code adjustments.

## Development Setup

### Prerequisites
- Python 3.12+ (pyenv recommended for version management)
- kodosumi v0.9.3
- Ray cluster (for distributed processing)
- Exa.ai API key

### Installation
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -e .
```

### Environment Variables
Set your Exa.ai API key in `.env`:
```bash
EXA_API_KEY=your_exa_api_key_here
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
- **Business Logic**: `extended_audience_profiles/agent.py` - Pure AI agent logic
- **Service Wrapper**: `extended_audience_profiles/query.py` - Kodosumi integration
- **Framework**: Kodosumi (Ray + FastAPI)
- **External API**: Exa.ai for web search and answer generation
- **Response Format**: Markdown with citations

### Key Dependencies
- `kodosumi` - Service framework and deployment
- `ray` - Distributed computing and task execution  
- `openai` - Client library for Exa.ai API interface
- `python-dotenv` - Environment variable management
- `pytest` - Testing framework