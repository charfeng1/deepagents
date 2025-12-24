"""Standalone test for async shell functionality - no package dependencies."""

import subprocess
import threading
import time
import uuid
from collections import defaultdict


class SimpleAsyncShell:
    """Simplified async shell for testing core functionality."""

    def __init__(self, workspace_root: str = "/tmp"):
        self._workspace_root = workspace_root
        self._processes: dict[str, subprocess.Popen] = {}
        self._output_buffers: dict[str, list[str]] = defaultdict(list)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)

    def run_background(self, command: str) -> str:
        """Start a background process."""
        shell_id = str(uuid.uuid4())[:8]

        process = subprocess.Popen(
            command,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=self._workspace_root,
            bufsize=1,
        )

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
                pass
            finally:
                try:
                    process.stdout.close()
                except Exception:
                    pass

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()

        return shell_id

    def get_output(self, shell_id: str) -> tuple[str, str]:
        """Get output and status from a background process."""
        if shell_id not in self._processes:
            return "", "not_found"

        process = self._processes[shell_id]

        with self._locks[shell_id]:
            output = "".join(self._output_buffers[shell_id])
            self._output_buffers[shell_id] = []

        exit_code = process.poll()
        if exit_code is None:
            status = "running"
        else:
            status = f"exited:{exit_code}"

        return output, status

    def send_input(self, shell_id: str, text: str) -> bool:
        """Send input to a background process."""
        if shell_id not in self._processes:
            return False

        process = self._processes[shell_id]
        if process.poll() is not None:
            return False

        try:
            process.stdin.write(text)
            process.stdin.flush()
            return True
        except (OSError, BrokenPipeError):
            return False

    def kill(self, shell_id: str) -> bool:
        """Kill a background process."""
        if shell_id not in self._processes:
            return False

        process = self._processes[shell_id]
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)

        del self._processes[shell_id]
        return True


def test_background_execution():
    """Test basic background execution."""
    print("Test 1: Background execution")

    shell = SimpleAsyncShell()

    # Start a process that outputs lines
    shell_id = shell.run_background("for i in 1 2 3; do echo \"Line $i\"; sleep 0.1; done")
    print(f"  Started: {shell_id}")

    # Wait for it to run
    time.sleep(0.5)

    # Get output
    output, status = shell.get_output(shell_id)
    print(f"  Status: {status}")
    print(f"  Output: {repr(output[:50])}")

    assert "Line" in output, f"Expected 'Line' in output, got: {output}"
    print("  PASSED\n")


def test_stdin_with_cat():
    """Test stdin with cat command."""
    print("Test 2: Stdin with cat")

    shell = SimpleAsyncShell()

    # Start cat (echoes stdin)
    shell_id = shell.run_background("cat")
    print(f"  Started cat: {shell_id}")
    time.sleep(0.1)

    # Send input
    success = shell.send_input(shell_id, "Hello World!\n")
    print(f"  Sent input: {success}")
    assert success

    time.sleep(0.2)

    # Get output
    output, status = shell.get_output(shell_id)
    print(f"  Status: {status}")
    print(f"  Output: {repr(output)}")

    assert "Hello World!" in output, f"Expected echo, got: {output}"

    # Kill it
    shell.kill(shell_id)
    print("  PASSED\n")


def test_python_repl():
    """Test interactive Python REPL."""
    print("Test 3: Python REPL")

    shell = SimpleAsyncShell()

    # Start Python with unbuffered output
    shell_id = shell.run_background("python3 -u -i 2>&1")
    print(f"  Started Python: {shell_id}")
    time.sleep(0.3)

    # Check initial output (should have version info or prompt)
    output, status = shell.get_output(shell_id)
    print(f"  Initial: {repr(output[:60]) if output else '<empty>'}")

    # Send a print command
    shell.send_input(shell_id, "print('HELLO_TEST')\n")
    time.sleep(0.2)

    output, status = shell.get_output(shell_id)
    print(f"  After print: {repr(output)}")
    assert "HELLO_TEST" in output, f"Expected HELLO_TEST, got: {output}"

    # Send math
    shell.send_input(shell_id, "2 + 2\n")
    time.sleep(0.2)

    output, status = shell.get_output(shell_id)
    print(f"  After 2+2: {repr(output)}")
    assert "4" in output, f"Expected 4, got: {output}"

    # Exit
    shell.send_input(shell_id, "exit()\n")
    time.sleep(0.3)

    output, status = shell.get_output(shell_id)
    print(f"  Final status: {status}")

    print("  PASSED\n")


def test_kill():
    """Test killing a process."""
    print("Test 4: Kill process")

    shell = SimpleAsyncShell()

    shell_id = shell.run_background("sleep 60")
    print(f"  Started sleep: {shell_id}")
    time.sleep(0.1)

    output, status = shell.get_output(shell_id)
    print(f"  Status before kill: {status}")
    assert status == "running"

    success = shell.kill(shell_id)
    print(f"  Kill result: {success}")
    assert success

    output, status = shell.get_output(shell_id)
    print(f"  Status after kill: {status}")
    assert status == "not_found"

    print("  PASSED\n")


def test_long_running_with_interaction():
    """Test a long-running process with multiple interactions."""
    print("Test 5: Long-running with multiple interactions")

    shell = SimpleAsyncShell()

    # Start bc (calculator)
    shell_id = shell.run_background("bc -q")
    print(f"  Started bc: {shell_id}")
    time.sleep(0.1)

    # Do some calculations
    for expr, expected in [("1+1", "2"), ("10*5", "50"), ("100/4", "25")]:
        shell.send_input(shell_id, f"{expr}\n")
        time.sleep(0.1)
        output, _ = shell.get_output(shell_id)
        print(f"  {expr} = {output.strip()}")
        assert expected in output, f"Expected {expected}, got: {output}"

    shell.kill(shell_id)
    print("  PASSED\n")


if __name__ == "__main__":
    print("=" * 60)
    print("Async Shell Standalone Tests")
    print("=" * 60 + "\n")

    try:
        test_background_execution()
        test_stdin_with_cat()
        test_python_repl()
        test_kill()
        test_long_running_with_interaction()

        print("=" * 60)
        print("ALL TESTS PASSED!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
