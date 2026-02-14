"""Claude Code CLI adapter implementation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime
from pathlib import Path

from herd_core.types import AgentRecord, AgentState, SpawnContext, SpawnResult


class ClaudeAgentAdapter:
    """AgentAdapter implementation for Claude Code CLI.

    Spawns agents as Claude CLI subprocesses in isolated git worktrees.
    Each instance runs in background with full Herd governance context.
    """

    def __init__(
        self,
        repo_root: str,
        worktree_root: str = "/private/tmp",
        branch_prefix: str = "herd",
    ) -> None:
        """Initialize the adapter.

        Args:
            repo_root: Path to the main repository.
            worktree_root: Directory for creating worktrees (default: /private/tmp).
            branch_prefix: Git branch prefix (default: herd).
        """
        self.repo_root = Path(repo_root)
        self.worktree_root = Path(worktree_root)
        self.branch_prefix = branch_prefix
        self._instances: dict[str, AgentRecord] = {}

    def spawn(
        self,
        role: str,
        ticket_id: str,
        context: SpawnContext,
        *,
        model: str | None = None,
    ) -> SpawnResult:
        """Spawn an agent instance with full context.

        Args:
            role: Agent role code (e.g., "grunt", "pikasso").
            ticket_id: Ticket identifier for this assignment.
            context: Complete context envelope (role, craft, guidelines, assignment).
            model: Optional model override.

        Returns:
            SpawnResult with instance_id, worktree path, and branch name.

        Raises:
            RuntimeError: If worktree creation or subprocess spawn fails.
        """
        # Generate instance ID
        instance_id = str(uuid.uuid4())

        # Create worktree
        worktree_path = self.worktree_root / f"{role}-{ticket_id.lower()}"
        branch_name = f"{self.branch_prefix}/{role}/{ticket_id.lower()}-agent-spawn"

        try:
            subprocess.run(
                ["git", "worktree", "add", str(worktree_path), "-b", branch_name],
                cwd=str(self.repo_root),
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"Failed to create worktree at {worktree_path}: {e.stderr}"
            ) from e

        # Assemble full context prompt
        context_prompt = self._assemble_context_prompt(
            role, ticket_id, branch_name, context, worktree_path
        )

        # Start Claude CLI subprocess in background
        try:
            # Write context to temp file to avoid shell escaping issues
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as f:
                f.write(context_prompt)
                temp_prompt_file = f.name

            # Start claude process (will run until completion or stopped)
            process = subprocess.Popen(
                [
                    "claude",
                    "-p",
                    f"@{temp_prompt_file}",
                    "--verbose",
                    "--output-format",
                    "stream-json",
                ],
                cwd=str(worktree_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "HERD_AGENT_NAME": role},
            )

            # Clean up temp file
            os.unlink(temp_prompt_file)

        except Exception as e:
            # Clean up worktree on spawn failure
            subprocess.run(
                ["git", "worktree", "remove", str(worktree_path), "--force"],
                cwd=str(self.repo_root),
                capture_output=True,
            )
            raise RuntimeError(f"Failed to spawn Claude process: {e}") from e

        # Track instance
        now = datetime.now()
        agent_record = AgentRecord(
            id=instance_id,
            agent=role,
            model=model or "claude-sonnet-4",
            ticket_id=ticket_id,
            state=AgentState.RUNNING,
            worktree=str(worktree_path),
            branch=branch_name,
            spawned_by=None,
            started_at=now,
            created_at=now,
        )
        self._instances[instance_id] = agent_record

        # Store process handle in a private attribute (not in AgentRecord)
        agent_record._process = process  # type: ignore

        return SpawnResult(
            instance_id=instance_id,
            agent=role,
            ticket_id=ticket_id,
            model=agent_record.model,
            worktree=str(worktree_path),
            branch=branch_name,
            spawned_at=now,
        )

    def get_status(self, instance_id: str) -> AgentRecord:
        """Get current state of an agent instance.

        Args:
            instance_id: Instance identifier from spawn().

        Returns:
            AgentRecord with current state.

        Raises:
            KeyError: If instance_id is not found.
        """
        if instance_id not in self._instances:
            raise KeyError(f"Instance {instance_id} not found")

        record = self._instances[instance_id]

        # Check process status if available
        if hasattr(record, "_process"):
            process = record._process  # type: ignore
            poll_result = process.poll()

            if poll_result is not None:
                # Process has ended
                if poll_result == 0:
                    record.state = AgentState.COMPLETED
                else:
                    record.state = AgentState.FAILED
                record.ended_at = datetime.now()

        return record

    def stop(self, instance_id: str) -> None:
        """Stop a running agent instance.

        Args:
            instance_id: Instance identifier from spawn().

        Raises:
            KeyError: If instance_id is not found.
        """
        if instance_id not in self._instances:
            raise KeyError(f"Instance {instance_id} not found")

        record = self._instances[instance_id]

        # Terminate process
        if hasattr(record, "_process"):
            process = record._process  # type: ignore
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()

        # Clean up worktree
        if record.worktree:
            subprocess.run(
                ["git", "worktree", "remove", record.worktree, "--force"],
                cwd=str(self.repo_root),
                capture_output=True,
            )

        # Update state
        record.state = AgentState.STOPPED
        record.ended_at = datetime.now()

    def _assemble_context_prompt(
        self,
        role: str,
        ticket_id: str,
        branch_name: str,
        context: SpawnContext,
        worktree_path: Path,
    ) -> str:
        """Assemble the full context prompt for the agent.

        Args:
            role: Agent role code.
            ticket_id: Ticket identifier.
            branch_name: Git branch name.
            context: SpawnContext with all governance docs.
            worktree_path: Path to worktree.

        Returns:
            Full context prompt string.
        """
        # Build environment variables block
        env_block = "\n".join(
            f"export {key}={value}" for key, value in context.environment.items()
        )

        # Build skills block if any
        skills_block = ""
        if context.skills:
            skills_block = "\n## SKILLS\n" + "\n".join(
                f"- {skill}" for skill in context.skills
            )

        return f"""You are {role.title()}, spawned to work on {ticket_id}.

## YOUR IDENTITY
{context.role_definition}

## CRITICAL GIT RULES
- NEVER push to main. NEVER run `git push origin main`.
- ALL work goes on your feature branch. Push ONLY your branch: `git push -u origin {branch_name}`
- Create a PR from your branch. The Architect merges. You NEVER merge or push to main.
- NEVER merge PRs. You do NOT have merge authority. Submitting the PR is the end of your responsibility.

## ENVIRONMENT
{env_block}

## WORKING DIRECTORY
You are working in: {worktree_path}
Branch: {branch_name}

## ASSIGNMENT: {ticket_id}
{context.assignment}

## CRAFT STANDARDS
{context.craft_standards}

## PROJECT GUIDELINES
{context.project_guidelines}
{skills_block}

START WORKING NOW.
"""
