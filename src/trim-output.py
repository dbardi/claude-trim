"""Filter noisy command output down to the lines that carry information.

Reads stdin, writes a trimmed version to stdout. Designed to sit on the right
of a pipe in a PreToolUse-rewritten command:

    set -o pipefail; pnpm test 2>&1 | python ~/.claude/trim-output.py

Rules:
  * Short output passes through untouched (see KEEP_ALL_UNDER).
  * Progress bars, spinners and download chatter are dropped outright.
  * Error / failure / warning lines are always kept.
  * The final summary (last TAIL_LINES) is always kept.
  * A one-line note reports how much was hidden.

Exit code is not this script's concern - `set -o pipefail` in the rewritten
command preserves the original command's status.
"""
import re
import sys

KEEP_ALL_UNDER = 60      # lines; below this the whole output is passed through
TAIL_LINES = 25          # trailing lines always kept (the final summary)
MAX_SIGNAL = 80          # cap on promoted error/failure lines

ANSI = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]|\x1B\][^\x07]*\x07")

# Lines worth keeping wherever they appear.
SIGNAL = re.compile(
    r"""(
        \berror\b | \bERR!\b | \bERROR\b
      | \bfail(ed|ure|s)?\b
      | \bexception\b | \btraceback\b
      | \bwarn(ing)?\b
      | \bcannot\b | \bnot\s+found\b | \bundefined\b
      | \bENOENT\b | \bELIFECYCLE\b | \bEADDRINUSE\b
      | \bTS\d{3,}\b                      # TypeScript diagnostics
      | ^\s*[-+]?\s*(Expected|Received|Difference)\b
      | ^\s*●                        # jest failure bullet
      | [✕✗✘×❌]  # cross marks
      | \bTests?:\s | \bSuites?:\s | \bSnapshots:\s
    )""",
    re.IGNORECASE | re.VERBOSE,
)

# Pure chatter - dropped before anything else is decided.
NOISE = re.compile(
    r"""(
        ^\s*Progress:\s*resolved\s              # pnpm resolver ticker
      | ^\s*Packages:\s*[-+]\d
      | ^\s*(Downloading|Fetching|Extracting|Resolving|Linking|Unpacking)\b
      | ^\s*(added|removed|changed)\s+\d+\s+packages?\b
      | ^\s*\[?\d{1,3}%\]?\s                    # bare percentage ticks
      | [─-╿▀-▟]{4,}        # box / block progress bars
      | [⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]  # braille spinner
      | ^\s*at\s.*\bnode_modules[/\x5c]   # library stack frames
      | ^\s*$
    )""",
    re.VERBOSE,
)


# Successful-step lines. Kept when output is short, dropped when trimming -
# they are the archetype of "prints a lot, says nothing".
PASSING = re.compile(
    r"""(
        ^\s*PASS\b
      | ^\s*[✓✔]
      | ^\s*ok\s+\d+
      | ^\s*Done\s+in\s
    )""",
    re.VERBOSE,
)


def clean(raw):
    """Strip ANSI, collapse carriage-return overwrites, drop chatter."""
    out = []
    for line in raw.splitlines():
        line = line.split("\r")[-1]          # progress bars overwrite in place
        line = ANSI.sub("", line).rstrip()
        if NOISE.search(line):
            continue
        out.append(line)
    return out


def emit(text):
    """Write via the byte stream - the console codepage (cp1252 on Windows)
    cannot encode marks like U+25CF that jest and tsc emit."""
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main():
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    lines = clean(raw)

    if len(lines) <= KEEP_ALL_UNDER:
        emit("\n".join(lines) + ("\n" if lines else ""))
        return

    original = len(lines)
    lines = [l for l in lines if not PASSING.search(l)]

    tail_from = max(0, len(lines) - TAIL_LINES)
    signal = [i for i, l in enumerate(lines) if i < tail_from and SIGNAL.search(l)]
    dropped_signal = max(0, len(signal) - MAX_SIGNAL)
    signal = signal[:MAX_SIGNAL]

    keep = sorted(set(signal) | set(range(tail_from, len(lines))))

    result, prev = [], None
    for i in keep:
        if prev is not None and i > prev + 1:
            result.append(f"    ... {i - prev - 1} lines omitted ...")
        result.append(lines[i])
        prev = i

    hidden = original - len(keep)
    note = f"[trim-output: {hidden} of {original} lines hidden"
    if dropped_signal:
        note += f"; {dropped_signal} further matches capped"
    note += "; re-run without the pipe for full output]"

    emit("\n".join(result) + "\n" + note + "\n")


if __name__ == "__main__":
    main()
