from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .bridge import (
    BridgeEventError,
    apply_bridge_event,
    build_export_bundle,
    iter_jsonl_source,
    load_export_bundle,
    parse_bridge_event,
    restore_state_from_bundle,
)
from .catalog import CatalogError, load_catalog
from .codex_events import (
    CodexEventAdapterError,
    default_cursor,
    load_cursor,
    resolve_session_inputs,
    save_cursor,
    scan_session_logs,
)
from .feedback import apply_evolution_feedback
from .paths import codex_home as resolve_codex_home
from .paths import default_state_path, repo_root as resolve_repo_root
from .overlay_state import global_state_path, is_tamacodex_selected, load_global_state, load_overlay_state, overlay_state_path, save_overlay_state
from .overlay_supervisor import ensure_overlay_supervisor, overlay_process_alive, pid_running, read_pid, stop_overlay_process, supervisor_pid_path
from .state import apply_event, apply_passive_rest, default_state, load_state, record_install_metadata, save_state
from .visual_state import derive_visual_state


def refresh_if_needed(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .watcher import refresh_if_needed as _refresh_if_needed

    return _refresh_if_needed(*args, **kwargs)


def build_codex_pet(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .pet_compiler import build_codex_pet as _build_codex_pet

    return _build_codex_pet(*args, **kwargs)


def install_codex_pet(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .pet_compiler import install_codex_pet as _install_codex_pet

    return _install_codex_pet(*args, **kwargs)


def run_preview_server(*args: Any, **kwargs: Any):
    from .preview_server import run_preview_server as _run_preview_server

    return _run_preview_server(*args, **kwargs)


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", help="Tamacodex repository root. Defaults to this source checkout.")
    parser.add_argument("--catalog-dir", help="M2.1-compatible catalog assets directory.")
    parser.add_argument("--codex-home", help="Codex home directory. Defaults to CODEX_HOME or ~/.codex.")
    parser.add_argument("--state-path", help="State ledger path. Defaults to <codex-home>/tamacodex/state.json.")


def state_catalog_dir(state_path: Path) -> str | None:
    if not state_path.exists():
        return None
    try:
        raw = json.loads(state_path.read_text(encoding="utf-8")).get("catalogDir")
    except (OSError, json.JSONDecodeError):
        return None
    return str(raw) if raw else None


def context(args: argparse.Namespace, use_state_catalog: bool = True):
    root = resolve_repo_root(args.repo_root)
    home = resolve_codex_home(args.codex_home)
    state_path = Path(args.state_path).expanduser().resolve() if args.state_path else default_state_path(home)
    catalog_dir = args.catalog_dir or (state_catalog_dir(state_path) if use_state_catalog else None)
    catalog = load_catalog(root, catalog_dir)
    return root, home, state_path, catalog


def remember_catalog_selection(state: dict[str, Any], catalog, args: argparse.Namespace) -> None:
    if getattr(args, "catalog_dir", None):
        state["catalogDir"] = str(catalog.root)
    else:
        state.setdefault("catalogDir", None)


def update_pet_selection(
    state: dict[str, Any],
    catalog,
    line: str | None = None,
    machine: str | None = None,
    form: str | None = None,
    display_name: str | None = None,
) -> None:
    if line:
        if line not in catalog.line_ids():
            raise ValueError(f"unknown line {line!r}; available: {', '.join(catalog.line_ids())}")
        state["lineId"] = line
    if machine:
        if machine not in catalog.machine_ids():
            raise ValueError(f"unknown machine {machine!r}; available: {', '.join(catalog.machine_ids())}")
        state["machineId"] = machine
    if form:
        info = catalog.form_info(form)
        state["lineId"] = info["lineId"]
        state["lifeStage"] = info["stage"]
        state["branch"] = info.get("branch")
        state["formId"] = form
    else:
        branch = state.get("branch") if state["lifeStage"] in {"teen", "adult"} else None
        try:
            state["formId"] = catalog.find_form(state["lineId"], state["lifeStage"], branch)
        except CatalogError:
            candidates = [
                candidate
                for candidate in catalog.form_ids(state["lineId"])
                if catalog.form_info(candidate).get("stage") == state["lifeStage"]
            ]
            if candidates:
                state["formId"] = sorted(candidates)[0]
                state["branch"] = catalog.form_info(state["formId"]).get("branch")
            else:
                state["lifeStage"] = "egg"
                state["branch"] = None
                state["formId"] = catalog.find_form(state["lineId"], "egg")
    if display_name:
        state["displayName"] = display_name


def print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def human_status(state: dict[str, Any]) -> str:
    stats = state["stats"]
    traits = state["traits"]
    counters = state["counters"]
    visual = derive_visual_state(state)
    branch = f"/{state['branch']}" if state.get("branch") else ""
    return "\n".join(
        [
            f"{state['displayName']} [{state['formId']}]",
            f"stage: {state['lifeStage']}{branch}  level: {state['level']}  xp: {state['xp']}  codex-state: {state['lastCodexState']}",
            f"energy: {stats['energy']}  mood: {stats['mood']}  health: {stats['health']}  bond: {stats['bond']}  mess: {stats['mess']}",
            f"visual: energy={visual['energy']}  satiety={visual['satiety']}  health={visual['health']}  bond={visual['bond']}  mess={visual['mess']}  alert={visual['alert']}",
            f"focus: {traits['focus']}  resilience: {traits['resilience']}  restlessness: {traits['restlessness']}  care: {traits['care']}",
            f"completed: {counters['completedRuns']}  failed: {counters['failedRuns']}  idle-min: {counters['idleMinutes']}  reviews: {counters['reviews']}",
        ]
    )


def cmd_init(args: argparse.Namespace) -> None:
    _root, _home, state_path, catalog = context(args, use_state_catalog=False)
    if state_path.exists() and not args.force:
        raise SystemExit(f"{state_path} already exists; pass --force to overwrite")
    state = default_state(catalog, line_id=args.line, machine_id=args.machine, display_name=args.display_name)
    remember_catalog_selection(state, catalog, args)
    save_state(state_path, state)
    print_json({"ok": True, "statePath": str(state_path), "catalog": str(catalog.root), "formId": state["formId"], "machineId": state["machineId"]})


def cmd_status(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    rest = apply_passive_rest(state, catalog)
    if rest["applied"]:
        state = rest["state"]
        save_state(state_path, state, touch=False)
        refresh_if_needed(catalog, state_path, home, home / "tamacodex" / "build")
        state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    state["formId"] = catalog.find_form(state["lineId"], state["lifeStage"], state.get("branch") if state["lifeStage"] in {"teen", "adult"} else None)
    if args.json:
        print_json({"ok": True, "statePath": str(state_path), "state": state, "visualState": derive_visual_state(state)})
    else:
        print(human_status(state))


def cmd_event(args: argparse.Namespace) -> None:
    if args.jsonl:
        if args.event:
            raise SystemExit("event name cannot be combined with --jsonl")
        args.input = args.jsonl
        args.follow = False
        args.poll_interval = 0.5
        args.refresh = args.install
        args.force = True
        args.strict = False
        cmd_bridge(args)
        return
    if not args.event:
        raise SystemExit("event name is required unless --jsonl is used")
    _root, home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    rest = apply_passive_rest(state, catalog)
    if rest["applied"]:
        state = rest["state"]
    remember_catalog_selection(state, catalog, args)
    result = apply_event(state, catalog, args.event, amount=args.amount)
    save_state(state_path, result["state"])
    install_report = None
    feedback_report = None
    if args.install:
        previous_form = state.get("lastInstalledFormId")
        build_dir = home / "tamacodex" / "build"
        install_report = install_codex_pet(catalog, result["state"], home, build_dir, force=True)
        record_install_metadata(result["state"], install_report)
        save_state(state_path, result["state"])
        if result["evolution"]["evolved"] and previous_form and previous_form != result["state"]["formId"]:
            feedback_report = apply_evolution_feedback(home, previous_form, result["state"]["formId"])
    if args.json:
        print_json({"ok": True, **result, "install": install_report, "evolutionFeedback": feedback_report})
    else:
        evo = result["evolution"]
        print(human_status(result["state"]))
        if evo["evolved"]:
            print(f"evolved: {evo['from']} -> {evo['to']}")
        if install_report:
            print(f"installed: {install_report['petDir']}")


def cmd_install(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line or "toast", machine_id=args.machine or "aurora")
    remember_catalog_selection(state, catalog, args)
    update_pet_selection(state, catalog, line=args.line, machine=args.machine, form=args.form, display_name=args.display_name)
    build_dir = Path(args.build_dir).expanduser().resolve() if args.build_dir else home / "tamacodex" / "build"
    report = install_codex_pet(catalog, state, home, build_dir, force=args.force)
    record_install_metadata(state, report)
    save_state(state_path, state)
    print_json({"ok": True, "statePath": str(state_path), **report})


def cmd_setup(args: argparse.Namespace) -> None:
    root, home, state_path, catalog = context(args, use_state_catalog=not args.reset)
    line = args.line or "toast"
    machine = args.machine or "aurora"
    if args.reset or not state_path.exists():
        state = default_state(catalog, line_id=line, machine_id=machine, display_name=args.display_name or "Tamacodex")
    else:
        state = load_state(state_path, catalog, line_id=line, machine_id=machine)
    update_pet_selection(state, catalog, line=args.line, machine=args.machine, form=args.form, display_name=args.display_name)
    remember_catalog_selection(state, catalog, args)
    save_state(state_path, state)
    build_dir = Path(args.build_dir).expanduser().resolve() if args.build_dir else home / "tamacodex" / "build"
    refresh = refresh_if_needed(
        catalog,
        state_path,
        home,
        build_dir,
        line_id=state["lineId"],
        machine_id=state["machineId"],
        force=args.force,
        catalog_dir=str(catalog.root) if getattr(args, "catalog_dir", None) else None,
    )
    installed_state = load_state(state_path, catalog, line_id=state["lineId"], machine_id=state["machineId"])
    response = {
        "ok": True,
        "statePath": str(state_path),
        "catalog": str(catalog.root),
        "state": {
            "displayName": installed_state["displayName"],
            "lineId": installed_state["lineId"],
            "machineId": installed_state["machineId"],
            "formId": installed_state["formId"],
            "lifeStage": installed_state["lifeStage"],
            "branch": installed_state.get("branch"),
            "level": installed_state["level"],
            "xp": installed_state["xp"],
        },
        "refresh": refresh,
    }
    if args.overlay_supervisor:
        response["overlaySupervisor"] = setup_overlay_supervisor(home, root)
    else:
        response["overlaySupervisor"] = {
            "ok": True,
            "skipped": True,
            "reason": "disabled",
        }
    if args.json:
        print_json(response)
    else:
        print(human_status(installed_state))
        if refresh["refreshed"]:
            print(f"installed: {refresh['install']['petDir']}")
        else:
            print(f"installed: already up to date in {home / 'pets' / installed_state.get('petId', 'tamacodex')}")


def setup_overlay_supervisor(
    home: Path,
    root: Path,
    installer=ensure_overlay_supervisor,
) -> dict[str, Any]:
    state_path = global_state_path(home)
    try:
        report = installer(home, root=root)
    except Exception as exc:  # noqa: BLE001
        report = {"ok": False, "error": str(exc)}
    if isinstance(report, dict):
        report.setdefault("globalStatePath", str(state_path))
        report.setdefault("globalStatePresent", state_path.exists())
    return report


def cmd_build(args: argparse.Namespace) -> None:
    _root, _home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    if args.form:
        info = catalog.form_info(args.form)
        state["lineId"] = info["lineId"]
        state["lifeStage"] = info["stage"]
        state["branch"] = info.get("branch")
        state["formId"] = args.form
    output_dir = Path(args.output_dir).expanduser().resolve()
    report = build_codex_pet(catalog, state, output_dir, form_id=args.form, machine_id=args.machine)
    print_json({"ok": True, "buildReport": str(output_dir / "build_report.json"), "report": report})


def cmd_preview(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    remember_catalog_selection(state, catalog, args)
    save_state(state_path, state)
    server, url = run_preview_server(catalog, state_path, home, args.host, args.port)
    print(f"Tamacodex preview running at {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def cmd_watch(args: argparse.Namespace) -> None:
    root = resolve_repo_root(args.repo_root)
    home = resolve_codex_home(args.codex_home)
    state_path = Path(args.state_path).expanduser().resolve() if args.state_path else default_state_path(home)
    build_dir = Path(args.build_dir).expanduser().resolve() if args.build_dir else home / "tamacodex" / "build"
    interval = max(0.2, args.interval)
    while True:
        catalog_dir = args.catalog_dir or state_catalog_dir(state_path)
        catalog = load_catalog(root, catalog_dir)
        report = refresh_if_needed(
            catalog,
            state_path,
            home,
            build_dir,
            line_id=args.line,
            machine_id=args.machine,
            force=args.force,
            catalog_dir=str(catalog.root) if args.catalog_dir else None,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False), flush=True)
        elif report["refreshed"]:
            reasons = ", ".join(report["reasons"])
            print(f"refreshed {report['formId']} on {report['machineId']} ({reasons})", flush=True)
        elif args.once:
            print(f"up to date: {report['formId']} on {report['machineId']}", flush=True)
        if args.once:
            return
        time.sleep(interval)


def cmd_overlay(args: argparse.Namespace) -> None:
    root = resolve_repo_root(args.repo_root)
    home = resolve_codex_home(args.codex_home)
    path = overlay_state_path(home)
    state = load_overlay_state(path)
    changed = False
    report: dict[str, Any] = {"ok": True, "overlayState": str(path)}

    if args.action == "mute":
        state["muted"] = True
        changed = True
    elif args.action == "unmute":
        state["muted"] = False
        changed = True
    elif args.action == "quiet":
        state["quietMode"] = True
        changed = True
    elif args.action == "normal":
        state["quietMode"] = False
        changed = True
    elif args.action == "stop":
        report["stopped"] = stop_overlay_process(home)
    elif args.action == "start":
        report["supervisor"] = ensure_overlay_supervisor(home, root=root)

    if changed:
        save_overlay_state(path, state)

    global_state = load_global_state(home)
    sidecar_pid = overlay_process_alive(home)
    state = load_overlay_state(path)
    supervisor_pid = state.get("supervisorPid") if isinstance(state.get("supervisorPid"), int) else read_pid(supervisor_pid_path(home))
    if supervisor_pid and not pid_running(supervisor_pid):
        supervisor_pid = None
        state["supervisorPid"] = None
        save_overlay_state(path, state)
    report.update(
        {
            "selected": is_tamacodex_selected(global_state),
            "muted": bool(state.get("muted")),
            "quietMode": bool(state.get("quietMode")),
            "sidecarPid": sidecar_pid,
            "supervisorPid": supervisor_pid,
            "lastSeenEventId": state.get("lastSeenEventId"),
            "lastPlayedEventId": state.get("lastPlayedEventId"),
        }
    )
    print_json(report)


def emit_bridge_result(result: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    elif result.get("ok"):
        print(result["message"], flush=True)
    else:
        print(f"skipped line {result.get('line')}: {result.get('error')}", file=sys.stderr, flush=True)


def cmd_bridge(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    build_dir = Path(args.build_dir).expanduser().resolve() if getattr(args, "build_dir", None) else home / "tamacodex" / "build"
    catalog_dir = str(catalog.root) if getattr(args, "catalog_dir", None) else None
    for line_number, line in iter_jsonl_source(args.input, follow=args.follow, poll_interval=args.poll_interval, stdin=sys.stdin):
        try:
            record = parse_bridge_event(line, line_number=line_number)
            if record is None:
                continue
            result = apply_bridge_event(
                catalog,
                state_path,
                record,
                line_id=args.line,
                machine_id=args.machine,
                catalog_dir=catalog_dir,
            )
            if args.refresh:
                result["refresh"] = refresh_if_needed(
                    catalog,
                    state_path,
                    home,
                    build_dir,
                    line_id=args.line,
                    machine_id=args.machine,
                    force=args.force,
                    catalog_dir=catalog_dir,
                )
            emit_bridge_result(result, args.json)
        except (BridgeEventError, CatalogError, RuntimeError, ValueError) as exc:
            result = {"ok": False, "schema": "tamacodex.bridge.event.v1", "line": line_number, "error": str(exc)}
            emit_bridge_result(result, args.json)
            if args.strict:
                raise SystemExit(1) from exc


def cmd_codex_events(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    build_dir = Path(args.build_dir).expanduser().resolve() if args.build_dir else home / "tamacodex" / "build"
    sessions_root = Path(args.sessions_root).expanduser().resolve() if args.sessions_root else home / "sessions"
    cursor_path = Path(args.cursor).expanduser().resolve() if args.cursor else home / "tamacodex" / "codex-events-cursor.json"
    output_jsonl = Path(args.output_jsonl).expanduser().resolve() if args.output_jsonl else None
    cursor = default_cursor() if args.no_cursor else load_cursor(cursor_path)
    persist_cursor = not args.no_cursor and not args.dry_run

    try:
        while True:
            paths = resolve_session_inputs(args.input, sessions_root)
            records = scan_session_logs(paths, cursor, backfill=args.backfill or args.no_cursor)
            if output_jsonl:
                output_jsonl.parent.mkdir(parents=True, exist_ok=True)
                with output_jsonl.open("a", encoding="utf-8") as handle:
                    for record in records:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            for record in records:
                if args.dry_run:
                    payload = {"ok": True, "schema": record["schema"], "event": record["event"], "record": record}
                    if args.json:
                        print(json.dumps(payload, ensure_ascii=False), flush=True)
                    else:
                        print(json.dumps(record, ensure_ascii=False), flush=True)
                    continue
                result = apply_bridge_event(
                    catalog,
                    state_path,
                    record,
                    line_id=args.line,
                    machine_id=args.machine,
                    catalog_dir=str(catalog.root) if getattr(args, "catalog_dir", None) else None,
                )
                if args.refresh:
                    result["refresh"] = refresh_if_needed(
                        catalog,
                        state_path,
                        home,
                        build_dir,
                        line_id=args.line,
                        machine_id=args.machine,
                        force=args.force,
                        catalog_dir=str(catalog.root) if getattr(args, "catalog_dir", None) else None,
                    )
                emit_bridge_result(result, args.json)

            if persist_cursor:
                save_cursor(cursor_path, cursor)
            if not args.follow:
                return
            time.sleep(max(0.2, args.poll_interval))
    except KeyboardInterrupt:
        if persist_cursor:
            save_cursor(cursor_path, cursor)


def cmd_export(args: argparse.Namespace) -> None:
    root, _home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    bundle = build_export_bundle(root, catalog, state, include_profiles=not args.no_profiles)
    if args.output == "-":
        print_json(bundle)
        return
    output = Path(args.output).expanduser().resolve()
    write_json(output, bundle)
    print_json({"ok": True, "output": str(output), "statePath": str(state_path), "profiles": len(bundle.get("profiles", {}))})


def cmd_import(args: argparse.Namespace) -> None:
    root = resolve_repo_root(args.repo_root)
    home = resolve_codex_home(args.codex_home)
    state_path = Path(args.state_path).expanduser().resolve() if args.state_path else default_state_path(home)
    bundle = load_export_bundle(Path(args.input).expanduser().resolve())
    state_catalog = bundle["state"].get("catalogDir") or bundle.get("catalog", {}).get("root")
    catalog_dir = args.catalog_dir
    if not catalog_dir and state_catalog and Path(str(state_catalog)).expanduser().exists():
        catalog_dir = str(Path(str(state_catalog)).expanduser().resolve())
    catalog = load_catalog(root, catalog_dir)
    if state_path.exists() and not args.force:
        raise SystemExit(f"{state_path} already exists; pass --force to overwrite")
    state = restore_state_from_bundle(bundle, catalog)
    if args.catalog_dir:
        state["catalogDir"] = str(catalog.root)
    save_state(state_path, state, touch=False)

    restored_profiles = 0
    if args.profiles:
        profile_dir = Path(args.profile_dir).expanduser().resolve() if args.profile_dir else root / "tamacodex_gen" / "profiles"
        profile_dir.mkdir(parents=True, exist_ok=True)
        for name, profile in sorted(bundle.get("profiles", {}).items()):
            target = profile_dir / Path(name).name
            if target.exists() and not args.force:
                raise SystemExit(f"{target} already exists; pass --force to overwrite profiles")
            write_json(target, profile)
            restored_profiles += 1

    print_json({"ok": True, "statePath": str(state_path), "catalog": str(catalog.root), "profiles": restored_profiles})


def cmd_list_forms(args: argparse.Namespace) -> None:
    _root, _home, _state_path, catalog = context(args)
    print_json(
        {
            "ok": True,
            "catalog": str(catalog.root),
            "lines": catalog.line_ids(),
            "machines": catalog.machine_ids(),
            "forms": catalog.form_ids(args.line),
        }
    )


def cmd_generate_profile(args: argparse.Namespace) -> None:
    root = resolve_repo_root(args.repo_root)
    script = root / "tamacodex_gen" / "scripts" / "prompt_to_profile.py"
    command = [sys.executable, str(script)]
    if args.prompt:
        command.extend(["--prompt", args.prompt])
    if args.brief_output:
        command.extend(["--brief-output", str(Path(args.brief_output).expanduser().resolve())])
    if args.input:
        command.extend(["--input", str(Path(args.input).expanduser().resolve())])
    if args.profile_json:
        command.extend(["--profile-json", args.profile_json])
    if args.output:
        command.extend(["--output", str(Path(args.output).expanduser().resolve())])
    completed = subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)
    try:
        response: dict[str, Any] = json.loads(completed.stdout)
    except json.JSONDecodeError:
        response = {"ok": True}
    if args.output:
        response["profile"] = str(Path(args.output).expanduser().resolve())
    if args.brief_output:
        response["brief"] = str(Path(args.brief_output).expanduser().resolve())
    print_json(response)


def cmd_render_catalog(args: argparse.Namespace) -> None:
    root = resolve_repo_root(args.repo_root)
    script = root / "tamacodex_gen" / "scripts" / "render_catalog.py"
    output = Path(args.output_dir).expanduser().resolve()
    command = [
        sys.executable,
        str(script),
        "--milestone",
        args.milestone,
        "--asset-version",
        args.asset_version,
        "--output-dir",
        str(output),
    ]
    for profile in args.profile:
        command.extend(["--profile", str(Path(profile).expanduser().resolve())])
    subprocess.run(command, check=True, text=True, stdout=subprocess.PIPE)
    print_json({"ok": True, "catalogDir": str(output / "assets")})


def cmd_doctor(args: argparse.Namespace) -> None:
    _root, home, state_path, catalog = context(args)
    state = load_state(state_path, catalog, line_id=args.line, machine_id=args.machine)
    build_dir = home / "tamacodex" / "doctor"
    report = build_codex_pet(catalog, state, build_dir)
    print_json({"ok": True, "statePath": str(state_path), "catalog": str(catalog.root), "buildReport": str(build_dir / "build_report.json"), "report": report})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tamacodex", description="Tamacodex runtime and Codex custom pet installer.")
    add_common(parser)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create or reset the Tamacodex state ledger.")
    init.add_argument("--line", default="toast")
    init.add_argument("--machine", default="aurora")
    init.add_argument("--display-name", default="Tamacodex")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="Show growth and care state.")
    status.add_argument("--line", default="toast")
    status.add_argument("--machine", default="aurora")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    event = sub.add_parser("event", help="Apply a Codex-like usage or care event.")
    event.add_argument("event", nargs="?")
    event.add_argument("--amount", type=int, default=1)
    event.add_argument("--line", default="toast")
    event.add_argument("--machine", default="aurora")
    event.add_argument("--install", action="store_true", help="Rebuild and reinstall after the event.")
    event.add_argument("--jsonl", nargs="?", const="-", help="Read bridge event JSONL from a file or stdin.")
    event.add_argument("--json", action="store_true")
    event.set_defaults(func=cmd_event)

    install = sub.add_parser("install", help="Build and install the current form as a Codex custom pet.")
    install.add_argument("--line")
    install.add_argument("--machine")
    install.add_argument("--form")
    install.add_argument("--display-name")
    install.add_argument("--build-dir")
    install.add_argument("--force", action="store_true")
    install.set_defaults(func=cmd_install)

    setup = sub.add_parser("setup", help="Install or switch Tamacodex for Codex App in one step.")
    setup.add_argument("--line")
    setup.add_argument("--machine")
    setup.add_argument("--form")
    setup.add_argument("--display-name")
    setup.add_argument("--build-dir")
    setup.add_argument("--force", action="store_true", help="Replace an existing package if needed.")
    setup.add_argument("--reset", action="store_true", help="Reset the growth ledger before installing.")
    setup.add_argument("--no-overlay-supervisor", dest="overlay_supervisor", action="store_false", help="Skip installing the M10 sidecar overlay supervisor.")
    setup.set_defaults(overlay_supervisor=True)
    setup.add_argument("--json", action="store_true")
    setup.set_defaults(func=cmd_setup)

    build = sub.add_parser("build", help="Build a Codex pet package without installing it.")
    build.add_argument("--line", default="toast")
    build.add_argument("--machine", default="aurora")
    build.add_argument("--form")
    build.add_argument("--output-dir", required=True)
    build.set_defaults(func=cmd_build)

    preview = sub.add_parser("preview", help="Run the draggable local preview with hover status.")
    preview.add_argument("--host", default="127.0.0.1")
    preview.add_argument("--port", type=int, default=8765)
    preview.add_argument("--line", default="toast")
    preview.add_argument("--machine", default="aurora")
    preview.set_defaults(func=cmd_preview)

    watch = sub.add_parser("watch", help="Poll the state ledger and refresh the installed Codex pet when it changes.")
    watch.add_argument("--line", default="toast")
    watch.add_argument("--machine", default="aurora")
    watch.add_argument("--build-dir")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--once", action="store_true", help="Check once and exit.")
    watch.add_argument("--force", action="store_true", help="Allow replacing an existing package when no prior install is recorded.")
    watch.add_argument("--json", action="store_true")
    watch.set_defaults(func=cmd_watch)

    overlay = sub.add_parser("overlay", help="Control the M10 sidecar overlay and SFX.")
    overlay.add_argument("action", choices=["status", "mute", "unmute", "quiet", "normal", "start", "stop"], nargs="?", default="status")
    overlay.set_defaults(func=cmd_overlay)

    bridge = sub.add_parser("bridge", help="Read plugin/automation-friendly event JSONL and update the growth ledger.")
    bridge.add_argument("--input", default="-", help="JSONL event file, or '-' for stdin.")
    bridge.add_argument("--follow", action="store_true", help="Keep waiting for new lines from the input.")
    bridge.add_argument("--poll-interval", type=float, default=0.5)
    bridge.add_argument("--line", default="toast")
    bridge.add_argument("--machine", default="aurora")
    bridge.add_argument("--build-dir")
    bridge.add_argument("--refresh", action="store_true", help="Refresh the installed pet package after each accepted event.")
    bridge.add_argument("--force", action="store_true", help="Allow refresh to replace an existing package when no prior install is recorded.")
    bridge.add_argument("--strict", action="store_true", help="Exit non-zero after the first invalid JSONL record.")
    bridge.add_argument("--json", action="store_true")
    bridge.set_defaults(func=cmd_bridge)

    codex_events = sub.add_parser("codex-events", help="Consume Codex session rollout logs through the bridge contract.")
    codex_events.add_argument("--input", action="append", help="Specific rollout JSONL file or sessions directory. Defaults to <codex-home>/sessions.")
    codex_events.add_argument("--sessions-root", help="Codex sessions root. Defaults to <codex-home>/sessions.")
    codex_events.add_argument("--cursor", help="Cursor path. Defaults to <codex-home>/tamacodex/codex-events-cursor.json.")
    codex_events.add_argument("--no-cursor", action="store_true", help="Do not read or write a cursor; useful for tests and one-off backfills.")
    codex_events.add_argument("--backfill", action="store_true", help="When a file has no cursor yet, process existing lines instead of starting at EOF.")
    codex_events.add_argument("--follow", action="store_true", help="Keep polling session logs for new Codex events.")
    codex_events.add_argument("--poll-interval", type=float, default=1.0)
    codex_events.add_argument("--line", default="toast")
    codex_events.add_argument("--machine", default="aurora")
    codex_events.add_argument("--build-dir")
    codex_events.add_argument("--output-jsonl", help="Append normalized bridge JSONL records to this file.")
    codex_events.add_argument("--dry-run", action="store_true", help="Emit normalized bridge records without changing the state ledger.")
    codex_events.add_argument("--refresh", action="store_true", help="Refresh the installed pet package after each accepted event.")
    codex_events.add_argument("--force", action="store_true", help="Allow refresh to replace an existing package when no prior install is recorded.")
    codex_events.add_argument("--json", action="store_true")
    codex_events.set_defaults(func=cmd_codex_events)

    list_forms = sub.add_parser("list-forms", help="List catalog lines, machines, and forms.")
    list_forms.add_argument("--line")
    list_forms.set_defaults(func=cmd_list_forms)

    generate = sub.add_parser("generate-profile", help="Validate a Codex-agent-authored tamacodex_gen profile.")
    generate.add_argument("--prompt", help="User prose prompt, used only to emit a Codex agent brief.")
    generate.add_argument("--brief-output", help="Write a Codex agent brief Markdown file for --prompt.")
    generate.add_argument("--input", help="Codex-agent-authored profile JSON file.")
    generate.add_argument("--profile-json", help="Codex-agent-authored profile JSON object, or @path.")
    generate.add_argument("--output", help="Destination normalized profile JSON path.")
    generate.set_defaults(func=cmd_generate_profile)

    render = sub.add_parser("render-catalog", help="Render a M2.1-compatible catalog with tamacodex_gen.")
    render.add_argument("--profile", action="append", required=True)
    render.add_argument("--output-dir", required=True)
    render.add_argument("--milestone", default="M2.1")
    render.add_argument("--asset-version", default="m2.1")
    render.set_defaults(func=cmd_render_catalog)

    doctor = sub.add_parser("doctor", help="Compile and validate the current state without installing.")
    doctor.add_argument("--line", default="toast")
    doctor.add_argument("--machine", default="aurora")
    doctor.set_defaults(func=cmd_doctor)

    export = sub.add_parser("export", help="Export the state ledger plus catalog/profile metadata as JSON.")
    export.add_argument("--output", required=True, help="Destination JSON path, or '-' for stdout.")
    export.add_argument("--line", default="toast")
    export.add_argument("--machine", default="aurora")
    export.add_argument("--no-profiles", action="store_true", help="Skip tamacodex_gen profile JSON in the export bundle.")
    export.set_defaults(func=cmd_export)

    import_bundle = sub.add_parser("import", help="Import a Tamacodex export bundle into the state ledger.")
    import_bundle.add_argument("--input", required=True)
    import_bundle.add_argument("--profiles", action="store_true", help="Also restore profile JSON files from the bundle.")
    import_bundle.add_argument("--profile-dir")
    import_bundle.add_argument("--force", action="store_true")
    import_bundle.set_defaults(func=cmd_import)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except (BridgeEventError, CodexEventAdapterError, CatalogError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"tamacodex: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
