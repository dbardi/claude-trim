"""PreToolUse hook: rewrite noisy build/install/test commands to pipe through
the output filter.

Wired up in ~/.claude/settings.json by install.py:

    {"matcher": "PowerShell",
     "hooks": [{"type": "command", "command": "<python> <dir>/hook-trim.py"}]}

Reads the tool call as JSON on stdin. Emits either {} (leave the command
alone) or a hookSpecificOutput carrying the rewritten command.

Keeping the match list here rather than in settings.json `if` rules means one
readable, testable place to edit - adding a build tool is a one-line change.
"""
import json
import re
import sys

SHELL = "PowerShell"
FILTER = "$HOME/.claude/trim-output.py"

# Commands that print a lot and say little. Matched at the start of the
# command or after a statement separator, so "cd foo; pnpm test" is caught.
NOISY = re.compile(
    r"""(^|[;&|]\s*|&&\s*)(
        pnpm\s+(install|i|add|update|build|test|lint|typecheck|parity:)
      | npm\s+(install|ci|test|run|rebuild)
      | yarn\s+(install|add|build|test|lint)
      | dotnet\s+(build|test|restore|publish|pack)
      | msbuild
      | (\S*[/\\])?(jest|tsc|eslint|vitest|playwright)\b
      | gradlew?\s+(build|test)
      | mvn\s+(clean|install|test|package)
      | (pip|pip3)\s+install
      | cargo\s+(build|test)
      | go\s+(build|test)
      | make\b
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Never rewrite these, even though they match above - the pipe would break
# them or the output is the point.
EXEMPT = re.compile(
    r"""(
        --help | -h\b | --version | -v\b
      | --watch | --watchAll
      | \bdev\b | \bserve\b | \bstart\b     # long-running / interactive
      | --list\b
      | >\s*\S                              # already redirecting to a file
    )""",
    re.IGNORECASE | re.VERBOSE,
)


def interpreter():
    """The interpreter already running this hook. It is guaranteed to exist
    and to be a version the filter parses. Assuming `python` is on PATH is the
    most common way a working install breaks on someone else's machine."""
    return sys.executable or "python"


def rewrite(command):
    """PowerShell has no `set -o pipefail`, so capture the output first while
    $LASTEXITCODE still belongs to the wrapped command, then filter and
    re-raise that code.

    `~` is not expanded inside a native command's arguments, so the filter
    path goes through $HOME. $LASTEXITCODE is null when nothing native ran.
    Test-Path degrades to unfiltered output if the filter is missing.
    """
    python = f'"{interpreter()}"'
    return (
        f"$o = & {{ {command} }} 2>&1 | Out-String -Stream; "
        f"$c = $LASTEXITCODE; "
        f'if (Test-Path "{FILTER}") {{ $o | & {python} "{FILTER}" }} '
        f"else {{ $o }}; "
        f"exit ($c ?? 0)"
    )


def decide(command, tool_name):
    """Return the rewritten command, or None to leave it untouched."""
    if tool_name != SHELL or not command or "trim-output" in command:
        return None
    if EXEMPT.search(command):
        return None
    if not NOISY.search(command):
        return None
    return rewrite(command)


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        print("{}")
        return

    tool_input = payload.get("tool_input") or {}
    rewritten = decide(tool_input.get("command", ""),
                       payload.get("tool_name", ""))

    if rewritten is None:
        print("{}")
        return

    updated = dict(tool_input)
    updated["command"] = rewritten
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": updated,
        }
    }))


if __name__ == "__main__":
    main()
