# TUI Interaction: Controlling Interactive UIs via PTY

> **Status**: Design Document (Future Work)
>
> This document describes how to control terminal UIs programmatically using
> PTY and ANSI escape sequences. **PTY mode is not yet implemented** - this
> serves as a design reference for future implementation.
>
> **Current state**: stdin pipe mode works for many programs
> **Future work**: PTY mode for full TUI control (ssh, vim, etc.)

## The Problem

Claude Code (and other modern CLIs) use interactive terminal UIs that require:
- Arrow key navigation
- Number key selection
- Enter to confirm
- Sometimes Ctrl+C to cancel

**Question:** Can a master agent control these UIs programmatically?

**Answer:** YES, using ANSI escape sequences via PTY.

---

## ANSI Escape Sequences for Key Input

When you press a key in a terminal, it sends specific byte sequences. We can send these programmatically:

### Arrow Keys

```python
KEYS = {
    "up":    "\x1b[A",    # ESC [ A
    "down":  "\x1b[B",    # ESC [ B
    "right": "\x1b[C",    # ESC [ C
    "left":  "\x1b[D",    # ESC [ D
}

# Usage
shell_input(shell_id, KEYS["down"])  # Move selection down
shell_input(shell_id, KEYS["up"])    # Move selection up
```

### Special Keys

```python
KEYS = {
    "enter":     "\n",        # or "\r"
    "tab":       "\t",
    "escape":    "\x1b",
    "backspace": "\x7f",      # or "\x08"
    "delete":    "\x1b[3~",
    "home":      "\x1b[H",
    "end":       "\x1b[F",
    "page_up":   "\x1b[5~",
    "page_down": "\x1b[6~",
}
```

### Control Keys

```python
KEYS = {
    "ctrl_c": "\x03",    # SIGINT
    "ctrl_d": "\x04",    # EOF
    "ctrl_z": "\x1a",    # SIGTSTP (suspend)
    "ctrl_l": "\x0c",    # Clear screen
    "ctrl_a": "\x01",    # Start of line
    "ctrl_e": "\x05",    # End of line
}
```

### Number Keys

```python
# Just send the character
shell_input(shell_id, "1")   # Select option 1
shell_input(shell_id, "2")   # Select option 2
```

---

## Claude Code Tool Approval Handling

### Option 1: Skip Permissions Entirely

```bash
claude --dangerously-skip-permissions
```

This bypasses all approval prompts. Easiest solution.

### Option 2: Send Number Key Selection

Claude Code typically shows numbered options:
```
1. Allow this tool
2. Allow all tools
3. Deny
```

```python
# Detect approval prompt
output = shell_output(shell_id)
if "Allow this tool" in output or "approve" in output.lower():
    shell_input(shell_id, "1\n")  # Select option 1 + Enter
```

### Option 3: Arrow Key Navigation

If the UI requires arrow navigation:

```python
def select_option(shell_id: str, option_index: int):
    """Select option by moving down N times, then Enter."""
    for _ in range(option_index):
        shell_input(shell_id, "\x1b[B")  # Arrow down
        time.sleep(0.1)  # Small delay for UI update
    shell_input(shell_id, "\n")  # Enter to confirm
```

### Option 4: Pattern-Based Auto-Response

```python
APPROVAL_PATTERNS = {
    r"Allow.*tool": "1\n",           # Select "Allow"
    r"Do you want to continue": "y",
    r"\[y/n\]": "y",
    r"Are you sure": "y",
    r"Press Enter": "\n",
}

def auto_respond(shell_id: str, output: str):
    for pattern, response in APPROVAL_PATTERNS.items():
        if re.search(pattern, output, re.IGNORECASE):
            shell_input(shell_id, response)
            return True
    return False
```

---

## Full Example: Controlling Claude Code

```python
import time
import re

class ClaudeCodeController:
    """Control Claude Code CLI via PTY."""

    READY_PROMPT = ">"  # Claude Code's prompt
    APPROVAL_KEYWORDS = ["allow", "approve", "confirm", "y/n"]

    def __init__(self, shell_middleware):
        self._shell = shell_middleware
        self._shell_id = None

    def start(self, auto_approve: bool = True):
        """Start Claude Code session."""
        cmd = "claude"
        if auto_approve:
            cmd += " --dangerously-skip-permissions"

        result = self._shell._run_interactive_pty(cmd)
        self._shell_id = self._extract_shell_id(result)

        # Wait for ready
        self._wait_for_prompt()
        return self._shell_id

    def send_message(self, message: str) -> str:
        """Send message and get response."""
        self._shell._shell_input(self._shell_id, message + "\n")

        # Collect response until prompt returns
        response = self._collect_until_prompt()

        return response

    def _collect_until_prompt(self, timeout: float = 300) -> str:
        """Collect output until we see the prompt again."""
        start = time.time()
        collected = []

        while time.time() - start < timeout:
            output = self._shell._shell_output(self._shell_id)
            collected.append(output)

            # Check for approval prompts and handle them
            if self._needs_approval(output):
                self._handle_approval(output)
                continue

            # Check if prompt returned
            full = "".join(collected)
            if self._is_ready(full):
                return full

            time.sleep(0.5)

        return "".join(collected)

    def _needs_approval(self, output: str) -> bool:
        """Check if output contains approval request."""
        lower = output.lower()
        return any(kw in lower for kw in self.APPROVAL_KEYWORDS)

    def _handle_approval(self, output: str):
        """Handle tool approval prompts."""
        # Try number selection first (most Claude Code UIs)
        if re.search(r"1\.", output) or re.search(r"Allow", output):
            self._shell._shell_input(self._shell_id, "1\n")
            return

        # Fall back to y/n
        if "[y/n]" in output.lower():
            self._shell._shell_input(self._shell_id, "y\n")
            return

        # Arrow key navigation as last resort
        self._shell._shell_input(self._shell_id, "\x1b[B\n")  # Down + Enter

    def _is_ready(self, output: str) -> bool:
        """Check if Claude Code is ready for input."""
        stripped = output.rstrip()
        return stripped.endswith(">") or stripped.endswith("?")

    def _wait_for_prompt(self, timeout: float = 60):
        """Wait for initial prompt."""
        start = time.time()
        while time.time() - start < timeout:
            output = self._shell._shell_output(self._shell_id)
            if self._is_ready(output):
                return
            time.sleep(0.5)
        raise TimeoutError("Claude Code did not start")

    def stop(self):
        """Stop Claude Code session."""
        # Send exit command
        self._shell._shell_input(self._shell_id, "/exit\n")
        time.sleep(1)
        # Force kill if still running
        self._shell._kill_shell(self._shell_id)
```

---

## Handling Complex TUIs (Ink/React-based)

Modern CLIs like Claude Code use Ink (React for terminals). These can have:
- Checkboxes
- Radio buttons
- Multi-select lists
- Text inputs with validation

### Strategies:

**1. Inspect the UI library's key bindings**
- Ink typically uses: Enter, Space, Arrow keys, Tab
- Space often toggles checkboxes
- Enter confirms

**2. Send keys with appropriate timing**
```python
def interact_with_tui(shell_id: str, actions: list[str]):
    """Send a sequence of TUI actions with timing."""
    for action in actions:
        if action in KEYS:
            shell_input(shell_id, KEYS[action])
        else:
            shell_input(shell_id, action)
        time.sleep(0.15)  # Allow UI to update
```

**3. Look for text cues in output**
```python
# Detect which option is selected (usually marked with > or [x])
if ">" in line or "[x]" in line or "●" in line:
    # This option is selected
    pass
```

---

## Limitations

1. **Timing sensitivity** - Some TUIs need delays between keystrokes
2. **No visual feedback** - Master can't "see" the UI, only parse text output
3. **Cursor position** - Hard to know exact cursor location
4. **Dynamic content** - Animations, spinners can pollute output
5. **Platform differences** - Key codes may vary slightly

---

## Recommendation for Agent-to-Agent Control

**Best approach for Claude Code:**

1. **First choice:** Use `--dangerously-skip-permissions` to bypass approvals
2. **Second choice:** Use number keys (`1\n`, `2\n`) for selection
3. **Third choice:** Use escape sequences for arrow navigation
4. **Always:** Add timing delays and retry logic

```python
# Robust approval handling
def handle_any_approval(shell_id: str, output: str, max_retries: int = 3):
    for attempt in range(max_retries):
        # Try number selection
        shell_input(shell_id, "1\n")
        time.sleep(0.3)

        new_output = shell_output(shell_id)
        if not needs_approval(new_output):
            return True  # Approval handled

        # Try arrow + enter
        shell_input(shell_id, "\x1b[B\n")
        time.sleep(0.3)

        new_output = shell_output(shell_id)
        if not needs_approval(new_output):
            return True

    return False  # Failed to handle
```

---

## Summary

| Method | Reliability | Complexity |
|--------|-------------|------------|
| `--dangerously-skip-permissions` | 100% | None |
| Number key selection (`1\n`) | 95% | Low |
| Arrow key navigation | 85% | Medium |
| Full TUI simulation | 70% | High |

**For agent-to-agent orchestration, use the permission skip flag when possible, fall back to number keys.**
