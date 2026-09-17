"""The configurable show/hide hotkey: config plumbing and the Swift registration gate.

`hideHotkey` lives beside `hudHidden` in overlay-state.json and is *documented*
with the default Cmd+Shift+H. The Python runtime only moves that value into the
native config payload (the one file the Swift helper reads); the helper registers
the combo with Carbon `RegisterEventHotKey` and forwards hide/show through the
existing interaction file. So there is no global-hotkey implementation in Python
at all — the last test in this module is the gate for that.

Nothing here launches, restarts or signals an overlay process: the native helper
is exercised by `swiftc -parse` plus a pure-logic Swift harness, and every state
file lives in a temp home.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tamahermes.cli import main as cli_main
from tamahermes.overlay import (
    OVERLAY_MODE_COLLAPSED,
    OVERLAY_MODE_EXPANDED,
    hud_visible_now,
    native_overlay_config_payload,
)
from tamahermes.overlay_state import (
    DEFAULT_HIDE_HOTKEY,
    OVERLAY_MODE_HIDDEN,
    default_overlay_state,
    hide_hotkey_setting,
    load_overlay_state,
    overlay_mode,
    overlay_state_path,
    save_overlay_state,
)

ROOT = Path(__file__).resolve().parents[1]
OVERLAY_SOURCE = ROOT / "tamahermes" / "native_overlay" / "TamaHermesOverlay.swift"
SWIFT_HARNESS = ROOT / "tests" / "test_swift_hotkey.swift"


def swift_toolchain_env() -> dict[str, str]:
    """The environment a `swiftc` run needs, free of an SDK override.

    `/usr/bin/python3` is Xcode's python and exports `SDKROOT=<CommandLineTools
    SDK>` into its own environment, which the Swift driver then passes to `-sdk`.
    On this machine that SDK's `libSystem.B.tbd` is not readable by the installed
    linker, and instead of failing, swift-frontend spins in the Clang importer —
    a compile started from a test process never returns. Dropping the override
    lets the driver resolve its SDK exactly the way a plain shell invocation does
    (the xcode-select default), which compiles in about a second.
    """
    env = dict(os.environ)
    for override in ("SDKROOT", "DEVELOPER_DIR"):
        env.pop(override, None)
    return env


def run_swiftc(arguments: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["swiftc", *arguments],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=swift_toolchain_env(),
        timeout=timeout,
    )


def extract_hotkey_logic(source: str) -> str:
    """The hotkey logic of the helper, verbatim, as a compilable Swift file.

    `HotKeyCombo` (what the helper registers) and the marked `HotKeyFlipGate`
    block (how often a press may flip the panel) are both pure Swift with no
    AppKit, which is why the harness can compile and drive exactly the source
    that ships instead of a copy of it.
    """
    combo_start = source.index("struct HotKeyCombo")
    gate_start = source.index("// HOTKEY-GATE-BEGIN")
    gate_end = source.index("// HOTKEY-GATE-END")
    combo = source[combo_start:gate_start].strip()
    gate = source[gate_start:gate_end].strip()
    assert "static func parse" in combo, "HotKeyCombo.parse is missing from the overlay source"
    assert "struct HotKeyFlipGate" in gate, "the HOTKEY-GATE block is missing from the overlay source"
    return f"import Carbon\nimport Foundation\n\n{combo}\n\n{gate}\n"


def run_swift_hotkey_harness(workdir: Path) -> tuple[int, str, str] | None:
    """Compile and run the Swift hotkey harness; None when swiftc is unavailable."""
    if shutil.which("swiftc") is None:
        return None
    (workdir / "gate.swift").write_text(
        extract_hotkey_logic(OVERLAY_SOURCE.read_text(encoding="utf-8")), encoding="utf-8"
    )
    # Only one file of a multi-file Swift build may hold top-level code, and that
    # file has to be named main.swift — hence the copy.
    (workdir / "main.swift").write_text(SWIFT_HARNESS.read_text(encoding="utf-8"), encoding="utf-8")
    binary = workdir / "swift-hotkey-tests"
    build = run_swiftc(
        ["-o", str(binary), str(workdir / "gate.swift"), str(workdir / "main.swift")], timeout=180
    )
    if build.returncode != 0:
        return build.returncode, build.stdout, build.stderr
    run = subprocess.run(
        [str(binary)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        env=swift_toolchain_env(),
        timeout=120,
    )
    return run.returncode, run.stdout, run.stderr


class HotkeyConfigTests(unittest.TestCase):
    """`hideHotkey`: the default, the persisted key, and the CLI it follows."""

    def run_cli(self, home: Path, action: str) -> dict:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            # Top-level options precede the subcommand in argparse.
            cli_main(["--codex-home", str(home), "overlay", action])
        return json.loads(output.getvalue())

    def test_default_state_has_hide_hotkey(self) -> None:
        self.assertEqual(default_overlay_state()["hideHotkey"], "Cmd+Shift+H")
        self.assertEqual(DEFAULT_HIDE_HOTKEY, "Cmd+Shift+H")

    def test_state_without_the_key_reads_back_as_the_documented_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlay-state.json"
            save_overlay_state(path, default_overlay_state())

            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("hideHotkey", None)
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(load_overlay_state(path)["hideHotkey"], DEFAULT_HIDE_HOTKEY)

            raw["hideHotkey"] = "   "
            path.write_text(json.dumps(raw), encoding="utf-8")
            loaded = load_overlay_state(path)
            self.assertEqual(hide_hotkey_setting(loaded), DEFAULT_HIDE_HOTKEY)

            raw["hideHotkey"] = "Ctrl+Opt+J"
            path.write_text(json.dumps(raw), encoding="utf-8")
            self.assertEqual(hide_hotkey_setting(load_overlay_state(path)), "Ctrl+Opt+J")

    def test_cli_toggle_flips_hudHidden(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            hidden = self.run_cli(home, "hide")

            self.assertTrue(hidden["hudHidden"])
            self.assertEqual(hidden["mode"], "hidden")
            self.assertTrue(load_overlay_state(overlay_state_path(home))["hudHidden"])

            shown = self.run_cli(home, "show")

            self.assertFalse(shown["hudHidden"])
            self.assertFalse(load_overlay_state(overlay_state_path(home))["hudHidden"])

    def test_hud_visible_now_respects_hudHidden(self) -> None:
        # Same fixture the contract's row spells `hud_visible_now(selected=True,
        # surface_active=True, state={hudHidden=True})`; the third parameter is
        # named `overlay_state` in the module, so it is passed positionally.
        self.assertFalse(hud_visible_now(True, True, {"hudHidden": True}))
        self.assertTrue(hud_visible_now(True, True, {"hudHidden": False}))
        # Hidden wins over everything else, including an unselected pet.
        self.assertFalse(hud_visible_now(False, True, {"hudHidden": True}))

    def test_hide_persists_across_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.run_cli(home, "hide")

            # A restart re-reads the same file: nothing keeps hudHidden in
            # memory, and the native payload the fresh helper receives says the
            # panel is not visible.
            self.assertTrue(load_overlay_state(overlay_state_path(home))["hudHidden"])
            self.assertFalse(native_overlay_config_payload(home, visible=False)["visible"])

            self.run_cli(home, "show")
            self.assertFalse(load_overlay_state(overlay_state_path(home))["hudHidden"])

    def test_hide_hotkey_rides_every_native_config_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)

            hidden_panel = native_overlay_config_payload(home, visible=False, mode=OVERLAY_MODE_COLLAPSED)
            shown_panel = native_overlay_config_payload(home, visible=True, mode=OVERLAY_MODE_EXPANDED)

            # The hidden payload must still carry the combo: a config that
            # dropped it would unregister the one hotkey that can bring the
            # panel back.
            self.assertFalse(hidden_panel["visible"])
            self.assertEqual(hidden_panel["hideHotkey"], DEFAULT_HIDE_HOTKEY)
            self.assertEqual(shown_panel["hideHotkey"], DEFAULT_HIDE_HOTKEY)

            state = load_overlay_state(overlay_state_path(home))
            state["hideHotkey"] = "Ctrl+Opt+J"
            save_overlay_state(overlay_state_path(home), state)

            self.assertEqual(
                native_overlay_config_payload(home, visible=False)["hideHotkey"], "Ctrl+Opt+J"
            )

    def test_hidden_state_wins_over_the_pill_so_no_pixel_survives(self) -> None:
        """AC1: hide must clear *every* surface, the glass pill included."""
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".codex-global-state.json").write_text(
                json.dumps(
                    {
                        "electron-persisted-atom-state": {"selected-avatar-id": "custom:tamahermes"},
                        "electron-avatar-overlay-open": True,
                        "electron-avatar-overlay-bounds": {
                            "x": 100,
                            "y": 200,
                            "width": 160,
                            "height": 120,
                            "mascot": {"left": 20, "top": 30, "width": 40, "height": 40},
                        },
                    }
                ),
                encoding="utf-8",
            )
            state = default_overlay_state()
            # The worst case for a leak: the pill is also armed, and a pill is
            # the one surface the loop keeps on screen without an active hover.
            state["hudHidden"] = True
            state["hudCollapsed"] = True
            save_overlay_state(overlay_state_path(home), state)

            self.assertEqual(overlay_mode(state), OVERLAY_MODE_HIDDEN)
            self.assertFalse(hud_visible_now(True, True, state))
            hidden = native_overlay_config_payload(home, visible=False, mode=overlay_mode(state))
            self.assertFalse(hidden["visible"])
            # The combo still rides the hidden payload: hide must not disable the
            # hotkey that brings the panel back.
            self.assertEqual(hidden["hideHotkey"], DEFAULT_HIDE_HOTKEY)

    def test_tk_fallback_gates_its_window_on_the_same_flag(self) -> None:
        # Static proof for the Tk path (it needs a display to run): the loop's
        # only visibility gate is hud_visible_now, and its false branch withdraws
        # the window, so `hudHidden` leaves no Tk pixel either.
        source = (ROOT / "tamahermes" / "overlay.py").read_text(encoding="utf-8")
        tick = source.split("    def tick(self) -> None:", 1)[1].split("\n    def run(self)", 1)[0]
        self.assertIn("if not hud_visible_now(selected, surface_active, overlay_state):", tick)
        self.assertIn("self.window.withdraw()", tick.split("if not hud_visible_now", 1)[1])

    def test_no_global_hotkey_implementation_in_python(self) -> None:
        forbidden_modules = {
            "Foundation",
            "AppKit",
            "PyObjC",
            "objc",
            "Quartz",
            "CoreGraphics",
            "ApplicationServices",
        }
        runtime = sorted((ROOT / "tamahermes").rglob("*.py"))
        self.assertTrue(runtime, "the overlay runtime package was not found")

        for path in runtime:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported = [node.module]
                else:
                    imported = []
                for name in imported:
                    self.assertNotIn(name.split(".")[0], forbidden_modules, f"{path.name} imports {name}")

                # Comments and docstrings may *name* the Carbon entry point (the
                # TamaHermes comment on the config key does); the gate is that
                # the Python runtime never calls it.
                target = node.func if isinstance(node, ast.Call) else None
                if target is None:
                    continue
                called = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
                self.assertNotIn(
                    called,
                    {"RegisterEventHotKey", "UnregisterEventHotKey", "CGEventTapCreate", "CGPostEvent"},
                    f"{path.name} calls {called}",
                )


class SwiftHotkeySourceTests(unittest.TestCase):
    """The native helper's registration/unregistration wiring, in the source."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = OVERLAY_SOURCE.read_text(encoding="utf-8")
        cls.harness_workdir = tempfile.TemporaryDirectory()
        cls.harness = run_swift_hotkey_harness(Path(cls.harness_workdir.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.harness_workdir.cleanup()

    def assert_swift_test_passed(self, name: str) -> None:
        if self.harness is None:
            self.skipTest("swiftc is unavailable")
        code, out, err = self.harness
        self.assertIn(f"== {name}", out, f"the Swift harness never reached {name}: {err or out}")
        ok = [line for line in out.splitlines() if line.startswith(f"ok {name}: ")]
        failed = [line for line in out.splitlines() if line.startswith(f"FAIL {name}: ")]
        self.assertTrue(ok, f"no assertions ran for {name}: {err or out}")
        self.assertEqual(failed, [], "\n".join(failed))
        self.assertEqual(code, 0, err or out)

    def test_hotkey_registration_is_wired_into_the_helper(self) -> None:
        self.assertIn("import Carbon", self.source)
        self.assertIn("RegisterEventHotKey(", self.source)
        self.assertIn("UnregisterEventHotKey(reference)", self.source)
        self.assertIn("let hideHotkey: String?", self.source)

        init_body = self.source.split("init(configPath: String) {", 1)[1].split("\n    }", 1)[0]
        self.assertIn("registerConfiguredHotKey(readConfig())", init_body)

        stop_body = self.source.split("func stopHotKeys() {", 1)[1].split("\n    }", 1)[0]
        self.assertIn("unregisterHotKey()", stop_body)
        self.assertIn("RemoveEventHandler(handler)", stop_body)
        self.assertIn("stopHotKeys()", self.source.split("deinit {", 1)[1].split("\n    }", 1)[0])
        # `overlay stop` reaches the helper as SIGTERM: the kill-switch has to
        # surrender the hotkey on that path too, not only on teardown.
        entry = self.source.split("let controller = OverlayController", 1)[1]
        self.assertIn("DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)", entry)
        self.assertIn("controller.stopHotKeys()", entry)
        self.assertIn("raise(SIGTERM)", entry)
        self.assertIn("hotKeyFlipGate", self.source)

    def test_swift_register_event_hotkey_syntax(self) -> None:
        if shutil.which("swiftc") is None:
            self.skipTest("swiftc is unavailable")

        for source in (OVERLAY_SOURCE, SWIFT_HARNESS):
            parsed = run_swiftc(["-parse", str(source)], timeout=180)
            self.assertEqual(parsed.returncode, 0, f"{source.name}: {parsed.stderr}")

        self.assert_swift_test_passed("test_swift_register_event_hotkey_syntax")

    def test_swift_hotkey_handler_ignores_stop_state(self) -> None:
        self.assert_swift_test_passed("test_swift_hotkey_handler_ignores_stop_state")


if __name__ == "__main__":
    unittest.main()
