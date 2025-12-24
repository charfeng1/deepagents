"""Enhanced middleware that exposes shell tools with background execution support."""

from __future__ import annotations

import os
import subprocess
import threading
import uuid
from collections import defaultdict
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langchain_core.tools.base import ToolException


class ShellMiddleware(AgentMiddleware[AgentState, Any]):
    """Give shell access to agents with support for background execution.

    This shell will execute on the local machine and has NO safeguards except
    for the human in the loop safeguard provided by the CLI itself.

    Supports:
    - Blocking execution (default): Run command and wait for result
    - Background execution: Run command in background, read output later
    - Interactive input: Send input to running background processes
    """

    def __init__(
        self,
        *,
        workspace_root: str,
        timeout: float = 120.0,
        max_output_bytes: int = 100_000,
        env: dict[str, str] | None = None,
    ) -> None:
        """Initialize an instance of `ShellMiddleware`.

        Args:
            workspace_root: Working directory for shell commands.
            timeout: Maximum time in seconds to wait for blocking command completion.
                Defaults to 120 seconds.
            max_output_bytes: Maximum number of bytes to capture from command output.
                Defaults to 100,000 bytes.
            env: Environment variables to pass to the subprocess. If None,
                uses the current process's environment. Defaults to None.
        """
        super().__init__()
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._env = env if env is not None else os.environ.copy()
        self._workspace_root = workspace_root

        # Background process tracking
        self._processes: dict[str, subprocess.Popen] = {}
        self._output_buffers: dict[str, list[str]] = defaultdict(list)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

        # Build tools
        self.tools = [
            self._build_shell_tool(),
            self._build_shell_output_tool(),
            self._build_shell_input_tool(),
            self._build_kill_shell_tool(),
        ]

    def _build_shell_tool(self):
        """Build the main shell tool."""
        description = (
            f"Execute a shell command. Commands run in: {self._workspace_root}\n"
            f"Set run_in_background=True for long-running commands (servers, watchers, etc). "
            f"Background commands return a shell_id to check output later with shell_output."
        )

        @tool("shell", description=description)
        def shell_tool(
            command: str,
            runtime: ToolRuntime[None, AgentState],
            run_in_background: bool = False,
        ) -> ToolMessage | str:
            """Execute a shell command.

            Args:
                command: The shell command to execute.
                runtime: The tool runtime context.
                run_in_background: If True, run in background and return shell_id.
            """
            if not command or not isinstance(command, str):
                raise ToolException("Shell tool expects a non-empty command string.")

            if run_in_background:
                return self._run_background(command)
            else:
                return self._run_blocking(command, tool_call_id=runtime.tool_call_id)

        return shell_tool

    def _build_shell_output_tool(self):
        """Build the shell_output tool."""
        @tool("shell_output", description="Get output from a background shell process.")
        def shell_output_tool(
            shell_id: str,
            runtime: ToolRuntime[None, AgentState],
        ) -> str:
            """Get output from a background shell.

            Args:
                shell_id: The ID of the background shell.
                runtime: The tool runtime context.
            """
            return self._get_output(shell_id)

        return shell_output_tool

    def _build_shell_input_tool(self):
        """Build the shell_input tool."""
        @tool("shell_input", description="Send input to a background shell process (writes to stdin).")
        def shell_input_tool(
            shell_id: str,
            input_text: str,
            runtime: ToolRuntime[None, AgentState],
        ) -> str:
            """Send input to a background shell.

            Args:
                shell_id: The ID of the background shell.
                input_text: Text to send to the process stdin.
                runtime: The tool runtime context.
            """
            return self._send_input(shell_id, input_text)

        return shell_input_tool

    def _build_kill_shell_tool(self):
        """Build the kill_shell tool."""
        @tool("kill_shell", description="Terminate a background shell process.")
        def kill_shell_tool(
            shell_id: str,
            runtime: ToolRuntime[None, AgentState],
        ) -> str:
            """Kill a background shell.

            Args:
                shell_id: The ID of the background shell to kill.
                runtime: The tool runtime context.
            """
            return self._kill_process(shell_id)

        return kill_shell_tool

    def _run_blocking(
        self,
        command: str,
        *,
        tool_call_id: str | None,
    ) -> ToolMessage:
        """Execute a shell command synchronously and return the result."""
        try:
            result = subprocess.run(
                command,
                check=False,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=self._env,
                cwd=self._workspace_root,
            )

            # Combine stdout and stderr
            output_parts = []
            if result.stdout:
                output_parts.append(result.stdout)
            if result.stderr:
                stderr_lines = result.stderr.strip().split("\n")
                for line in stderr_lines:
                    output_parts.append(f"[stderr] {line}")

            output = "\n".join(output_parts) if output_parts else "<no output>"

            # Truncate output if needed
            if len(output) > self._max_output_bytes:
                output = output[: self._max_output_bytes]
                output += f"\n\n... Output truncated at {self._max_output_bytes} bytes."

            # Add exit code info if non-zero
            if result.returncode != 0:
                output = f"{output.rstrip()}\n\nExit code: {result.returncode}"
                status = "error"
            else:
                status = "success"

        except subprocess.TimeoutExpired:
            output = f"Error: Command timed out after {self._timeout:.1f} seconds."
            status = "error"

        return ToolMessage(
            content=output,
            tool_call_id=tool_call_id,
            name="shell",
            status=status,
        )

    def _run_background(self, command: str) -> str:
        """Start a background process with stdin pipe support."""
        shell_id = str(uuid.uuid4())[:8]

        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self._workspace_root,
                env=self._env,
                bufsize=1,  # Line buffered
            )
        except Exception as e:
            return f"Error starting process: {e}"

        self._processes[shell_id] = process

        # Start output reader thread
        def reader():
            try:
                for line in iter(process.stdout.readline, ''):
                    if not line:
                        break
                    with self._locks[shell_id]:
                        self._output_buffers[shell_id].append(line)
            except (ValueError, OSError):
                # Process closed
                pass
            finally:
                try:
                    process.stdout.close()
                except Exception:
                    pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        return (
            f"Background process started with shell_id: {shell_id}\n"
            f"Command: {command}\n"
            f"Use shell_output('{shell_id}') to read output.\n"
            f"Use shell_input('{shell_id}', 'text') to send input.\n"
            f"Use kill_shell('{shell_id}') to terminate."
        )

    def _get_output(self, shell_id: str) -> str:
        """Get buffered output from a background process."""
        if shell_id not in self._processes:
            return f"Error: No process with shell_id '{shell_id}'"

        process = self._processes[shell_id]

        # Get buffered output
        with self._locks[shell_id]:
            output = "".join(self._output_buffers[shell_id])
            self._output_buffers[shell_id] = []  # Clear after reading

        # Check process status
        exit_code = process.poll()
        if exit_code is None:
            status = "running"
        else:
            status = f"exited (code: {exit_code})"
            # Clean up finished process
            self._cleanup(shell_id)

        # Truncate if needed
        if len(output) > self._max_output_bytes:
            output = output[: self._max_output_bytes]
            output += f"\n... truncated at {self._max_output_bytes} bytes"

        return f"[{status}]\n{output}" if output else f"[{status}]\n<no new output>"

    def _send_input(self, shell_id: str, input_text: str) -> str:
        """Send input to a background process."""
        if shell_id not in self._processes:
            return f"Error: No process with shell_id '{shell_id}'"

        process = self._processes[shell_id]

        # Check if process is still running
        if process.poll() is not None:
            return f"Error: Process {shell_id} has already exited"

        # Check if stdin is available
        if process.stdin is None:
            return f"Error: Process {shell_id} stdin not available"

        try:
            process.stdin.write(input_text)
            process.stdin.flush()
            return f"Sent to {shell_id}: {repr(input_text)}"
        except (OSError, BrokenPipeError) as e:
            return f"Error sending input: {e}"

    def _kill_process(self, shell_id: str) -> str:
        """Kill a background process."""
        if shell_id not in self._processes:
            return f"Error: No process with shell_id '{shell_id}'"

        process = self._processes[shell_id]

        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        except Exception as e:
            return f"Error killing process: {e}"

        self._cleanup(shell_id)
        return f"Killed process {shell_id}"

    def _cleanup(self, shell_id: str) -> None:
        """Clean up a finished process."""
        if shell_id in self._processes:
            del self._processes[shell_id]
        if shell_id in self._output_buffers:
            del self._output_buffers[shell_id]
        if shell_id in self._locks:
            del self._locks[shell_id]

    def cleanup_all(self) -> None:
        """Kill all background processes. Call on shutdown."""
        for shell_id in list(self._processes.keys()):
            try:
                self._kill_process(shell_id)
            except Exception:
                pass

    def list_shells(self) -> list[str]:
        """List all active shell IDs."""
        return list(self._processes.keys())


__all__ = ["ShellMiddleware"]
