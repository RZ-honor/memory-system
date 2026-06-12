# Memory System for Claude Code

A persistent memory system for Claude Code that provides intelligent context injection and knowledge management across sessions.

## Features

- **Three-Factor Retrieval**: Combines recency, importance, and relevance for intelligent memory search
- **FSRS Decay Model**: Implements spaced repetition scheduling for memory retention
- **Knowledge Fusion**: Automatic contradiction detection and memory merging
- **Reasoning Chain Extraction**: Captures and indexes problem-solving patterns
- **MCP Server Integration**: Native integration with Claude Code via Model Context Protocol
- **Web UI**: Visual interface for memory management and exploration

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Claude Code                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   MCP Server │  │   Hooks     │  │   Web UI    │        │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘        │
│         │                │                │                 │
│  ┌──────▼────────────────▼────────────────▼──────┐        │
│  │              Core Library (lib/)               │        │
│  │  ┌─────────┐ ┌─────────┐ ┌─────────┐        │        │
│  │  │retriever│ │ observer│ │  fusion │        │        │
│  │  └────┬────┘ └────┬────┘ └────┬────┘        │        │
│  │       │           │           │               │        │
│  │  ┌────▼───────────▼───────────▼────┐        │        │
│  │  │         SQLite + FTS5           │        │        │
│  │  │    (Vector Embeddings)          │        │        │
│  │  └─────────────────────────────────┘        │        │
│  └──────────────────────────────────────────────┘        │
└─────────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Python 3.9+
- Claude Code CLI

### Setup

1. **Clone the repository**:
   ```bash
   git clone https://github.com/YOUR_USERNAME/memory-system.git
   cd memory-system
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure the system**:
   ```bash
   cp config.example.json config.json
   cp .env.example .env
   ```

   Edit `config.json` and `.env` with your API credentials:
   - `llm.base_url`: Your LLM API endpoint
   - `llm.api_key`: Your API key
   - `embedding.model_name`: Path to embedding model (default: `all-MiniLM-L6-v2`)

4. **Initialize the database**:
   ```bash
   python main.py init
   ```

5. **Start the MCP server** (for Claude Code integration):
   ```bash
   python mcp_server.py
   ```

## Claude Code Integration

### MCP Server Setup

Add the MCP server to your Claude Code configuration:

```json
// ~/.claude/.mcp.json
{
  "mcpServers": {
    "memory-system": {
      "command": "python",
      "args": ["path/to/memory-system/mcp_server.py"]
    }
  }
}
```

### Available Tools

The MCP server provides these tools:

| Tool | Description |
|------|-------------|
| `search_memory` | Search memories using three-factor retrieval |
| `save_memory` | Save new memories with metadata |
| `inject_context` | Get relevant context for current conversation |
| `list_modules` | List memory modules (topic clusters) |
| `get_stats` | Get memory system statistics |
| `search_reasoning` | Search reasoning chains |
| `run_maintenance` | Run fusion and pruning cycles |
| `extract_from_session` | Extract memories from session interactions |

## Configuration

### Embedding Model

The system uses `all-MiniLM-L6-v2` by default (384 dimensions, ~80MB). You can configure a different model:

```json
{
  "embedding": {
    "enabled": true,
    "model_name": "path/to/your/model",
    "dimension": 384
  }
}
```

### Memory Fusion

Automatic memory fusion runs every 5 minutes (configurable):

```json
{
  "fusion": {
    "enabled": true,
    "interval_seconds": 300,
    "similarity_threshold": 0.75,
    "contradiction_window_days": 7
  }
}
```

### Memory Pruning

FSRS-based pruning runs every 6 hours:

```json
{
  "pruning": {
    "enabled": true,
    "interval_hours": 6,
    "expire_days": 30,
    "idle_threshold_seconds": 300
  }
}
```

## Web Interface

Access the web UI at `http://127.0.0.1:38800` after starting the server:

```bash
python main.py serve
```

Features:
- Memory browsing and search
- Session history
- Knowledge graph visualization
- Manual memory management
- System statistics

## API Reference

### REST API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/memories` | GET | List memories |
| `/api/memories/search` | GET/POST | Search memories |
| `/api/memories` | POST | Create memory |
| `/api/sessions` | GET | List sessions |
| `/api/stats` | GET | Get statistics |
| `/api/knowledge` | GET | Get knowledge entries |
| `/api/modules` | GET | List modules |
| `/api/reasoning-chains` | GET | List reasoning chains |

## Development

### Project Structure

```
memory-system/
├── lib/                    # Core library
│   ├── db.py              # Database operations
│   ├── retriever.py       # Three-factor retrieval
│   ├── observer.py        # Memory extraction
│   ├── fusion.py          # Memory fusion
│   ├── pruner.py          # Memory pruning
│   ├── hook.py            # Claude Code hooks
│   ├── server.py          # Web server
│   └── llm.py             # LLM client
├── templates/             # Web UI templates
├── mcp_server.py          # MCP server
├── main.py                # CLI entry point
├── config.example.json    # Example configuration
└── requirements.txt       # Python dependencies
```

### Adding New Features

1. **New Memory Type**: Add to `observer.py` extraction prompts
2. **New Retrieval Method**: Extend `retriever.py` search functions
3. **New API Endpoint**: Add to `server.py` routes
4. **New MCP Tool**: Add to `mcp_server.py` tools

## License

MIT License - see LICENSE file for details

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## Acknowledgments

- Based on research from [Generative Agents](https://arxiv.org/abs/2304.03442)
- Uses [sentence-transformers](https://www.sbert.net/) for embeddings
- Implements [FSRS](https://github.com/open-spaced-repetition/fsrs4anki) decay model
