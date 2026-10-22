"""Command-line entry points (GUI imports are lazy)."""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__


def check_claude(settings=None) -> dict:
    import time
    from .claude_provider import ClaudeProvider
    from .claude_state import automatic_diagnosis
    from .models import snapshot
    from .storage import load_settings
    settings = load_settings() if settings is None else settings
    if not settings["claude_auto_query"]:
        return automatic_diagnosis(settings)
    provider = ClaudeProvider(settings["claude_binary"], timeout=25)
    attempted = time.time()
    try:
        observation = provider.fetch()
    except Exception:
        observation = snapshot("claude", "error", "Claude usage could not be read. Check the connection and settings.")
        observation.update(source_kind="direct", error_code="query_failed")
    finally:
        provider.close()
    observation["last_attempt_at"] = attempted
    return automatic_diagnosis(settings, observation)


def check() -> int:
    from concurrent.futures import ThreadPoolExecutor
    from .codex_provider import CodexProvider, ProviderError, locate_codex
    from .desktop import autostart_enabled
    from .storage import load_settings
    settings = load_settings()
    result = {"app_version": __version__, "python_version": sys.version.split()[0],
              "codex_found": bool(locate_codex(settings["codex_binary"])),
              "autostart_enabled": autostart_enabled()}
    def codex_check():
        provider = CodexProvider(settings["codex_binary"], timeout=10)
        try:
            observation = provider.fetch()
            return {"codex_state": observation["state"], "codex_message": observation["message"],
                    "codex_weekly_windows": sum(w["kind"] == "weekly" for w in observation["windows"])}
        except Exception as exc:
            return {"codex_state": "error", "codex_message": str(exc) if isinstance(exc, ProviderError)
                    else "Codex usage could not be read."}
        finally:
            provider.close()
    with ThreadPoolExecutor(max_workers=2) as workers:
        codex_result = workers.submit(codex_check)
        claude_result = workers.submit(check_claude, settings)
        result.update(codex_result.result())
        result["claude"] = claude_result.result()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("codex_state") == "ok" and result["claude"]["state"] == "ok" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="quota-widget", description="Weekly Codex and Claude usage widget for Ubuntu")
    parser.add_argument("--version", action="version", version=__version__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install-claude", action="store_true", help="Install the optional Claude session connection (not needed for automatic queries)")
    action.add_argument("--uninstall-claude", action="store_true", help="Remove the Claude session connection and restore the previous status line")
    action.add_argument("--claude-statusline", action="store_true", help=argparse.SUPPRESS)
    action.add_argument("--check", action="store_true", help="Diagnose connections without the GUI (no model requests)")
    action.add_argument("--check-claude", action="store_true", help="Diagnose automatic Claude queries or the configured session connection (no model requests)")
    action.add_argument("--check-gui", action="store_true", help="Check display and XKB configuration without starting the GUI")
    action.add_argument("--autostart", choices=("on", "off"), help="Set whether the widget starts at login")
    parser.add_argument("--demo", action="store_true", help="Show clearly labeled example data without connecting accounts")
    parser.add_argument("--from-autostart", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--screenshot", metavar="PNG", help="Save the --demo screen as a PNG and exit")
    args = parser.parse_args(argv)
    try:
        if args.claude_statusline:
            from .claude_bridge import run_statusline
            return run_statusline()
        if args.install_claude or args.uninstall_claude:
            from .claude_bridge import install_bridge, uninstall_bridge
            print(install_bridge() if args.install_claude else uninstall_bridge())
            return 0
        if args.autostart:
            from .desktop import autostart
            autostart(args.autostart == "on")
            print("Start at login is " + ("enabled." if args.autostart == "on" else "disabled."))
            return 0
        if args.check:
            return check()
        if args.check_claude:
            result = check_claude()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["state"] == "ok" else 1
        if args.check_gui:
            from .startup import gui_diagnostics
            result = gui_diagnostics()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["detected_xkb_root"] else 1
        if args.screenshot and not args.demo:
            parser.error("Use --screenshot with --demo to protect account information.")
        if args.screenshot:
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from .startup import prepare_gui_environment
        prepare_gui_environment()
        from .app import run
        return run(demo=args.demo, screenshot=args.screenshot, from_autostart=args.from_autostart)
    except ImportError as exc:
        print("A required package is missing. Reinstall Quota Dock. " + str(exc), file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
