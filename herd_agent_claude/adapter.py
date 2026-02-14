"""Claude Code CLI adapter implementation — placeholder for Step 1."""

from __future__ import annotations


class ClaudeAgentAdapter:
    """Placeholder adapter — to be implemented in Step 2."""

    def __init__(
        self,
        repo_root: str,
        worktree_root: str = "/private/tmp",
        branch_prefix: str = "herd",
    ) -> None:
        self.repo_root = repo_root
        self.worktree_root = worktree_root
        self.branch_prefix = branch_prefix
