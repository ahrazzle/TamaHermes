"""D3 build provenance: recipe lock, atomic replace, additive record.

Adapted for the restored (pre-liquid-glass) contract: the recipe is exactly the
baseline legacy command — no SDK probe, no ``-D EVOPET_GLASS``, no glass fields
in the provenance record. The cache/drift/atomic-swap machinery stays, since
contract C2.3 (helper source drift) builds on it.

Every test uses a temp home and a stub toolchain. Nothing here compiles with the
real toolchain, touches the live native-overlay directory, or runs a process.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tamahermes.overlay import (
    NATIVE_OVERLAY_PROVENANCE_SCHEMA,
    build_native_overlay_helper,
    native_overlay_build_recipe,
    native_overlay_paths,
    native_overlay_recipe_key,
    native_overlay_source,
)

ROOT = Path(__file__).resolve().parents[1]

BASELINE_FLAGS = ["-O", "-framework", "AppKit", "-framework", "WebKit"]


class StubRunner:
    """Stands in for subprocess.run: --version, git and swiftc."""

    def __init__(
        self,
        *,
        version: str = "swift-driver version: 1.0 Apple Swift version 6.4 (swiftlang)",
        compile_rc: int = 0,
        git: bool = True,
    ) -> None:
        self.version = version
        self.compile_rc = compile_rc
        self.git_available = git
        self.commands: list[list[str]] = []
        self.compiles = 0

    def __call__(self, command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.commands.append([str(part) for part in command])
        executable = str(command[0])
        if executable.endswith("swiftc") and "--version" in command:
            return subprocess.CompletedProcess(command, 0, stdout=self.version + "\n", stderr="")
        if executable == "git":
            if not self.git_available:
                return subprocess.CompletedProcess(command, 128, stdout="", stderr="not a git repository\n")
            if "--abbrev-ref" in command:
                return subprocess.CompletedProcess(command, 0, stdout="fix/evopet-hud-layout-restore\n", stderr="")
            if "rev-parse" in command and "HEAD" in command:
                return subprocess.CompletedProcess(command, 0, stdout="cafe1234\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        self.compiles += 1
        if self.compile_rc != 0:
            return subprocess.CompletedProcess(command, self.compile_rc, stdout="", stderr="compile failed\n")
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"fake-binary")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def make_fake_toolchain(root: Path) -> Path:
    clt = root / "clt"
    (clt / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (clt / "usr" / "bin" / "swiftc").write_text("#!/bin/sh\n", encoding="utf-8")
    return clt


class BuildRecipeTests(unittest.TestCase):
    def test_recipe_is_the_baseline_legacy_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp))

            recipe = native_overlay_build_recipe(native_overlay_source(), toolchain_root=clt, runner=StubRunner())

            self.assertEqual(recipe["swiftc"], clt / "usr" / "bin" / "swiftc")
            self.assertEqual(recipe["flags"], BASELINE_FLAGS)
            self.assertEqual(recipe["toolchainVersion"], "swift-driver version: 1.0 Apple Swift version 6.4 (swiftlang)")

    def test_recipe_carries_no_glass_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp))

            recipe = native_overlay_build_recipe(native_overlay_source(), toolchain_root=clt, runner=StubRunner())

            for key in ("glassEnabled", "sdk", "target"):
                self.assertNotIn(key, recipe)
            for flag in ("-D", "EVOPET_GLASS", "-sdk", "-target"):
                self.assertNotIn(flag, recipe["flags"])

    def test_recipe_key_changes_with_every_locked_input(self) -> None:
        base_source = "aaaa"
        recipe = {
            "swiftc": Path("/usr/bin/swiftc"),
            "flags": list(BASELINE_FLAGS),
            "toolchainVersion": "Swift 6.4",
        }
        baseline = native_overlay_recipe_key(recipe, base_source)

        self.assertEqual(native_overlay_recipe_key(dict(recipe), base_source), baseline)
        self.assertNotEqual(native_overlay_recipe_key(recipe, "bbbb"), baseline)

        other_flags = dict(recipe, flags=[*recipe["flags"], "-g"])
        self.assertNotEqual(native_overlay_recipe_key(other_flags, base_source), baseline)

        other_version = dict(recipe, toolchainVersion="Swift 6.5")
        self.assertNotEqual(native_overlay_recipe_key(other_version, base_source), baseline)

        # A glass-era recipe (whatever a stale cache recorded) keys differently
        # from the restored one, so the first post-revert build re-compiles.
        glassish = dict(recipe, flags=[*recipe["flags"], "-D", "EVOPET_GLASS"])
        self.assertNotEqual(native_overlay_recipe_key(glassish, base_source), baseline)


class BuildProvenanceTests(unittest.TestCase):
    def source_hash(self) -> str:
        return hashlib.sha256(native_overlay_source().read_bytes()).hexdigest()

    def test_build_replaces_atomically_and_records_every_provenance_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner()
            replaces: list[tuple[str, str]] = []
            real_replace = __import__("os").replace

            def spy_replace(source: object, target: object) -> None:
                replaces.append((str(source), str(target)))
                real_replace(source, target)

            with mock.patch("tamahermes.overlay.os.replace", spy_replace):
                binary = build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            paths = native_overlay_paths(home)
            self.assertEqual(binary, paths["binary"])
            self.assertTrue(binary.exists())
            self.assertEqual(binary.read_bytes(), b"fake-binary")

            # Atomic: compiled to a temp sibling and moved into place. (The
            # provenance record is written atomically too, hence the filter.)
            binary_replaces = [call for call in replaces if call[1] == str(paths["binary"])]
            self.assertEqual(len(binary_replaces), 1)
            self.assertTrue(binary_replaces[0][0].endswith(f"TamaHermesOverlay.tmp-{__import__('os').getpid()}"))
            self.assertEqual(list(paths["root"].glob("*.tmp-*")), [])
            self.assertFalse(paths["backup"].exists())

            provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
            self.assertEqual(provenance["schema"], NATIVE_OVERLAY_PROVENANCE_SCHEMA)
            for field in (
                "recipeKey",
                "sourcePath",
                "sourceSha256",
                "binarySha256",
                "toolchainPath",
                "toolchainVersion",
                "flags",
                "gitHead",
                "gitBranch",
                "gitDirty",
                "builtAt",
            ):
                self.assertIn(field, provenance)
            # The no-glass lock on the record as well.
            for gone in ("sdkPath", "sdkName", "target", "glassEnabled", "recipeFallback"):
                self.assertNotIn(gone, provenance)
            self.assertEqual(provenance["flags"], BASELINE_FLAGS)
            self.assertEqual(provenance["sourceSha256"], self.source_hash())
            self.assertEqual(provenance["binarySha256"], hashlib.sha256(b"fake-binary").hexdigest())
            self.assertEqual(provenance["gitHead"], "cafe1234")
            self.assertEqual(provenance["gitBranch"], "fix/evopet-hud-layout-restore")
            self.assertFalse(provenance["gitDirty"])

            # Legacy stamp keeps the old wire format: the source hash only.
            self.assertEqual(paths["stamp"].read_text(encoding="utf-8").strip(), self.source_hash())

    def test_recipe_key_matches_the_recorded_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            provenance = json.loads(native_overlay_paths(home)["provenance"].read_text(encoding="utf-8"))
            recipe = native_overlay_build_recipe(
                native_overlay_source(),
                toolchain_root=clt,
                runner=runner,
            )
            self.assertEqual(provenance["recipeKey"], native_overlay_recipe_key(recipe, self.source_hash()))

    def test_cached_build_is_not_recompiled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)
            self.assertEqual(runner.compiles, 1)

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            self.assertEqual(runner.compiles, 1)
            self.assertEqual(
                native_overlay_paths(home)["stamp"].read_text(encoding="utf-8").strip(),
                self.source_hash(),
            )

    def test_a_foreign_binary_invalidates_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner()
            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            paths = native_overlay_paths(home)
            paths["binary"].write_bytes(b"tampered")
            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            self.assertEqual(runner.compiles, 2)
            self.assertEqual(paths["binary"].read_bytes(), b"fake-binary")
            # One-generation rollback copy of the replaced binary.
            self.assertEqual(paths["backup"].read_bytes(), b"tampered")
            self.assertTrue(paths["backupProvenance"].exists())

    def test_compile_failure_raises_and_leaves_no_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner(compile_rc=1)

            from tamahermes.overlay import NativeOverlayUnavailable

            with self.assertRaises(NativeOverlayUnavailable):
                build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            self.assertFalse(native_overlay_paths(home)["binary"].exists())
            # One attempt only: there is no glass path to degrade onto.
            self.assertEqual(runner.compiles, 1)

    def test_git_provenance_degrades_when_git_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))

            build_native_overlay_helper(home, runner=StubRunner(git=False), toolchain_root=clt)

            provenance = json.loads(native_overlay_paths(home)["provenance"].read_text(encoding="utf-8"))
            self.assertIsNone(provenance["gitHead"])
            self.assertIsNone(provenance["gitBranch"])
            self.assertIsNone(provenance["gitDirty"])

    def test_build_touches_nothing_outside_the_given_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp))
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt)

            written = sorted(path.relative_to(home).as_posix() for path in home.rglob("*") if path.is_file())
            self.assertEqual(
                written,
                [
                    "tamahermes/native-overlay/TamaHermesOverlay",
                    "tamahermes/native-overlay/TamaHermesOverlay.provenance.json",
                    "tamahermes/native-overlay/TamaHermesOverlay.sha256",
                ],
            )
            # Nothing is written into the source checkout: the build is cached in
            # the given codex home only.
            source_dir = ROOT / "tamahermes" / "native_overlay"
            self.assertFalse((source_dir / "TamaHermesOverlay").exists())
            self.assertFalse((source_dir / "TamaHermesOverlay.provenance.json").exists())
            self.assertFalse((source_dir / "TamaHermesOverlay.sha256").exists())


if __name__ == "__main__":
    unittest.main()
