# Extended Audience Profiles

> 🎯 Generate comprehensive, evidence-based audience profiles through intelligent multi-agent orchestration

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Kodosumi 0.9.3](https://img.shields.io/badge/kodosumi-0.9.3-green.svg)](https://kodosumi.com)
[![Masumi Network](https://img.shields.io/badge/masumi-network-purple.svg)](https://masumi.network)

Extended Audience Profiles is an AI-powered service that creates deep, actionable audience insights by orchestrating multiple specialized research agents through the Masumi Network's Agent-to-Agent (A2A) Protocol. It transforms simple audience descriptions into 15-20k word comprehensive profiles with full source citations.

## 🌟 Key Features

### Intelligent Multi-Agent Orchestration
- **5-Phase Workflow**: Automated orchestration → initial research → gap analysis → deep-dive → synthesis
- **3 Specialized Agents**: 
  - **GWI**: Access to 250K+ consumer profiling points across 50+ markets
  - **Advanced Web Research**: Real-time internet search with source citations
  - **Ask-the-Crowd**: EU consumer validation through surveys and opinions
- **Smart Budget Management**: Optimized spending across phases (30-50% initial, 100% refinement)

### Advanced Capabilities
- **Dual-Layer Citations**: `[Original Source, Year][via: agent_name]` format
- **Dynamic Profile Generation**: Creative formats including personas, journey maps, and matrices
- **Token-Aware Processing**: Automatic content optimization for GPT-4.1's 1M+ context
- **Distributed State Management**: Ray Actor pattern for reliable job execution
- **Persistent Storage**: All research results saved with metadata

### Quality Assurance
- **Schema-Driven Validation**: Generalized input validation for all agents
- **Graceful Error Handling**: Comprehensive exception hierarchy
- **Budget Protection**: Real-time tracking with automatic limits
- **Smart Truncation**: Preserves most important content when needed

## 🚀 Quick Start

### Prerequisites
- Python 3.12+
- Masumi Network API credentials
- 40 USDM budget allocation

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/extended-audience-profiles.git
cd extended-audience-profiles

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Set up environment variables
cp .env.example .env
# Edit .env with your Masumi credentials
```

### Running the Service

```bash
# Start all services with one command
just start

# Access the admin panel
open http://localhost:3370

# Stop all services
just stop
```

## 📋 Example Usage

### Input
```
"Millennial parents in urban areas interested in sustainable baby furniture"
```

### Output Structure
```markdown
# Extended Audience Profile

## Executive Summary
[Compelling narrative about the audience...]

## Demographic Deep Dive
[Detailed demographics with data visualization descriptions...]

## Psychographic Analysis
[Values, attitudes, lifestyle patterns...]

## Behavioral Insights
[Purchase behaviors, decision-making processes...]

## Media Consumption Patterns
[Channel preferences, content consumption habits...]

## Market Opportunities
[Actionable recommendations based on research...]

[... continues for 15-20k words ...]

## Sources
[Comprehensive citation list with original sources]
```

## 🏗️ Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Kodosumi Service Layer                  │
│  (FastAPI + Ray Serve for distributed request handling)     │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                    Orchestration Engine                      │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │ Orchestrator│  │  Refinement  │  │  Consolidator   │   │
│  │   (GPT-4.1) │  │   (GPT-4.1)  │  │    (GPT-4.1)    │   │
│  └─────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                    Masumi Network Layer                      │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │     GWI     │  │Advanced Web  │  │ Ask-the-Crowd   │   │
│  │   Agent     │  │Research Agent│  │     Agent       │   │
│  └─────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────┐
│                    State & Storage Layer                     │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐   │
│  │Ray StateActor│ │Budget Tracker│  │File Storage     │   │
│  │(Jobs+Budget) │  │ (Real-time)  │  │(Results+Meta)   │   │
│  └─────────────┘  └──────────────┘  └─────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

### Workflow Phases

1. **Orchestration Phase** (30-50% budget)
   - Analyzes audience description
   - Plans initial research strategy
   - Submits 2-4 jobs to foundation agents

2. **Initial Collection Phase**
   - Background polling for results
   - Automatic retry on transient failures
   - Result storage with metadata

3. **Refinement Phase** (100% remaining budget)
   - Gap analysis on initial findings
   - Plans deep-dive research
   - Focuses on validation and specifics

4. **Deep-Dive Phase**
   - Executes refined research plan
   - Leverages ask-the-crowd for validation
   - Builds comprehensive dataset

5. **Synthesis Phase**
   - GPT-4.1 processes all results
   - Creates narrative-driven profile
   - Applies dual-layer citations

## 🛠️ Development

### Running Tests
```bash
# Run unit tests (fast, mocked)
just test

# Run integration tests (uses real APIs)
just test-integration

# Run all tests
just test-all
```

### Project Structure
```
extended_audience_profiles/
├── agent.py              # Core 5-phase orchestration logic
├── state.py              # Ray Actor for distributed state
├── masumi.py            # Masumi Network API client
├── tools.py             # OpenAI function calling tools
├── background.py        # Async job polling system
├── storage.py           # Persistent result storage
├── context_management.py # Token counting & truncation
├── exceptions.py        # Custom exception hierarchy
├── prompts/             # External agent instructions
│   ├── orchestrator.txt
│   ├── refinement.txt
│   └── consolidator.txt
└── query.py             # Kodosumi service entry point
```

### Configuration

Budget and agent settings in `config/masumi.yaml`:
```yaml
budget:
  total_budget: 40.0  # USDM
  network: "Preprod"

agents:
  - name: "audience-insights-gwi"
    price: 3.0
    max_total_spend: 9.0
  # ... more agents
```

## 📊 Performance & Reliability

- **Execution Time**: 10-25 minutes for complete profile
- **Success Rate**: 95%+ with retry mechanisms
- **Token Efficiency**: Automatic optimization for model limits
- **Cost Control**: Hard limits with real-time tracking
- **Error Recovery**: Graceful degradation with partial results

## 🤝 Contributing

We welcome contributions! Please see our [Contributing Guidelines](CONTRIBUTING.md) for details.

### Development Principles
- Build generalized solutions, not agent-specific code
- Maintain schema-driven validation
- Follow the 5-phase workflow pattern
- Keep prompts external and editable

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- Built on [Kodosumi](https://kodosumi.com) framework
- Powered by [Masumi Network](https://masumi.network) A2A Protocol
- Uses OpenAI's GPT-4.1 for synthesis

## 📞 Support

- **Documentation**: See [CLAUDE.md](CLAUDE.md) for AI-assisted development
- **Issues**: [GitHub Issues](https://github.com/yourusername/extended-audience-profiles/issues)
- **Discussions**: [GitHub Discussions](https://github.com/yourusername/extended-audience-profiles/discussions)

---

**Extended Audience Profiles** - Transform audience descriptions into actionable intelligence.