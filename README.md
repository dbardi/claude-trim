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
__ct=$(mktemp); {
<your command>
} > "$__ct" 2>&1; __cs=$?
{ [ -f "<filter>" ] && "<python>" "<filter>" < "$__ct" || cat "$__ct"; }
rm -f "$__ct"; (exit $__cs)
```

On PowerShell:

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
  discard any `cd` the command performed — and Claude Code's Bash tool carries
  the working directory between calls, so the *next* command would silently
  run in the wrong place. `set -o pipefail` fixes the exit code but not that.
- **A brace group** so a multi-line command is captured whole; without it only
  the final line would be redirected.
- **The interpreter is an absolute path**, taken from the interpreter already
  running the hook. There is no bare `python` on most macOS and Linux
  installs.
- **The filter is guarded.** If it is missing you get full output, not an
  error.
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

71 tests, no dependencies beyond the standard library. They cover what gets
wrapped, what must *not* get wrapped, the shape of both rewrites, that neither
shell's syntax leaks into the other, the hook's JSON protocol, what the filter
keeps and drops, platform-correct matcher selection, and — most importantly —
that merging into `settings.json` never disturbs anything else in it.

Several exist because of bugs that were expensive to find, and they share a
failure mode: **breaking quietly, in a way that looks like nothing happened.**

- A build tool on the second line of a multi-statement command never matched,
  because `NOISY` anchored on `^` without `re.MULTILINE`. A hook that decides
  "leave alone" is indistinguishable from a hook that is not installed.
- `str.splitlines()` breaks on `\r` as well as `\n`, so a progress bar
  overwriting itself in place was *expanded* into one line per update — the
  exact output this tool exists to suppress.
- Piping the command into the filter on Bash discarded any `cd` it performed,
  because Bash runs pipeline stages in subshells. The command itself still
  worked; the *next* one ran in the wrong directory.

The last was only caught by running the hook on a real Linux system. An
earlier check on Git Bash appeared to pass because the shell was already in
the target directory, so the `cd` was a no-op.
