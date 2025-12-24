"""Basic tests for async shell functionality."""

import time
import sys
import os

# Add the package to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from deepagents_cli.shell import ShellMiddleware


def test_blocking_execution():
    """Test that blocking execution still works."""
    print("Testing blocking execution...")

    middleware = ShellMiddleware(workspace_root="/tmp")

    # Find the shell tool
    shell_tool = None
    for t in middleware.tools:
        if t.name == "shell":
            shell_tool = t
            break

    assert shell_tool is not None, "Shell tool not found"

    # Create a mock runtime
    class MockRuntime:
        tool_call_id = "test-123"
        state = {}

    # Run a simple command (blocking)
    result = shell_tool.func("echo 'hello world'", MockRuntime(), run_in_background=False)

    print(f"  Result: {result.content[:100]}")
    assert "hello world" in result.content
    print("  PASSED")


def test_background_execution():
    """Test background execution with output reading."""
    print("\nTesting background execution...")

    middleware = ShellMiddleware(workspace_root="/tmp")

    # Get tools
    tools = {t.name: t for t in middleware.tools}

    class MockRuntime:
        tool_call_id = "test-456"
        state = {}

    # Start a background process that outputs multiple lines
    result = tools["shell"].func(
        "for i in 1 2 3; do echo \"Line $i\"; sleep 0.2; done",
        MockRuntime(),
        run_in_background=True
    )
    print(f"  Start result: {result[:80]}...")

    # Extract shell_id
    shell_id = result.split("shell_id: ")[1].split("\n")[0]
    print(f"  Shell ID: {shell_id}")

    # Wait a bit for output
    time.sleep(1)

    # Read output
    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  Output: {output[:100]}...")
    assert "Line" in output

    # Wait for completion
    time.sleep(1)

    # Check final output
    final_output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  Final: {final_output[:100]}...")

    print("  PASSED")


def test_stdin_input():
    """Test sending input to a background process."""
    print("\nTesting stdin input...")

    middleware = ShellMiddleware(workspace_root="/tmp")
    tools = {t.name: t for t in middleware.tools}

    class MockRuntime:
        tool_call_id = "test-789"
        state = {}

    # Start a cat process (echoes stdin to stdout)
    result = tools["shell"].func("cat", MockRuntime(), run_in_background=True)
    shell_id = result.split("shell_id: ")[1].split("\n")[0]
    print(f"  Shell ID: {shell_id}")

    # Give it a moment to start
    time.sleep(0.2)

    # Send input
    send_result = tools["shell_input"].func(shell_id, "Hello from stdin!\n", MockRuntime())
    print(f"  Send result: {send_result}")

    # Wait for output
    time.sleep(0.3)

    # Read output
    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  Output: {output}")
    assert "Hello from stdin!" in output

    # Kill it
    kill_result = tools["kill_shell"].func(shell_id, MockRuntime())
    print(f"  Kill result: {kill_result}")

    print("  PASSED")


def test_python_repl():
    """Test interacting with Python REPL."""
    print("\nTesting Python REPL interaction...")

    middleware = ShellMiddleware(workspace_root="/tmp")
    tools = {t.name: t for t in middleware.tools}

    class MockRuntime:
        tool_call_id = "test-repl"
        state = {}

    # Start Python with unbuffered output
    result = tools["shell"].func(
        "python3 -u -i 2>&1",
        MockRuntime(),
        run_in_background=True
    )
    shell_id = result.split("shell_id: ")[1].split("\n")[0]
    print(f"  Shell ID: {shell_id}")

    # Wait for Python to start
    time.sleep(0.5)

    # Check initial output
    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  Initial output: {output[:100]}...")

    # Send a Python command
    tools["shell_input"].func(shell_id, "print('Hello from Python!')\n", MockRuntime())
    time.sleep(0.3)

    # Read response
    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  After print: {output}")

    # Send another command
    tools["shell_input"].func(shell_id, "2 + 2\n", MockRuntime())
    time.sleep(0.3)

    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  After 2+2: {output}")

    # Exit Python
    tools["shell_input"].func(shell_id, "exit()\n", MockRuntime())
    time.sleep(0.5)

    # Check it exited
    output = tools["shell_output"].func(shell_id, MockRuntime())
    print(f"  After exit: {output}")

    print("  PASSED")


def test_kill_shell():
    """Test killing a background process."""
    print("\nTesting kill_shell...")

    middleware = ShellMiddleware(workspace_root="/tmp")
    tools = {t.name: t for t in middleware.tools}

    class MockRuntime:
        tool_call_id = "test-kill"
        state = {}

    # Start a long-running process
    result = tools["shell"].func("sleep 60", MockRuntime(), run_in_background=True)
    shell_id = result.split("shell_id: ")[1].split("\n")[0]
    print(f"  Shell ID: {shell_id}")

    # Verify it's running
    output = tools["shell_output"].func(shell_id, MockRuntime())
    assert "running" in output
    print(f"  Status: running")

    # Kill it
    kill_result = tools["kill_shell"].func(shell_id, MockRuntime())
    print(f"  Kill result: {kill_result}")
    assert "Killed" in kill_result

    # Verify it's gone
    output = tools["shell_output"].func(shell_id, MockRuntime())
    assert "Error" in output  # No such process
    print(f"  After kill: {output}")

    print("  PASSED")


if __name__ == "__main__":
    print("=" * 60)
    print("Async Shell Tests")
    print("=" * 60)

    try:
        test_blocking_execution()
        test_background_execution()
        test_stdin_input()
        test_python_repl()
        test_kill_shell()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    except Exception as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
