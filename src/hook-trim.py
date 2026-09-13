"""PreToolUse hook: rewrite noisy build/install/test commands to pipe through
the output filter.

Wired up in ~/.claude/settings.json by install.py, against whichever shell
tool Claude Code uses on this platform:

    {"matcher": "PowerShell",   # or "Bash", or "Bash|PowerShell"
     "hooks": [{"type": "command", "command": "<python> <dir>/hook-trim.py"}]}

Reads the tool call as JSON on stdin. Emits either {} (leave the command
alone) or a hookSpecificOutput carrying the rewritten command.

Keeping the match list here rather than in settings.json `if` rules means one
readable, testable place to edit - adding a build tool is a one-line change.
"""
import json
import pathlib
import re
import shlex
import sys
import tempfile
import uuid

# Beside this file, wherever it was installed - not a hardcoded ~/.claude,
# so a --target install still finds its own filter.
FILTER = (pathlib.Path(__file__).resolve().parent / "trim-output.py").as_posix()

# Commands that print a lot and say little. Matched at the start of any line,
# or after a statement separator, so "cd foo; pnpm test" is caught.
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
    # MULTILINE so ^ anchors at every line, not just the first. Claude Code
    # sends multi-statement commands across several lines, and a newline
    # separates statements exactly as ; and && do.
    re.IGNORECASE | re.VERBOSE | re.MULTILINE,
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
    most common way a working install breaks on someone else's machine - on
    macOS and most Linux distributions there is no bare `python` at all.

    Forward slashes: Windows accepts them, and they carry through every
    quoting layer between here and the shell unambiguously.
    """
    return pathlib.Path(sys.executable).as_posix() if sys.executable else "python3"


def rewrite_powershell(command):
    """PowerShell has no `set -o pipefail`, so capture the output first while
    $LASTEXITCODE still belongs to the wrapped command, then filter and
    re-raise that code. $LASTEXITCODE is null when nothing native ran.

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


def rewrite_bash(command):
    """Capture to a file, then filter it - the same shape as the PowerShell
    branch, and for a closely related reason.

    Bash runs every stage of a pipeline in a subshell, so piping the command
    straight into the filter discards any `cd` it performed. Claude Code's
    Bash tool carries the working directory between calls, so that silently
    breaks the next one. Redirecting a brace group to a file keeps the command
    in the current shell, and captures a multi-line command whole.

    Nothing here asks the shell to expand anything. When reads outside the
    working directories are blocked, Claude Code asks the user about any
    command it cannot analyse statically, so a `$(mktemp)` or a `$?` would
    make every rewritten build ask, even one the user has allowed. The capture
    path is chosen here instead, the filter learns the outcome from which side
    of `&& ... ||` runs it, and it removes the capture file itself.
    """
    capture = shlex.quote(
        (pathlib.Path(tempfile.gettempdir()) / f"claude-trim-{uuid.uuid4().hex}.out").as_posix())
    run_filter = f"{shlex.quote(interpreter())} {shlex.quote(FILTER)} {capture}"
    return f"{{\n{command}\n}} > {capture} 2>&1 && {run_filter} ok || {run_filter} failed"


REWRITERS = {"Bash": rewrite_bash, "PowerShell": rewrite_powershell}


def decide(command, tool_name):
    """Return the rewritten command, or None to leave it untouched.

    A missing filter leaves every command untouched: its output is worth more
    than trimming it.
    """
    rewrite = REWRITERS.get(tool_name)
    if rewrite is None or not command or "trim-output" in command:
        return None
    if EXEMPT.search(command):
        return None
    if not NOISY.search(command):
        return None
    if not pathlib.Path(FILTER).is_file():
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
