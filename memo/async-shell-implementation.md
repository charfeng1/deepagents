# Async Shell Implementation Plan

> **Status**: Partially Implemented
>
> | Feature | Status |
> |---------|--------|
> | Background execution (`run_in_background`) | ✅ Implemented |
> | `shell_output` tool | ✅ Implemented |
> | `shell_input` tool (stdin pipe) | ✅ Implemented |
> | `kill_shell` tool | ✅ Implemented |
> | PTY mode (`interactive=True`) | ❌ Future work |

## Overview

Enhance DeepAgents' shell capabilities to support background execution, stdin input, and full PTY (pseudo-terminal) support. This addresses a major gap compared to Claude Agent SDK while also exceeding its capabilities.

## Current State

**DeepAgents (`shell.py`):**
- Uses `subprocess.run()` - blocking, synchronous
- No background process support
- No stdin input after process starts
- 120 second timeout, then killed
- Each command runs in fresh shell

**Claude Agent SDK:**
- `run_in_background=True` - async execution
- `BashOutput` - read from running process
- `KillShell` - terminate process
- **NO stdin input** - observe only, cannot interact

## Proposed Enhancement

DeepAgents will support THREE modes:

| Mode | Description | Use Case |
|------|-------------|----------|
| Blocking | Current behavior, `subprocess.run()` | Quick commands |
| Background (pipe) | Non-blocking with stdin pipe | Automation, scripting |
| Interactive (PTY) | Full pseudo-terminal | ssh, REPLs, vim |

---

## Implementation Details

### 1. Process Registry

```python
class ShellMiddleware(AgentMiddleware):
    def __init__(self, ...):
        # Process tracking
        self._processes: dict[str, subprocess.Popen] = {}
        self._master_fds: dict[str, int] = {}  # PTY file descriptors
        self._output_buffers: dict[str, list[str]] = defaultdict(list)
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._modes: dict[str, str] = {}  # "pipe" or "pty"
```

### 2. Tool Signatures

```python
@tool("shell")
def shell_tool(
    command: str,
    run_in_background: bool = False,
    interactive: bool = False,  # True = PTY, False = pipe
    runtime: ToolRuntime,
) -> str

@tool("shell_output")
def shell_output_tool(
    shell_id: str,
    runtime: ToolRuntime,
) -> str

@tool("shell_input")
def shell_input_tool(
    shell_id: str,
    input: str,
    runtime: ToolRuntime,
) -> str

@tool("kill_shell")
def kill_shell_tool(
    shell_id: str,
    runtime: ToolRuntime,
) -> str
```

### 3. Background Execution (Pipe Mode)

```python
def _run_background_pipe(self, command: str) -> str:
    shell_id = str(uuid.uuid4())[:8]

    process = subprocess.Popen(
        command,
        shell=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=self._workspace_root,
        env=self._env,
    )

    self._processes[shell_id] = process
    self._modes[shell_id] = "pipe"

    # Start output reader thread
    def reader():
        for line in iter(process.stdout.readline, ''):
            with self._locks[shell_id]:
                self._output_buffers[shell_id].append(line)
        process.stdout.close()

    threading.Thread(target=reader, daemon=True).start()

    return f"Background process started: {shell_id}"
```

### 4. Interactive Execution (PTY Mode)

```python
def _run_interactive_pty(self, command: str) -> str:
    shell_id = str(uuid.uuid4())[:8]

    # Create pseudo-terminal
    master_fd, slave_fd = pty.openpty()

    process = subprocess.Popen(
        command,
        shell=True,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=self._workspace_root,
        env={**self._env, "TERM": "xterm-256color"},
        preexec_fn=os.setsid,
    )

    os.close(slave_fd)  # Close slave in parent

    self._processes[shell_id] = process
    self._master_fds[shell_id] = master_fd
    self._modes[shell_id] = "pty"

    # Start output reader thread
    def reader():
        while True:
            if select.select([master_fd], [], [], 0.1)[0]:
                try:
                    data = os.read(master_fd, 4096).decode('utf-8', errors='replace')
                    with self._locks[shell_id]:
                        self._output_buffers[shell_id].append(data)
                except OSError:
                    break
            if process.poll() is not None:
                break

    threading.Thread(target=reader, daemon=True).start()

    return f"Interactive shell started: {shell_id}"
```

### 5. Shell Input Tool

```python
def _shell_input(self, shell_id: str, input_text: str) -> str:
    if shell_id not in self._processes:
        return f"Error: No process with ID '{shell_id}'"

    mode = self._modes.get(shell_id)

    if mode == "pty":
        master_fd = self._master_fds[shell_id]
        os.write(master_fd, input_text.encode('utf-8'))
    elif mode == "pipe":
        process = self._processes[shell_id]
        process.stdin.write(input_text)
        process.stdin.flush()
    else:
        return f"Error: Process '{shell_id}' does not support input"

    return f"Sent to {shell_id}: {repr(input_text)}"
```

### 6. Shell Output Tool

```python
def _shell_output(self, shell_id: str) -> str:
    if shell_id not in self._processes:
        return f"Error: No process with ID '{shell_id}'"

    process = self._processes[shell_id]

    # Get buffered output
    with self._locks[shell_id]:
        output = "".join(self._output_buffers[shell_id])
        self._output_buffers[shell_id] = []

    # Check status
    exit_code = process.poll()
    if exit_code is None:
        status = "running"
    else:
        status = f"exited (code: {exit_code})"
        self._cleanup_process(shell_id)

    return f"[{status}]\n{output or '<no new output>'}"
```

### 7. Cleanup

```python
def _cleanup_process(self, shell_id: str):
    """Clean up a finished process."""
    if shell_id in self._master_fds:
        try:
            os.close(self._master_fds[shell_id])
        except OSError:
            pass
        del self._master_fds[shell_id]

    if shell_id in self._processes:
        del self._processes[shell_id]

    if shell_id in self._modes:
        del self._modes[shell_id]

def cleanup_all(self):
    """Kill all background processes on shutdown."""
    for shell_id in list(self._processes.keys()):
        try:
            self._processes[shell_id].terminate()
            self._processes[shell_id].wait(timeout=5)
        except:
            self._processes[shell_id].kill()
        self._cleanup_process(shell_id)
```

---

## Comparison: stdin Pipe vs PTY

| Aspect | stdin Pipe | PTY |
|--------|-----------|-----|
| `isatty()` returns | `False` | `True` |
| ANSI colors | Disabled | Enabled |
| Line editing | Disabled | Enabled |
| Ctrl+C handling | Kills process | Sends SIGINT |
| Works with ssh | No | Yes |
| Works with vim | No | Yes |
| Output cleanliness | Clean text | May have escape codes |
| Complexity | Simple | More complex |
| Use case | Automation | Interactive |

**Recommendation:** Support both, default to pipe, opt-in to PTY.

---

## Files to Modify

1. **`libs/deepagents-cli/deepagents_cli/shell.py`** - Main implementation
2. **`libs/deepagents-cli/deepagents_cli/agent.py`** - Register new tools
3. **`libs/deepagents-cli/tests/`** - Add tests

---

## Estimated Effort

| Component | Lines | Time |
|-----------|-------|------|
| Process registry | ~20 | 30m |
| Background pipe mode | ~40 | 1h |
| PTY mode | ~50 | 1.5h |
| shell_input tool | ~25 | 30m |
| shell_output tool | ~25 | 30m |
| kill_shell tool | ~15 | 15m |
| Cleanup logic | ~20 | 30m |
| Tests | ~100 | 1.5h |
| **Total** | **~295** | **~6h** |

---

## Future Enhancements

1. **Timeout for background processes** - Auto-kill after N seconds
2. **Output filtering** - Regex filter on shell_output
3. **Multiple shells** - List all running shells
4. **Shell naming** - Custom names instead of UUIDs
5. **Persistent shells** - Survive agent restart
