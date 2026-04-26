# Codex CLI Backend on Windows

This mode lets ContractCoding use Codex CLI as the model backend instead of the OpenAI chat API.

The important security boundary is:

- Codex CLI is launched as a read-only process.
- Codex CLI must not write, edit, delete, or move project files directly.
- Codex CLI returns generated code as text inside a `<file_write>` envelope.
- ContractCoding validates the target path against the scheduler-selected file, then writes the file itself through `WorkspaceFS`.

## Configuration

Set these environment variables before running the engine:

```powershell
$env:MODEL_BACKEND = "codex_cli"
$env:CODEX_CLI_COMMAND = 'codex exec --sandbox read-only --ask-for-approval never -'
$env:CODEX_CLI_WORKDIR = "."
$env:CODEX_CLI_TIMEOUT = "300"
$env:CODEX_CLI_READ_ONLY = "true"
python main.py --task "Your task description"
```

`CODEX_CLI_COMMAND` is intentionally configurable because Windows installations can differ. If the Microsoft Store alias or your terminal cannot launch `codex`, point this value at the real executable or a trusted wrapper script, for example:

```powershell
$env:CODEX_CLI_COMMAND = '"C:\\path\\to\\codex.exe" exec --sandbox read-only --ask-for-approval never -'
```

## Returned Code Format

For implementation tasks, Codex CLI should return the full file content like this:

```text
<file_write path="service.py">
```python
def run() -> bool:
    return True
```
</file_write>
```

The framework only materializes the file that the scheduler asked the current implementation agent to work on. If Codex CLI returns another path, that path is ignored. If no matching `<file_write>` block is found, the agent retries with a stricter instruction.

## Tool Calling Behavior

When `MODEL_BACKEND=codex_cli`, native LLM tool calling is disabled. The CLI receives tool names only as context and must remain read-only. Document updates are still returned through the existing `<document_action>` protocol.

## Why This Avoids Direct File Hacks

The model backend no longer receives write authority. It can propose code, but the process that writes files is ContractCoding itself. That lets the scheduler enforce ownership and target-file constraints while still preserving the existing multi-agent workflow.
