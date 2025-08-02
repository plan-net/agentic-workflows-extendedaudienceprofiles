# Extended Audience Profiles

An advanced AI-powered audience research system that leverages the Masumi Network's Agent-2-Agent Protocol to generate comprehensive, evidence-based audience profiles through multi-agent orchestration.

> **Compatibility**: Optimized for kodosumi v0.9.3 and Masumi Network

## 🌟 Key Features

### Multi-Agent Research Orchestration
- **5-Phase Workflow**: Intelligent research planning → Initial data collection → Refinement analysis → Deep-dive research → Comprehensive synthesis
- **Adaptive Research**: Automatically identifies gaps and opportunities for deeper investigation
- **Budget-Aware**: Smart allocation of research budget across multiple agents
- **Token Management**: Automatic context window management for o3-mini (200k tokens)

### Advanced Capabilities
- **Distributed Agent Network**: Access to specialized Masumi Network agents:
  - `advanced-web-research`: Deep web search and analysis
  - `audience-insights-gwi`: Global Web Index data insights
  - `ask-the-crowd`: Crowd-sourced opinions and surveys
- **Evidence-Based Profiles**: Every insight includes source citations
- **Smart Truncation**: Intelligent content management when approaching token limits
- **Real-Time Progress Tracking**: Detailed UI feedback through Kodosumi tracer
- **Persistent Storage**: All research results saved for debugging and analysis

### Budget Management
- **USDM Currency**: All transactions in USD-pegged stablecoin
- **Per-Agent Limits**: Configurable spending caps per agent
- **Dynamic Pricing**: Adjusts to actual vs expected costs
- **Cost Transparency**: Real-time budget tracking and reporting

## 🚀 Quick Start

### Prerequisites
- Python 3.12+ (pyenv recommended)
- kodosumi v0.9.3
- Ray cluster
- Masumi Network API credentials

### Installation

1. **Set up Python environment**:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -e .
   ```

2. **Create environment file**:
   ```bash
   # Copy example and add your credentials
   cp .env.example .env
   # Edit .env to add your Masumi API credentials:
   # PAYMENT_SERVICE_URL=your_masumi_payment_url
   # PAYMENT_API_KEY=your_masumi_api_key
   ```

3. **Configure kodosumi deployment**:
   ```bash
   # Copy and configure deployment settings
   cp data/config/extended_audience_profiles.yaml.example data/config/extended_audience_profiles.yaml
   # Edit data/config/extended_audience_profiles.yaml to add:
   # - Your OpenAI API key
   # - Your Masumi registry and payment service credentials
   ```

4. **Configure Masumi settings** (optional - defaults are usually fine):
   ```bash
   # The config/masumi.yaml can be used as-is for most cases
   # Only edit if you need custom budget limits or agent settings
   ```

5. **Start the service**:
   ```bash
   just start  # Starts Ray, deploys service, launches UI
   ```

6. **Access the admin panel**:
   Open `http://localhost:3370` in your browser

## 📊 How It Works

### 5-Phase Research Workflow

1. **Phase 1: Orchestration**
   - Analyzes audience description
   - Plans 2-3 initial research angles
   - Submits jobs to specialized agents
   - Budget-aware job distribution

2. **Phase 2: Initial Data Collection**
   - Polls Masumi Network for results
   - Saves results with metadata
   - Tracks spending and performance

3. **Phase 3: Refinement Analysis**
   - Reviews initial findings
   - Identifies gaps and opportunities
   - Plans targeted follow-up research
   - Can use ALL remaining budget

4. **Phase 4: Deep-Dive Research**
   - Executes second round of research
   - Focuses on specific insights
   - Gathers detailed information

5. **Phase 5: Synthesis**
   - Combines all research (both rounds)
   - Creates comprehensive profile
   - Ensures source attribution
   - Manages token limits

### Example Research Flow

**Input**: "Millennials interested in sustainable fashion"

**First Round**:
- General demographics and values
- Broad fashion preferences
- Sustainability attitudes

**Refinement Analysis**:
- "Need deeper info on specific brands"
- "Clarify price sensitivity patterns"
- "Explore social media influence"

**Second Round**:
- Brand loyalty analysis
- Price point research
- Instagram/TikTok behavior study

**Output**: 2000+ word comprehensive profile with citations

## 💰 Budget Configuration

Edit `config/masumi.yaml`:

```yaml
masumi:
  budget:
    total: 20.0  # Total USDM budget
    per_request_max: 20.0
    per_agent_max:
      advanced-web-research: 10.0
      audience-insights-gwi: 10.0
      ask-the-crowd: 5.0
```

## 🔧 Development

### Commands

- `just start` - Start full service stack
- `just stop` - Stop all services
- `just test` - Run unit tests
- `just test-integration` - Run integration tests
- `just test-all` - Run complete test suite

### Architecture

```
extended-audience-profiles/
├── extended_audience_profiles/
│   ├── agent.py              # Core 5-phase orchestration logic
│   ├── query.py              # Kodosumi service wrapper
│   ├── masumi.py             # Masumi Network client
│   ├── tools.py              # OpenAI function tools
│   ├── state.py              # Simplified Ray-based state management
│   ├── background.py         # Async polling logic
│   ├── storage.py            # Result persistence
│   ├── context_management.py # Token counting and truncation
│   ├── formatting.py         # Result formatting utilities
│   ├── errors.py             # Error handling utilities
│   └── prompts/              # Agent prompt templates
│       ├── orchestrator.txt
│       ├── refinement.txt
│       └── consolidator.txt
├── config/
│   └── masumi.yaml           # Masumi Network configuration
├── data/
│   ├── config/               # Kodosumi deployment configs
│   └── results/              # Stored research results
└── tests/                    # Comprehensive test suite
```

### Key Components

- **Orchestrator Agent** (GPT-4): Plans initial research strategy
- **Refinement Agent** (o3-mini): Analyzes gaps, plans deep dives  
- **Consolidator Agent** (o3-mini): Synthesizes all findings
- **State Manager**: Simplified unified state for jobs and budget tracking
- **Context Manager**: Unified token counting and smart truncation
- **Storage System**: Filesystem-based result persistence
- **Externalized Prompts**: Agent prompts in separate template files

## 📈 Monitoring

### Kodosumi UI Feedback
- Real-time budget tracking
- Token usage analysis
- Task submission confirmations
- Error handling with context
- Progress indicators for all phases

### Ray Dashboard
Access at `http://localhost:8265` for:
- Task execution metrics
- Resource utilization
- Error logs and debugging

## 🔍 Advanced Usage

### Token Window Management
The system automatically:
- Counts tokens before consolidation
- Applies smart truncation if needed
- Preserves citations and key findings
- Warns about truncation in UI

### Storage Structure
```
data/results/jobs/{job_id}/
├── metadata.json                    # Job-level metadata
├── {agent_name}/
│   ├── {masumi_job_id}.md          # Agent result
│   └── metadata.json               # Agent metadata with rounds
└── consolidated/
    ├── profile.md                  # Final synthesized profile
    └── summary.json               # Summary with token info
```

### API Usage

```python
from extended_audience_profiles import generate_audience_profile

result = await generate_audience_profile(
    "Gen Z attitudes toward insurance products"
)

print(result['profile'])  # Comprehensive profile
print(result['budget_summary'])  # Spending breakdown
print(result['metadata']['first_round_tasks'])  # Initial research
print(result['metadata']['second_round_tasks'])  # Deep dives
```

## 🛡️ Security & Best Practices

- Never commit API keys (use `.env`)
- Budget limits prevent runaway spending
- Token limits prevent context overflow
- All agent calls are logged and tracked
- Results stored locally for audit trail

## 📝 License

This project follows the license terms of the kodosumi framework and Masumi Network usage agreements.