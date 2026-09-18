from __future__ import annotations

import argparse
import os
import plistlib
import signal
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from .overlay_state import (
    global_state_path,
    is_tamahermes_selected,
    load_overlay_state,
    overlay_pid_path,
    overlay_should_run,
    overlay_state_fingerprint,
    overlay_state_path,
    read_json_object,
    save_overlay_state,
    supervisor_pid_path,
    update_surface_activity,
)
from .paths import codex_home as resolve_codex_home
from .paths import repo_root as resolve_repo_root

LAUNCH_AGENT_LABEL = "com.autoark.tamahermes.overlay-supervisor"
MIN_RESTART_SECONDS = 3.0

# Claim outcomes. A live pid we cannot identify is never trusted and never
# signalled; a live peer that really is our supervisor is a duplicate instance.
CLAIMED = "CLAIMED"
RECOVERED_STALE = "RECOVERED_STALE"
DUPLICATE_PEER = "DUPLICATE_PEER"
UNVERIFIED_PEER = "UNVERIFIED_PEER"

# Exit codes (sysexits): non-zero so a fault surfaces in the launchd log and can
# be retried instead of a silent clean exit that KeepAlive will not relaunch.
EXIT_SOFTWARE = 70
EXIT_TEMPFAIL = 75
EXIT_CONFIG = 78

SUPERVISOR_SIGNATURE = "supervisor"
SIDECAR_SIGNATURE = "sidecar"
FOREIGN_SIGNATURE = "foreign"
UNKNOWN_SIGNATURE = "unknown"

# Order matters: "tamahermes.overlay_supervisor" also contains
# "tamahermes.overlay", so the supervisor marker is tested first.
SUPERVISOR_MARKER = "tamahermes.overlay_supervisor"
SIDECAR_MARKER = "tamahermes.overlay"


def log(message: str) -> None:
    """One observable line per event: the supervisor logs were empty before."""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    print(f"{stamp} overlay-supervisor[{os.getpid()}] {message}", file=sys.stderr, flush=True)


def pid_identity(pid: int, runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run) -> str | None:
    """The pid's command line, or None when it cannot be read in time."""
    if pid <= 0:
        return None
    ps = "/bin/ps" if Path("/bin/ps").exists() else "ps"
    try:
        completed = runner([ps, "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=0.5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    command = (completed.stdout or "").strip()
    return command or None


def pid_signature(pid: int, identity_reader: Callable[[int], str | None] = pid_identity) -> str:
    """Classify a pid by its command line: supervisor | sidecar | foreign | unknown."""
    identity = identity_reader(pid)
    if not identity or not identity.strip():
        return UNKNOWN_SIGNATURE
    if SUPERVISOR_MARKER in identity:
        return SUPERVISOR_SIGNATURE
    if SIDECAR_MARKER in identity:
        return SIDECAR_SIGNATURE
    return FOREIGN_SIGNATURE


def _pid_stat(pid: int, runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run) -> str | None:
    ps = "/bin/ps" if Path("/bin/ps").exists() else "ps"
    try:
        completed = runner([ps, "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=0.5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def pid_running(pid: int, stat_reader: Callable[[int], str | None] = _pid_stat) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    stat = stat_reader(pid)
    if stat and stat.upper().startswith("Z"):
        return False
    return True


def read_pid(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    try:
        pid = int(text)
    except ValueError:
        return None
    return pid if pid > 0 else None


def write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n", encoding="utf-8")


def clear_pid(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def claim_pid_file(
    path: Path,
    pid: int | None = None,
    is_running: Callable[[int], bool] = pid_running,
    identity_reader: Callable[[int], str | None] = pid_identity,
) -> str:
    """Claim the pid file after verifying the identity of a live claimant.

    Returns one of CLAIMED / RECOVERED_STALE / DUPLICATE_PEER / UNVERIFIED_PEER.
    A live pid whose signature is not our supervisor is a stale file from a
    reused pid, so it is self-healed; an unreadable pid is never trusted, and
    nothing here signals anything.
    """
    pid = pid or os.getpid()
    existing = read_pid(path)
    if not existing or existing == pid or not is_running(existing):
        write_pid(path, pid)
        return CLAIMED
    signature = pid_signature(existing, identity_reader=identity_reader)
    if signature == SUPERVISOR_SIGNATURE:
        return DUPLICATE_PEER
    if signature == UNKNOWN_SIGNATURE:
        return UNVERIFIED_PEER
    write_pid(path, pid)
    return RECOVERED_STALE


def record_supervisor_claim(home: Path, result: str, peer_pid: int | None) -> None:
    """Persist the claim outcome so the silent-exit defect stays observable."""
    state = load_overlay_state(overlay_state_path(home))
    state["supervisorClaim"] = {
        "result": result,
        "peerPid": peer_pid,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    save_overlay_state(overlay_state_path(home), state)


def stop_pid(
    pid: int,
    timeout: float = 1.5,
    is_running: Callable[[int], bool] = pid_running,
    killer: Callable[[int, int], None] | None = None,
) -> bool:
    if not is_running(pid):
        return True
    send = killer or os.kill
    try:
        send(pid, signal.SIGTERM)
    except OSError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running(pid):
            return True
        time.sleep(0.05)
    return not is_running(pid)


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def launch_agent_plist(home: Path, root: Path | None = None, python: str | None = None, interval: float = 1.0) -> dict[str, Any]:
    log_dir = Path.home() / "Library" / "Logs" / "TamaHermes"
    program_arguments = [
        python or sys.executable,
        "-m",
        "tamahermes.overlay_supervisor",
        "supervisor",
        "--codex-home",
        str(home),
        "--interval",
        f"{interval:.2f}",
    ]
    if root is not None:
        program_arguments.extend(["--repo-root", str(root)])
    environment = {"CODEX_HOME": str(home)}
    if root is not None:
        environment["PYTHONPATH"] = str(root)
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": program_arguments,
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(log_dir / "overlay-supervisor.log"),
        "StandardErrorPath": str(log_dir / "overlay-supervisor.err.log"),
        "EnvironmentVariables": environment,
    }


def write_launch_agent(home: Path, root: Path | None = None, python: str | None = None, interval: float = 1.0, path: Path | None = None) -> Path:
    target = path or launch_agent_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    (Path.home() / "Library" / "Logs" / "TamaHermes").mkdir(parents=True, exist_ok=True)
    plist = launch_agent_plist(home, root=root, python=python, interval=interval)
    target.write_bytes(plistlib.dumps(plist, sort_keys=False))
    return target


def load_launch_agent(path: Path, runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run) -> bool:
    if sys.platform != "darwin":
        return False
    launchctl = "/bin/launchctl"
    if not Path(launchctl).exists():
        return False
    domain = f"gui/{os.getuid()}"
    runner([launchctl, "bootout", domain, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    completed = runner([launchctl, "bootstrap", domain, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    if completed.returncode == 0:
        runner([launchctl, "enable", f"{domain}/{LAUNCH_AGENT_LABEL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        runner([launchctl, "kickstart", "-k", f"{domain}/{LAUNCH_AGENT_LABEL}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return True
    fallback = runner([launchctl, "load", "-w", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return fallback.returncode == 0


def install_launch_agent(
    home: Path,
    root: Path | None = None,
    python: str | None = None,
    interval: float = 1.0,
    load: bool = True,
) -> dict[str, Any]:
    path = write_launch_agent(home, root=root, python=python, interval=interval)
    loaded = load_launch_agent(path) if load else False
    return {"ok": True, "path": str(path), "label": LAUNCH_AGENT_LABEL, "loaded": loaded}


def overlay_process_alive(
    home: Path,
    is_running: Callable[[int], bool] = pid_running,
    identity_reader: Callable[[int], str | None] = pid_identity,
) -> int | None:
    """The live sidecar pid, or None. Only a verified sidecar counts."""
    state = load_overlay_state(overlay_state_path(home))
    candidates = [state.get("sidecarPid"), read_pid(overlay_pid_path(home))]
    for raw_pid in candidates:
        if (
            isinstance(raw_pid, int)
            and is_running(raw_pid)
            and pid_signature(raw_pid, identity_reader=identity_reader) == SIDECAR_SIGNATURE
        ):
            return raw_pid
    clear_pid(overlay_pid_path(home))
    if state.get("sidecarPid"):
        state["sidecarPid"] = None
        save_overlay_state(overlay_state_path(home), state)
    return None


def start_overlay_process(
    home: Path,
    root: Path | None = None,
    python: str | None = None,
    interval: float = 0.4,
    visual: bool = True,
    popen: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> int:
    log_dir = home / "tamahermes" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    command = [
        python or sys.executable,
        "-m",
        "tamahermes.overlay",
        "--codex-home",
        str(home),
        "--interval",
        f"{interval:.2f}",
    ]
    if root is not None:
        command.extend(["--repo-root", str(root)])
    if not visual:
        command.append("--headless")
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    if root is not None:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(root) if not existing else f"{root}{os.pathsep}{existing}"
    with (log_dir / "overlay.log").open("ab") as stdout, (log_dir / "overlay.err.log").open("ab") as stderr:
        process = popen(command, cwd=str(root) if root else None, env=env, stdout=stdout, stderr=stderr)
    write_pid(overlay_pid_path(home), process.pid)
    state = load_overlay_state(overlay_state_path(home))
    state["sidecarPid"] = process.pid
    save_overlay_state(overlay_state_path(home), state)
    return process.pid


def wait_for_running_pid(path: Path, timeout: float = 2.0, is_running: Callable[[int], bool] = pid_running) -> int | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pid = read_pid(path)
        if pid and is_running(pid):
            return pid
        time.sleep(0.05)
    pid = read_pid(path)
    return pid if pid and is_running(pid) else None


def stop_overlay_process(
    home: Path,
    is_running: Callable[[int], bool] = pid_running,
    identity_reader: Callable[[int], str | None] = pid_identity,
    killer: Callable[[int, int], None] | None = None,
) -> bool:
    """Stop the sidecar — and only the sidecar.

    A pid file or state value that does not verify as our sidecar (foreign,
    supervisor, or unreadable) is cleared without signalling anything: signalling
    a reused pid is the defect this hardening removes.
    """
    pid = read_pid(overlay_pid_path(home))
    state = load_overlay_state(overlay_state_path(home))
    if not pid and isinstance(state.get("sidecarPid"), int):
        pid = state["sidecarPid"]
    stopped = True
    if pid:
        if not is_running(pid):
            stopped = True
        elif pid_signature(pid, identity_reader=identity_reader) == SIDECAR_SIGNATURE:
            stopped = stop_pid(pid, is_running=is_running, killer=killer)
        else:
            stopped = False
    clear_pid(overlay_pid_path(home))
    state["sidecarPid"] = None
    save_overlay_state(overlay_state_path(home), state)
    return stopped


def native_pet_process_running() -> bool:
    """Return whether the EvoPet native runtime is currently alive."""
    ps = "/bin/ps" if Path("/bin/ps").exists() else "ps"
    try:
        result = subprocess.run([ps, "-axo", "command="], capture_output=True, text=True, timeout=0.5, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    for command in result.stdout.splitlines():
        if "petdex-desktop-native" in command or "PetdexDev" in command:
            if "/bin/bash -c" not in command and "python3 -c" not in command:
                return True
    return False


def selected_from_disk(home: Path) -> bool:
    return is_tamahermes_selected(read_json_object(global_state_path(home)))


def supervise_once(
    home: Path,
    root: Path | None = None,
    python: str | None = None,
    is_running: Callable[[int], bool] = pid_running,
    starter: Callable[[Path, Path | None, str | None], int] | None = None,
    stopper: Callable[[Path], bool] | None = None,
    app_running: Callable[[], bool] | None = None,
    identity_reader: Callable[[int], str | None] = pid_identity,
) -> dict[str, Any]:
    global_state = read_json_object(global_state_path(home))
    selected = is_tamahermes_selected(global_state)
    overlay_state = load_overlay_state(overlay_state_path(home))
    before = overlay_state_fingerprint(overlay_state)
    surface_active, _bounds = update_surface_activity(global_state, overlay_state, time.time())
    # Contract C1.1: the supervisor only rewrites overlay-state.json when
    # something it owns changed; the per-second unconditional save was one of
    # the two writers racing the sidecar's flip writes.
    if overlay_state_fingerprint(overlay_state) != before:
        save_overlay_state(overlay_state_path(home), overlay_state)
    # The pill and the hidden HUD are live surfaces: idle-stop may not reap them.
    should_run = overlay_should_run(global_state, overlay_state, time.time(), surface_active=surface_active)
    is_running = is_running or pid_running
    identity_reader = identity_reader or pid_identity
    running_pid = overlay_process_alive(home, is_running=is_running, identity_reader=identity_reader)
    live = should_run and (app_running() if app_running else True)
    if not live:
        stopped = True
        if running_pid:
            stopped = stopper(home) if stopper else stop_overlay_process(home, is_running=is_running, identity_reader=identity_reader)
        return {"selected": selected, "surfaceActive": surface_active, "runningPid": running_pid, "startedPid": None, "stopped": stopped}
    if running_pid:
        return {"selected": True, "surfaceActive": True, "runningPid": running_pid, "startedPid": None, "stopped": False}
    started_pid = starter(home, root, python) if starter else start_overlay_process(home, root=root, python=python)
    write_pid(overlay_pid_path(home), started_pid)
    state = load_overlay_state(overlay_state_path(home))
    state["sidecarPid"] = started_pid
    save_overlay_state(overlay_state_path(home), state)
    return {"selected": True, "surfaceActive": True, "runningPid": None, "startedPid": started_pid, "stopped": False}


def ensure_overlay_supervisor(
    home: Path,
    root: Path | None = None,
    python: str | None = None,
    install_agent: bool = True,
) -> dict[str, Any]:
    report: dict[str, Any] = {"ok": True, "launchAgent": None, "supervisorPid": None, "alreadyRunning": False}
    pid_path = supervisor_pid_path(home)
    if install_agent:
        try:
            report["launchAgent"] = install_launch_agent(home, root=root, python=python or sys.executable, load=True)
        except Exception as exc:  # noqa: BLE001
            report["launchAgent"] = {"ok": False, "error": str(exc)}
    if isinstance(report.get("launchAgent"), dict) and report["launchAgent"].get("loaded"):
        launched = wait_for_running_pid(pid_path)
        if launched:
            state = load_overlay_state(overlay_state_path(home))
            state["supervisorPid"] = launched
            save_overlay_state(overlay_state_path(home), state)
            report["supervisorPid"] = launched
            report["alreadyRunning"] = True
        return report
    existing = read_pid(pid_path)
    if existing and pid_running(existing):
        report["supervisorPid"] = existing
        report["alreadyRunning"] = True
        return report
    command = [
        python or sys.executable,
        "-m",
        "tamahermes.overlay_supervisor",
        "supervisor",
        "--codex-home",
        str(home),
    ]
    env = os.environ.copy()
    env["CODEX_HOME"] = str(home)
    if root is not None:
        existing_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(root) if not existing_path else f"{root}{os.pathsep}{existing_path}"
    log_dir = home / "tamahermes" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / "overlay-supervisor.log").open("ab") as stdout, (log_dir / "overlay-supervisor.err.log").open("ab") as stderr:
        process = subprocess.Popen(command, cwd=str(root) if root else None, env=env, stdout=stdout, stderr=stderr)
    write_pid(pid_path, process.pid)
    state = load_overlay_state(overlay_state_path(home))
    state["supervisorPid"] = process.pid
    save_overlay_state(overlay_state_path(home), state)
    report["supervisorPid"] = process.pid
    return report


def _home_usable(home: Path) -> bool:
    """Whether the supervisor can write its state under this codex home.

    Checked without side effects: a missing home is a config fault (78), and a
    supervisor must never invent the codex home it was pointed at.
    """
    target = home / "tamahermes"
    for candidate in (target, target.parent):
        if candidate.is_dir():
            return os.access(candidate, os.W_OK | os.X_OK)
    return False


def supervisor_loop(
    home: Path,
    root: Path | None = None,
    python: str | None = None,
    interval: float = 1.0,
    is_running: Callable[[int], bool] | None = None,
    identity_reader: Callable[[int], str | None] | None = None,
) -> int:
    # Late-bound so tests (and callers) can swap process identity reading
    # wholesale without ever probing a live pid.
    is_running = is_running or pid_running
    identity_reader = identity_reader or pid_identity
    pid_path = supervisor_pid_path(home)
    if not _home_usable(home):
        log(f"ERROR config: codex home not usable: {home}")
        return EXIT_CONFIG
    if root is not None and not root.is_dir():
        log(f"ERROR config: repo root not a directory: {root}")
        return EXIT_CONFIG
    claim = claim_pid_file(pid_path, is_running=is_running, identity_reader=identity_reader)
    if claim == UNVERIFIED_PEER:
        existing = read_pid(pid_path)
        log(f"ERROR {claim}: pid file holds {existing} which cannot be identified; exiting {EXIT_TEMPFAIL}")
        record_supervisor_claim(home, claim, existing)
        return EXIT_TEMPFAIL
    if claim == DUPLICATE_PEER:
        existing = read_pid(pid_path)
        log(f"WARNING {claim}: another supervisor ({existing}) already holds the pid file; exiting 0")
        record_supervisor_claim(home, claim, existing)
        return 0
    if claim == RECOVERED_STALE:
        log(f"WARNING {claim}: stale/reused pid found in {pid_path}; rewrote it for self ({os.getpid()})")
        record_supervisor_claim(home, claim, None)
    else:
        record_supervisor_claim(home, claim, None)
    state = load_overlay_state(overlay_state_path(home))
    state["supervisorPid"] = os.getpid()
    save_overlay_state(overlay_state_path(home), state)
    last_start = 0.0
    try:
        while True:
            global_state = read_json_object(global_state_path(home))
            overlay_state = load_overlay_state(overlay_state_path(home))
            before = overlay_state_fingerprint(overlay_state)
            surface_active, _bounds = update_surface_activity(global_state, overlay_state, time.time())
            # Contract C1.1: only persist when an owned key changed (see
            # supervise_once); observation timestamps are ignored by the
            # fingerprint, so an idle tick costs no write.
            if overlay_state_fingerprint(overlay_state) != before:
                save_overlay_state(overlay_state_path(home), overlay_state)
            app_running = native_pet_process_running()
            # D9.1: the collapsed pill / hidden toggle count as live surfaces; the
            # by-design idle child stop (EvoPet closed) is preserved as-is.
            should_run = overlay_should_run(global_state, overlay_state, time.time(), surface_active=surface_active)
            if should_run and app_running:
                running = overlay_process_alive(home, is_running=is_running, identity_reader=identity_reader)
                if not running and time.monotonic() - last_start >= MIN_RESTART_SECONDS:
                    start_overlay_process(home, root=root, python=python)
                    last_start = time.monotonic()
            else:
                stop_overlay_process(home, is_running=is_running, identity_reader=identity_reader)
            time.sleep(max(0.2, interval))
    except Exception:  # noqa: BLE001
        log(f"ERROR fault: unexpected supervisor failure; exiting {EXIT_SOFTWARE}")
        log(traceback.format_exc())
        return EXIT_SOFTWARE
    finally:
        current = read_pid(pid_path)
        if current == os.getpid():
            clear_pid(pid_path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tamahermes.overlay_supervisor")
    sub = parser.add_subparsers(dest="command", required=True)
    install = sub.add_parser("install")
    install.add_argument("--codex-home")
    install.add_argument("--repo-root")
    install.add_argument("--python")
    install.add_argument("--no-load", action="store_true")
    install.add_argument("--interval", type=float, default=1.0)
    supervisor = sub.add_parser("supervisor")
    supervisor.add_argument("--codex-home")
    supervisor.add_argument("--repo-root")
    supervisor.add_argument("--python")
    supervisor.add_argument("--interval", type=float, default=1.0)
    once = sub.add_parser("once")
    once.add_argument("--codex-home")
    once.add_argument("--repo-root")
    once.add_argument("--python")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    home = resolve_codex_home(args.codex_home)
    root = resolve_repo_root(args.repo_root) if getattr(args, "repo_root", None) else None
    if args.command == "install":
        report = install_launch_agent(home, root=root, python=args.python, interval=args.interval, load=not args.no_load)
        print(report)
    elif args.command == "once":
        print(supervise_once(home, root=root, python=args.python))
    elif args.command == "supervisor":
        raise SystemExit(supervisor_loop(home, root=root, python=args.python, interval=args.interval))


if __name__ == "__main__":
    main()
