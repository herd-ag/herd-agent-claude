"""Tests for ClaudeAgentAdapter."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from herd_agent_claude import ClaudeAgentAdapter
from herd_core.adapters.agent import AgentAdapter
from herd_core.types import AgentState, SpawnContext


@pytest.fixture
def adapter() -> ClaudeAgentAdapter:
    """Create a test adapter instance."""
    return ClaudeAgentAdapter(
        repo_root="/test/repo",
        worktree_root="/private/tmp",
        branch_prefix="herd",
    )


@pytest.fixture
def spawn_context() -> SpawnContext:
    """Create a test SpawnContext."""
    return SpawnContext(
        role_definition="You are Grunt, the backend developer.",
        craft_standards="Follow PEP 8. Write tests.",
        project_guidelines="Use Python 3.10+.",
        assignment="Implement feature X for DBC-123.",
        environment={"HERD_SLACK_TOKEN": "xoxb-test"},
        skills=["python", "testing"],
    )


def test_adapter_is_protocol_instance(adapter: ClaudeAgentAdapter) -> None:
    """Test that adapter implements AgentAdapter protocol."""
    assert isinstance(adapter, AgentAdapter)


def test_adapter_initialization() -> None:
    """Test adapter initialization with default and custom params."""
    adapter = ClaudeAgentAdapter(repo_root="/test/repo")
    assert adapter.repo_root == Path("/test/repo")
    assert adapter.worktree_root == Path("/private/tmp")
    assert adapter.branch_prefix == "herd"

    adapter = ClaudeAgentAdapter(
        repo_root="/custom/repo",
        worktree_root="/custom/tmp",
        branch_prefix="custom",
    )
    assert adapter.repo_root == Path("/custom/repo")
    assert adapter.worktree_root == Path("/custom/tmp")
    assert adapter.branch_prefix == "custom"


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_spawn_creates_worktree_and_starts_process(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test spawn creates worktree and starts Claude process."""
    # Mock subprocess calls
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context, model="claude-opus-4")

    # Verify worktree creation
    mock_run.assert_called_once()
    worktree_call = mock_run.call_args
    assert worktree_call[0][0] == [
        "git",
        "worktree",
        "add",
        "/private/tmp/grunt-dbc-123",
        "-b",
        "herd/grunt/dbc-123-agent-spawn",
    ]
    assert worktree_call[1]["cwd"] == "/test/repo"

    # Verify Claude process spawn
    mock_popen.assert_called_once()
    popen_call = mock_popen.call_args
    assert popen_call[0][0][0] == "claude"
    assert popen_call[0][0][1] == "-p"
    assert popen_call[0][0][2].startswith("@")  # Temp file path
    assert popen_call[1]["cwd"] == "/private/tmp/grunt-dbc-123"
    assert popen_call[1]["env"]["HERD_AGENT_NAME"] == "grunt"

    # Verify temp file was deleted
    mock_unlink.assert_called_once()

    # Verify SpawnResult
    assert result.agent == "grunt"
    assert result.ticket_id == "DBC-123"
    assert result.model == "claude-opus-4"
    assert result.worktree == "/private/tmp/grunt-dbc-123"
    assert result.branch == "herd/grunt/dbc-123-agent-spawn"
    assert isinstance(result.instance_id, str)
    assert isinstance(result.spawned_at, datetime)


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_spawn_uses_default_model(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test spawn uses default model when not specified."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_popen.return_value = MagicMock()

    result = adapter.spawn("grunt", "DBC-123", spawn_context)
    assert result.model == "claude-sonnet-4"


@patch("herd_agent_claude.adapter.subprocess.run")
def test_spawn_failure_cleans_up_worktree(
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test spawn cleans up worktree on failure."""
    # First call (worktree add) succeeds, second would be cleanup
    mock_run.side_effect = [
        MagicMock(returncode=0),  # worktree add
        MagicMock(returncode=0),  # worktree remove (cleanup)
    ]

    # Popen will fail
    with patch("herd_agent_claude.adapter.subprocess.Popen", side_effect=OSError("fail")):
        with pytest.raises(RuntimeError, match="Failed to spawn Claude process"):
            adapter.spawn("grunt", "DBC-123", spawn_context)

    # Verify cleanup was called
    assert mock_run.call_count == 2
    cleanup_call = mock_run.call_args_list[1]
    assert cleanup_call[0][0] == [
        "git",
        "worktree",
        "remove",
        "/private/tmp/grunt-dbc-123",
        "--force",
    ]


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_get_status_returns_running_agent(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test get_status returns current agent state."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_process.poll.return_value = None  # Still running
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context)
    status = adapter.get_status(result.instance_id)

    assert status.id == result.instance_id
    assert status.agent == "grunt"
    assert status.ticket_id == "DBC-123"
    assert status.state == AgentState.RUNNING
    assert status.worktree == "/private/tmp/grunt-dbc-123"
    assert status.branch == "herd/grunt/dbc-123-agent-spawn"


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_get_status_detects_completed(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test get_status detects completed process."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_process.poll.return_value = 0  # Completed successfully
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context)
    status = adapter.get_status(result.instance_id)

    assert status.state == AgentState.COMPLETED
    assert status.ended_at is not None


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_get_status_detects_failed(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test get_status detects failed process."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_process.poll.return_value = 1  # Failed
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context)
    status = adapter.get_status(result.instance_id)

    assert status.state == AgentState.FAILED
    assert status.ended_at is not None


def test_get_status_unknown_instance(adapter: ClaudeAgentAdapter) -> None:
    """Test get_status raises KeyError for unknown instance."""
    with pytest.raises(KeyError, match="Instance .* not found"):
        adapter.get_status("unknown-instance-id")


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_stop_terminates_process_and_cleans_worktree(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test stop terminates process and removes worktree."""
    # Mock spawn
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_process.poll.return_value = None  # Running
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context)

    # Mock run will be called again for worktree removal
    mock_run.reset_mock()

    # Stop the instance
    adapter.stop(result.instance_id)

    # Verify process termination
    mock_process.terminate.assert_called_once()
    mock_process.wait.assert_called()

    # Verify worktree removal
    mock_run.assert_called_once()
    cleanup_call = mock_run.call_args
    assert cleanup_call[0][0] == [
        "git",
        "worktree",
        "remove",
        "/private/tmp/grunt-dbc-123",
        "--force",
    ]

    # Verify status update
    status = adapter.get_status(result.instance_id)
    assert status.state == AgentState.STOPPED
    assert status.ended_at is not None


@patch("herd_agent_claude.adapter.subprocess.run")
@patch("herd_agent_claude.adapter.subprocess.Popen")
@patch("herd_agent_claude.adapter.os.unlink")
def test_stop_kills_if_terminate_times_out(
    mock_unlink: Mock,
    mock_popen: Mock,
    mock_run: Mock,
    adapter: ClaudeAgentAdapter,
    spawn_context: SpawnContext,
) -> None:
    """Test stop kills process if terminate times out."""
    mock_run.return_value = MagicMock(returncode=0)
    mock_process = MagicMock()
    mock_process.poll.return_value = None
    mock_process.wait.side_effect = [
        subprocess.TimeoutExpired("cmd", 5.0),  # First wait times out
        None,  # Second wait after kill succeeds
    ]
    mock_popen.return_value = mock_process

    result = adapter.spawn("grunt", "DBC-123", spawn_context)
    mock_run.reset_mock()

    adapter.stop(result.instance_id)

    # Verify kill was called
    mock_process.terminate.assert_called_once()
    mock_process.kill.assert_called_once()
    assert mock_process.wait.call_count == 2


def test_stop_unknown_instance(adapter: ClaudeAgentAdapter) -> None:
    """Test stop raises KeyError for unknown instance."""
    with pytest.raises(KeyError, match="Instance .* not found"):
        adapter.stop("unknown-instance-id")


def test_context_prompt_assembly(
    adapter: ClaudeAgentAdapter, spawn_context: SpawnContext
) -> None:
    """Test context prompt includes all required sections."""
    prompt = adapter._assemble_context_prompt(
        role="grunt",
        ticket_id="DBC-123",
        branch_name="herd/grunt/dbc-123-test",
        context=spawn_context,
        worktree_path=Path("/tmp/test"),
    )

    # Verify all sections are present
    assert "You are Grunt, spawned to work on DBC-123" in prompt
    assert "## YOUR IDENTITY" in prompt
    assert "You are Grunt, the backend developer." in prompt
    assert "## CRITICAL GIT RULES" in prompt
    assert "NEVER push to main" in prompt
    assert "herd/grunt/dbc-123-test" in prompt
    assert "## ENVIRONMENT" in prompt
    assert "export HERD_SLACK_TOKEN=xoxb-test" in prompt
    assert "## WORKING DIRECTORY" in prompt
    assert "/tmp/test" in prompt
    assert "## ASSIGNMENT: DBC-123" in prompt
    assert "Implement feature X for DBC-123." in prompt
    assert "## CRAFT STANDARDS" in prompt
    assert "Follow PEP 8. Write tests." in prompt
    assert "## PROJECT GUIDELINES" in prompt
    assert "Use Python 3.10+." in prompt
    assert "## SKILLS" in prompt
    assert "- python" in prompt
    assert "- testing" in prompt
    assert "START WORKING NOW." in prompt


def test_context_prompt_without_skills(adapter: ClaudeAgentAdapter) -> None:
    """Test context prompt works without skills."""
    context = SpawnContext(
        role_definition="Role",
        craft_standards="Craft",
        project_guidelines="Guidelines",
        assignment="Assignment",
        environment={},
        skills=[],
    )

    prompt = adapter._assemble_context_prompt(
        role="grunt",
        ticket_id="DBC-123",
        branch_name="test",
        context=context,
        worktree_path=Path("/tmp"),
    )

    assert "## SKILLS" not in prompt
