# herd-agent-claude

Herd execution adapter for [Claude Code CLI](https://github.com/anthropics/claude-code).

Implements the `AgentAdapter` protocol from [herd-core](https://github.com/dbt-conceptual/herd-core) to spawn, manage, and track Claude-powered agent instances in isolated git worktrees.

## Installation

```bash
pip install herd-agent-claude
```

## Usage

```python
from herd_agent_claude import ClaudeAgentAdapter
from herd_core.types import SpawnContext

adapter = ClaudeAgentAdapter(
    repo_root="/path/to/repo",
    worktree_root="/private/tmp",
    branch_prefix="herd"
)

context = SpawnContext(
    role_definition="...",
    craft_standards="...",
    project_guidelines="...",
    assignment="...",
)

result = adapter.spawn("grunt", "DBC-144", context)
print(f"Spawned {result.instance_id} at {result.worktree}")

# Check status
status = adapter.get_status(result.instance_id)
print(f"Agent state: {status.state}")

# Stop when done
adapter.stop(result.instance_id)
```

## License

MIT
