# Agent-to-Agent Orchestration via Interactive Shell

## Concept

Use DeepAgents' interactive shell capability to spawn and control other AI agent CLIs (Claude Code, Aider, etc.), enabling hierarchical agent orchestration.

```
┌─────────────────────────────────────────────────────────┐
│                 DeepAgents (Master Agent)               │
│                                                         │
│  Orchestrates, delegates, synthesizes                   │
│                                                         │
└────────────┬───────────────────┬───────────────────────┘
             │                   │
     ┌───────▼───────┐   ┌───────▼───────┐
     │  Claude Code  │   │     Aider     │
     │  (Child #1)   │   │  (Child #2)   │
     │               │   │               │
     │  Code analysis│   │  Git-aware    │
     │  & generation │   │  editing      │
     └───────────────┘   └───────────────┘
```

---

## Why This Is Powerful

### 1. Context Window Multiplication
- Master agent: 200k context
- Each child agent: 200k context
- Spawn 5 agents = 1M effective context

### 2. Model Mixing
- Master: GPT-4 (orchestration)
- Child 1: Claude (analysis)
- Child 2: Gemini (research)
- Combine strengths of different models

### 3. Capability Composition
- Claude Code: File operations, bash
- Aider: Git integration
- Custom agent: Domain-specific tools
- Master combines all capabilities

### 4. Isolation & Fault Tolerance
- Child crashes don't affect master
- Each child has clean state
- Can retry failed children

### 5. Parallel Execution
- Spawn multiple children simultaneously
- Each works independently
- Master synthesizes results

---

## Compatible Agent CLIs

| Agent | Command | Strengths |
|-------|---------|-----------|
| Claude Code | `claude` | Full IDE-like capabilities |
| Aider | `aider` | Git-aware, diff-based editing |
| GPT Engineer | `gpt-engineer` | Full project generation |
| Mentat | `mentat` | Code understanding |
| Open Interpreter | `interpreter` | Code execution |
| Another DeepAgents | `deepagents` | Recursive orchestration |

---

## Example Workflow

```python
# 1. Spawn Claude Code for exploration
shell("claude --dangerously-skip-permissions",
      interactive=True, run_in_background=True)
# Returns: shell_id="claude_1"

# 2. Wait for ready
shell_output("claude_1")
# "What would you like to do?"

# 3. Send exploration task
shell_input("claude_1",
    "Explore the authentication system. Find all auth providers, "
    "their configurations, and security issues. When done, say DONE.")

# 4. Wait for completion
while "DONE" not in shell_output("claude_1"):
    time.sleep(2)

# 5. Get final report
report = shell_output("claude_1")

# 6. Spawn second agent for implementation
shell("aider --yes", interactive=True, run_in_background=True)
# Returns: shell_id="aider_1"

# 7. Send implementation task based on findings
shell_input("aider_1", f"Based on this analysis:\n{report}\n\n"
    "Please fix the SQL injection vulnerability in auth/oauth.py")
```

---

## Challenges & Solutions

### Challenge 1: Detecting Response Completion

**Problem:** How to know when child agent finished responding?

**Solutions:**

A. **Prompt Detection** - Look for known prompts:
```python
AGENT_PROMPTS = [
    "> ",           # Claude Code
    ">>> ",         # Python REPL
    "aider> ",      # Aider
    "$ ",           # Shell
]

def is_ready(output: str) -> bool:
    return any(output.rstrip().endswith(p.rstrip()) for p in AGENT_PROMPTS)
```

B. **Completion Signals** - Ask agent to signal:
```python
shell_input(shell_id, "When finished, output: <<<COMPLETE>>>")

while "<<<COMPLETE>>>" not in shell_output(shell_id):
    time.sleep(1)
```

C. **Timeout-based** - Wait fixed duration:
```python
time.sleep(30)  # Assume done after 30 seconds
output = shell_output(shell_id)
```

### Challenge 2: Tool Approval Handling

**Problem:** Claude Code asks for tool approval with interactive UI (arrow keys).

**Solutions:**

A. **Skip permissions flag:**
```bash
claude --dangerously-skip-permissions
```

B. **Send escape sequences for arrow keys:**
```python
# Arrow down
shell_input(shell_id, "\x1b[B")

# Arrow up
shell_input(shell_id, "\x1b[A")

# Enter to select
shell_input(shell_id, "\n")
```

C. **Send number keys for selection:**
```python
shell_input(shell_id, "1\n")  # Select option 1
```

D. **Pattern matching + auto-response:**
```python
output = shell_output(shell_id)
if "Do you want to" in output or "[y/n]" in output:
    shell_input(shell_id, "y\n")
```

### Challenge 3: ANSI Escape Code Pollution

**Problem:** PTY output includes color codes, cursor movement.

**Solution:** Strip ANSI codes:
```python
import re

def strip_ansi(text: str) -> str:
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    return ansi_pattern.sub('', text)
```

### Challenge 4: Timing & Synchronization

**Problem:** Need to wait for child to be ready before sending input.

**Solution:** Polling with backoff:
```python
def wait_for_ready(shell_id: str, timeout: float = 60) -> str:
    start = time.time()
    accumulated = []

    while time.time() - start < timeout:
        output = shell_output(shell_id)
        accumulated.append(output)

        full_output = "".join(accumulated)
        if is_ready(full_output):
            return full_output

        time.sleep(0.5)

    raise TimeoutError(f"Agent {shell_id} did not become ready")
```

---

## Structured Communication Protocol

For reliable agent-to-agent communication:

```xml
<!-- Master sends structured request -->
<agent_request>
  <task>Analyze authentication system</task>
  <output_format>JSON with keys: providers, configs, issues</output_format>
  <completion_signal><<<TASK_COMPLETE>>></completion_signal>
</agent_request>

<!-- Child responds with structured output -->
<agent_response>
  <result>
    {"providers": [...], "configs": [...], "issues": [...]}
  </result>
  <<<TASK_COMPLETE>>>
</agent_response>
```

---

## Advanced: Recursive Agent Spawning

Master DeepAgents can spawn Claude Code, which can spawn sub-agents:

```
DeepAgents (Master)
    │
    ├── Claude Code (explorer)
    │       │
    │       └── Task subagent (via Claude's Task tool)
    │
    └── Claude Code (implementer)
            │
            └── Task subagent (testing)
```

Each level adds another context window!

---

## Implementation: Agent Orchestration Helpers

```python
class AgentOrchestrator:
    """Helper for managing child agents."""

    def __init__(self, shell_middleware: ShellMiddleware):
        self._shell = shell_middleware
        self._agents: dict[str, AgentInfo] = {}

    def spawn(
        self,
        agent_type: str,  # "claude", "aider", etc.
        name: str | None = None,
        auto_approve: bool = True,
    ) -> str:
        """Spawn a child agent."""
        commands = {
            "claude": "claude" + (" --dangerously-skip-permissions" if auto_approve else ""),
            "aider": "aider" + (" --yes" if auto_approve else ""),
            "deepagents": "deepagents" + (" --auto-approve" if auto_approve else ""),
        }

        cmd = commands.get(agent_type)
        if not cmd:
            raise ValueError(f"Unknown agent type: {agent_type}")

        result = self._shell._run_interactive_pty(cmd)
        shell_id = result.split(": ")[1]  # Extract ID

        self._agents[shell_id] = AgentInfo(
            type=agent_type,
            name=name or shell_id,
            status="starting",
        )

        # Wait for agent to be ready
        self._wait_for_ready(shell_id)
        self._agents[shell_id].status = "ready"

        return shell_id

    def send(self, agent_id: str, message: str) -> None:
        """Send message to child agent."""
        self._shell._shell_input(agent_id, message + "\n")
        self._agents[agent_id].status = "working"

    def receive(
        self,
        agent_id: str,
        wait_for_complete: bool = True,
        timeout: float = 300,
    ) -> str:
        """Receive response from child agent."""
        if wait_for_complete:
            return self._wait_for_completion(agent_id, timeout)
        else:
            return self._shell._shell_output(agent_id)

    def ask(
        self,
        agent_id: str,
        message: str,
        timeout: float = 300,
    ) -> str:
        """Send message and wait for response."""
        self.send(agent_id, message)
        return self.receive(agent_id, wait_for_complete=True, timeout=timeout)

    def kill(self, agent_id: str) -> None:
        """Terminate child agent."""
        self._shell._kill_shell(agent_id)
        del self._agents[agent_id]

    def list_agents(self) -> list[AgentInfo]:
        """List all spawned agents."""
        return list(self._agents.values())
```

---

## Use Cases

1. **Multi-perspective code review**
   - Spawn 3 Claude Code instances
   - Each reviews from different angle (security, performance, style)
   - Master synthesizes findings

2. **Divide and conquer refactoring**
   - Master breaks down large refactor
   - Each child handles one component
   - Master ensures integration

3. **Research + Implementation pipeline**
   - Child 1: Research best practices
   - Child 2: Implement based on research
   - Child 3: Test implementation
   - Master orchestrates flow

4. **Model comparison**
   - Same task to Claude, GPT, Gemini agents
   - Master compares and picks best

5. **Continuous monitoring**
   - Background agent watches logs
   - Alerts master on issues
   - Master spawns fix agents

---

## Security Considerations

1. **Privilege escalation** - Child agents inherit shell permissions
2. **Resource exhaustion** - Limit number of concurrent children
3. **Infinite loops** - Prevent recursive spawning without limits
4. **Sensitive data** - Children see each other's output if not isolated
5. **Cost** - Each child uses API tokens

---

## Future Enhancements

1. **Agent pooling** - Reuse agents instead of spawning new ones
2. **Load balancing** - Distribute tasks across available agents
3. **Agent specialization** - Configure agents for specific domains
4. **Inter-agent communication** - Children talk to each other
5. **Persistent agents** - Survive master restart
6. **Agent checkpointing** - Save/restore agent state
