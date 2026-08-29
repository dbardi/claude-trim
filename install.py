"""Install the PowerShell output-trimming hook into Claude Code.

    python install.py [--target DIR] [--dry-run] [--no-backup]

Copies the two scripts into ~/.claude and merges a PreToolUse hook into
~/.claude/settings.json. Merging - not overwriting - is the whole point: that
file usually holds settings the user cares about more than this hook.
"""
import argparse
import copy
import json
import pathlib
import shutil
import sys

SOURCES = ("hook-trim.py", "trim-output.py")
MATCHER = "PowerShell"
MARKER = "hook-trim.py"


def default_target():
    return pathlib.Path.home() / ".claude"


def hook_entry(script):
    """Absolute interpreter and script path, written with forward slashes.

    `python` is not on PATH on every machine and `~` is not expanded by every
    shell that runs a hook, so both are resolved here. Windows backslashes are
    not usable: they survive a shell but not every layer that parses the
    command on the way there, and the hook then silently never fires.
    """
    return {
        "type": "command",
        "command": f'"{pathlib.Path(sys.executable).as_posix()}" '
                   f'"{pathlib.Path(script).as_posix()}"',
        "statusMessage": "Trimming noisy output",
        "timeout": 10,
    }


def merge_hook(config, script):
    """Add the hook, leaving every other setting untouched.

    Idempotent, and it upgrades an existing install in place. Appending
    blindly would eventually wrap a command twice, which double-filters the
    output and loses the exit code.
    """
    config = copy.deepcopy(config)
    entries = config.setdefault("hooks", {}).setdefault("PreToolUse", [])
    for entry in entries:
        for command in entry.get("hooks", []):
            if MARKER in command.get("command", ""):
                command.clear()
                command.update(hook_entry(script))
                entry["matcher"] = MATCHER
                return config
    entries.append({"matcher": MATCHER, "hooks": [hook_entry(script)]})
    return config


def remove_hook(config):
    """Take the hook out and leave settings.json as it was found, including
    dropping the containers that only existed to hold it."""
    config = copy.deepcopy(config)
    hooks = config.get("hooks")
    if not hooks:
        return config
    entries = hooks.get("PreToolUse", [])
    for entry in list(entries):
        kept = [c for c in entry.get("hooks", [])
                if MARKER not in c.get("command", "")]
        if kept:
            entry["hooks"] = kept
        else:
            entries.remove(entry)
    if not entries:
        hooks.pop("PreToolUse", None)
    if not hooks:
        config.pop("hooks", None)
    return config


def read_settings(path):
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_settings(path, config, backup):
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def report_shell():
    if shutil.which("pwsh") or shutil.which("powershell"):
        return
    print("  ! PowerShell was not found on PATH. This hook only matches the\n"
          "    PowerShell tool, so it will never fire without it.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=pathlib.Path, default=default_target(),
                        help="Claude Code config directory (default: ~/.claude)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change, write nothing")
    parser.add_argument("--no-backup", action="store_true",
                        help="do not copy settings.json aside first")
    args = parser.parse_args()

    here = pathlib.Path(__file__).resolve().parent / "src"
    settings = args.target / "settings.json"
    merged = merge_hook(read_settings(settings), str(args.target / MARKER))

    print(f"target      {args.target}")
    for name in SOURCES:
        print(f"  copy      {name}")
    print(f"  merge     {settings.name} "
          f"({'exists' if settings.exists() else 'new'})")

    if args.dry_run:
        print("\n-- dry run, nothing written --\n")
        print(json.dumps(merged["hooks"], indent=2))
        return

    args.target.mkdir(parents=True, exist_ok=True)
    for name in SOURCES:
        shutil.copy2(here / name, args.target / name)
    write_settings(settings, merged, backup=not args.no_backup)

    print("\ninstalled.")
    report_shell()
    print("  Open /hooks once, or start a new session, if it does not fire.")


if __name__ == "__main__":
    main()
