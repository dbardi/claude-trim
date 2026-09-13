# claude-trim

A Claude Code hook that stops noisy command output from filling your context.

Installs, builds and test runs print thousands of lines and say almost
nothing. Every one of those lines is read back on every subsequent turn of the
session, so a single verbose `pnpm test` is charged again and again.

`claude-trim` rewrites those commands *before they run* so only the lines that
matter come back: errors, failures, and the final summary.

Measured on a real monorepo test run:

| | Lines | Bytes | Approx. tokens |
| --- | ---: | ---: | ---: |
| Before | 10,716 | 852,427 | ~213,000 |
| After | 157 | 8,015 | ~2,000 |

The failure list, the root cause and the `Tests:` summary all survived.

## Requirements

- Claude Code on Windows, macOS or Linux
- Python 3.8+
- PowerShell 7+ on Windows (the rewrite uses `??`); Bash elsewhere

## Install

```
git clone <this repo>
cd claude-trim
python install.py
```

That copies two scripts into `~/.claude` and merges a `PreToolUse` hook into
`~/.claude/settings.json`. Existing settings are preserved — the installer
merges, and copies the file to `settings.json.bak` first (`--no-backup` to
skip). Use `--dry-run` to see the change without writing it.

The hook is scoped to the shell tool Claude Code drives on your platform:
**PowerShell** on Windows, **Bash** on macOS and Linux. Override with
`--shell bash`, `--shell powershell` or `--shell both` if your setup differs
— a hook scoped to a tool your session never uses installs cleanly, reports
success, and then never fires.

If the hook does not fire straight away, open `/hooks` once or start a new
session. Claude Code only watches directories that already had a settings
file when the session started.

## What it catches

`pnpm` / `npm` / `yarn` install, build, test, lint, typecheck · `dotnet
build`/`test`/`restore`/`publish` · `msbuild` · `jest` · `tsc` · `eslint` ·
`vitest` · `playwright` · `gradlew` · `mvn` · `pip install` · `cargo` ·
`go build`/`test` · `make`

Matched at the start of any line, or after a `;`, `&&` or `|`. Both
`cd apps/web; pnpm build` and

```powershell
Set-Location C:/repo
pnpm build
```

are caught — multi-statement commands are the normal case, not the exception.

## What it leaves alone

Everything else, and deliberately these:

- `--help`, `--version`, `-v`, `--list` — the output *is* the answer
- `--watch`, `--watchAll`, `dev`, `serve`, `start` — long-running or
  interactive; piping them would hang the call
- anything already redirecting to a file
- every command, if the filter script is missing: its output is worth more
  than trimming it
- short output. Under 60 lines passes through whole, untouched

## What survives the filter

- error, failure, exception and warning lines
- TypeScript diagnostics (`TS2322`), jest failure bullets, cross marks
- `Tests:` / `Suites:` / `Snapshots:` summaries
- the last 25 lines, always
- stack frames in **your** source

Dropped: progress bars and spinners, download and resolver chatter, blank
lines, `PASS`/`✓` lines when trimming, and stack frames inside
`node_modules` — those are the frames that bury the one line naming the
actual error.

Every trim ends with a note saying how much was hidden, so nothing disappears
silently:

```
[trim-output: 113 of 138 lines hidden; re-run without the pipe for full output]
```

## Tuning

Both files are plain Python, installed at `~/.claude/`. Adding a build tool is
one line in the `NOISY` list in `hook-trim.py`. The thresholds live at the top
of `trim-output.py`:

| Constant | Default | Meaning |
| --- | ---: | --- |
| `KEEP_ALL_UNDER` | 60 | below this many lines, pass everything through |
| `TAIL_LINES` | 25 | trailing lines always kept |
| `MAX_SIGNAL` | 80 | cap on promoted error lines |

## How it works

The hook returns a rewritten command through
`hookSpecificOutput.updatedInput`. On Bash:

```bash
{
<your command>
} > <capture> 2>&1 && <python> <filter> <capture> ok || <python> <filter> <capture> failed
```

where `<capture>` is a file in the system temp directory with a fresh name
for every rewrite. On PowerShell:

```powershell
$o = & { <your command> } 2>&1 | Out-String -Stream
$c = $LASTEXITCODE
if (Test-Path "<filter>") { $o | & "<python>" "<filter>" } else { $o }
exit ($c ?? 0)
```

Details that are load-bearing in both:

- **Neither shell filters in a pipeline.** Both capture the output first, for
  different reasons. PowerShell has no `set -o pipefail`, so a pipeline would
  report the *filter's* exit code and a failed build would look successful.
  Bash runs every stage of a pipeline in a subshell, so a pipeline would
  discard any `cd` the command performed. Claude Code's Bash tool carries the
  working directory between calls, so the *next* command would silently run
  in the wrong place. `set -o pipefail` fixes the exit code but not that.
- **A brace group** so a multi-line command is captured whole; without it only
  the final line would be redirected. The group runs in the current shell,
  which is what keeps a `cd`, so a command that calls `exit` itself ends the
  shell before the filter sees its output.
- **The Bash rewrite asks the shell to expand nothing.** Claude Code asks
  before running a command it cannot analyze statically, and `$(mktemp)` or
  `$?` is exactly that, so every rewritten build would ask, even one on your
  allow list. Instead the hook names the capture file, and the filter learns
  the outcome from which side of `&& ... ||` runs it. It removes the capture
  file once read and exits 1 on `failed`, so a failed build still fails.
- **The filter is called as a plain command**, so an allow rule matches it:
  `Bash(/usr/bin/python3 /home/you/.claude/trim-output.py *)`.
- **The interpreter is an absolute path**, taken from the interpreter already
  running the hook. There is no bare `python` on most macOS and Linux
  installs.
- **The filter is checked before rewriting.** If it is missing, the hook
  leaves the command alone and you get full output, not an error.
- **The filter path is resolved beside the hook**, so a `--target` install
  finds its own copy rather than a hardcoded `~/.claude`.

## Uninstall

```
python uninstall.py
```

Removes the hook from `settings.json` and deletes the scripts. Other settings
and other hooks are left exactly as they were; `--keep-scripts` unhooks
without deleting. There is a round-trip test asserting that install followed
by uninstall returns `settings.json` to its original contents.

## Tests

```
python -m unittest discover -s tests
```

78 tests, no dependencies beyond the standard library. The Bash rewrite is
also run in a real `bash` where one is available.

| Area | Covers |
| --- | --- |
| Command matching | what gets wrapped, and what is deliberately left alone |
| Rewrite shape | exit-status handling, working-directory preservation, a missing filter, multi-line capture, no shell expansion on Bash, and that neither shell's syntax leaks into the other |
| Hook protocol | the JSON contract with Claude Code, including malformed input |
| Filter behavior | what survives and what is dropped, reading and removing a capture file, ANSI codes, carriage-return overwrites, and output the console codepage cannot encode |
| Installation | platform-correct matcher selection, idempotent reinstall, and that merging into `settings.json` never disturbs anything else in it |
