"""D3 build provenance: recipe selection, atomic replace, additive record.

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
    GLASS_SDK_HEADER,
    NATIVE_OVERLAY_DEPLOYMENT_TARGET_MIN,
    NATIVE_OVERLAY_PROVENANCE_SCHEMA,
    build_native_overlay_helper,
    native_overlay_build_recipe,
    native_overlay_paths,
    native_overlay_recipe_key,
    native_overlay_source,
    probe_glass_sdk,
    sdk_version_key,
)

ROOT = Path(__file__).resolve().parents[1]


class StubRunner:
    """Stands in for subprocess.run: --version, git and swiftc."""

    def __init__(
        self,
        *,
        version: str = "swift-driver version: 1.0 Apple Swift version 6.4 (swiftlang)",
        compile_rc: int = 0,
        fail_first_compile: bool = False,
        git: bool = True,
    ) -> None:
        self.version = version
        self.compile_rc = compile_rc
        self.fail_first_compile = fail_first_compile
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
                return subprocess.CompletedProcess(command, 0, stdout="feat/hud-liquid-glass-collapsible\n", stderr="")
            if "rev-parse" in command and "HEAD" in command:
                return subprocess.CompletedProcess(command, 0, stdout="cafe1234\n", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        self.compiles += 1
        if self.fail_first_compile and self.compiles == 1:
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="glass unavailable\n")
        if self.compile_rc != 0:
            return subprocess.CompletedProcess(command, self.compile_rc, stdout="", stderr="compile failed\n")
        output = Path(command[command.index("-o") + 1])
        output.write_bytes(b"fake-binary")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


def make_fake_toolchain(root: Path, *, sdk_names: list[str], glass_sdks: list[str]) -> Path:
    clt = root / "clt"
    (clt / "usr" / "bin").mkdir(parents=True, exist_ok=True)
    (clt / "usr" / "bin" / "swiftc").write_text("#!/bin/sh\n", encoding="utf-8")
    for name in sdk_names:
        sdk = clt / "SDKs" / name
        sdk.mkdir(parents=True, exist_ok=True)
        if name in glass_sdks:
            header = sdk / GLASS_SDK_HEADER
            header.parent.mkdir(parents=True, exist_ok=True)
            header.write_text("// NSGlassEffectView\n", encoding="utf-8")
    return clt


class BuildRecipeTests(unittest.TestCase):
    def test_sdk_version_key_compares_numerically(self) -> None:
        self.assertGreater(sdk_version_key("MacOSX27.sdk"), sdk_version_key("MacOSX9.10.sdk"))
        self.assertGreater(sdk_version_key("MacOSX26.5.sdk"), sdk_version_key("MacOSX26.sdk"))
        self.assertEqual(sdk_version_key("MacOSX.sdk"), (0, 0))
        self.assertEqual(sdk_version_key("not-an-sdk"), (-1, -1))

    def test_highest_glass_capable_sdk_wins_over_a_newer_one_without_the_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(
                Path(tmp),
                sdk_names=["MacOSX.sdk", "MacOSX9.10.sdk", "MacOSX15.2.sdk", "MacOSX26.sdk", "MacOSX27.sdk"],
                glass_sdks=["MacOSX26.sdk", "MacOSX27.sdk"],
            )

            sdk = probe_glass_sdk(clt)

            self.assertIsNotNone(sdk)
            assert sdk is not None
            self.assertEqual(sdk.name, "MacOSX27.sdk")

    def test_no_glass_sdk_anywhere_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX.sdk", "MacOSX15.2.sdk"], glass_sdks=[])

            self.assertIsNone(probe_glass_sdk(clt))

    def test_recipe_uses_the_glass_toolchain_when_the_sdk_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])

            recipe = native_overlay_build_recipe(native_overlay_source(), toolchain_root=clt, machine="arm64", runner=StubRunner())

            self.assertTrue(recipe["glassEnabled"])
            self.assertEqual(recipe["target"], f"arm64-apple-{NATIVE_OVERLAY_DEPLOYMENT_TARGET_MIN}")
            self.assertEqual(recipe["swiftc"], clt / "usr" / "bin" / "swiftc")
            self.assertIn("-D", recipe["flags"])
            self.assertIn("EVOPET_GLASS", recipe["flags"])
            self.assertIn("-sdk", recipe["flags"])
            self.assertIn(str(clt / "SDKs" / "MacOSX27.sdk"), recipe["flags"])

    def test_recipe_degrades_to_legacy_without_a_glass_sdk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX15.2.sdk"], glass_sdks=[])

            recipe = native_overlay_build_recipe(native_overlay_source(), toolchain_root=clt, machine="arm64", runner=StubRunner())

            self.assertFalse(recipe["glassEnabled"])
            self.assertIsNone(recipe["target"])
            self.assertEqual(recipe["flags"], ["-O", "-framework", "AppKit", "-framework", "WebKit"])

    def test_recipe_target_follows_the_host_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])

            recipe = native_overlay_build_recipe(native_overlay_source(), toolchain_root=clt, machine="x86_64", runner=StubRunner())

            self.assertEqual(recipe["target"], f"x86_64-apple-{NATIVE_OVERLAY_DEPLOYMENT_TARGET_MIN}")

    def test_recipe_key_changes_with_every_locked_input(self) -> None:
        base_source = "aaaa"
        recipe = {
            "swiftc": Path("/usr/bin/swiftc"),
            "sdk": Path("/sdk/MacOSX27.sdk"),
            "target": "arm64-apple-macos15.0",
            "flags": ["-O", "-sdk", "/sdk/MacOSX27.sdk", "-D", "EVOPET_GLASS"],
            "toolchainVersion": "Swift 6.4",
        }
        baseline = native_overlay_recipe_key(recipe, base_source)

        self.assertEqual(native_overlay_recipe_key(dict(recipe), base_source), baseline)
        self.assertNotEqual(native_overlay_recipe_key(recipe, "bbbb"), baseline)

        other_sdk = dict(recipe, sdk=Path("/sdk/MacOSX26.sdk"))
        self.assertNotEqual(native_overlay_recipe_key(other_sdk, base_source), baseline)

        other_flags = dict(recipe, flags=[*recipe["flags"], "-g"])
        self.assertNotEqual(native_overlay_recipe_key(other_flags, base_source), baseline)

        other_version = dict(recipe, toolchainVersion="Swift 6.5")
        self.assertNotEqual(native_overlay_recipe_key(other_version, base_source), baseline)

        no_glass = dict(recipe, sdk=None, target=None, flags=["-O", "-framework", "AppKit", "-framework", "WebKit"])
        self.assertNotEqual(native_overlay_recipe_key(no_glass, base_source), baseline)


class BuildProvenanceTests(unittest.TestCase):
    def source_hash(self) -> str:
        return hashlib.sha256(native_overlay_source().read_bytes()).hexdigest()

    def test_build_replaces_atomically_and_records_every_provenance_field(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk", "MacOSX.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner()
            replaces: list[tuple[str, str]] = []
            real_replace = __import__("os").replace

            def spy_replace(source: object, target: object) -> None:
                replaces.append((str(source), str(target)))
                real_replace(source, target)

            with mock.patch("tamahermes.overlay.os.replace", spy_replace):
                binary = build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

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
                "sdkPath",
                "target",
                "flags",
                "glassEnabled",
                "gitHead",
                "gitBranch",
                "gitDirty",
                "builtAt",
            ):
                self.assertIn(field, provenance)
            self.assertEqual(provenance["sourceSha256"], self.source_hash())
            self.assertEqual(provenance["binarySha256"], hashlib.sha256(b"fake-binary").hexdigest())
            self.assertTrue(provenance["glassEnabled"])
            self.assertEqual(provenance["sdkName"], "MacOSX27.sdk")
            self.assertEqual(provenance["gitHead"], "cafe1234")
            self.assertEqual(provenance["gitBranch"], "feat/hud-liquid-glass-collapsible")
            self.assertFalse(provenance["gitDirty"])
            self.assertNotIn("recipeFallback", provenance)

            # Legacy stamp keeps the old wire format: the source hash only.
            self.assertEqual(paths["stamp"].read_text(encoding="utf-8").strip(), self.source_hash())

    def test_recipe_key_matches_the_recorded_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            provenance = json.loads(native_overlay_paths(home)["provenance"].read_text(encoding="utf-8"))
            recipe = native_overlay_build_recipe(
                native_overlay_source(),
                toolchain_root=clt,
                machine="arm64",
                sdk_dirs=[clt / "SDKs" / "MacOSX27.sdk"],
                runner=runner,
            )
            self.assertEqual(provenance["recipeKey"], native_overlay_recipe_key(recipe, self.source_hash()))

    def test_cached_build_is_not_recompiled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")
            self.assertEqual(runner.compiles, 1)

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            self.assertEqual(runner.compiles, 1)
            self.assertEqual(
                native_overlay_paths(home)["stamp"].read_text(encoding="utf-8").strip(),
                self.source_hash(),
            )

    def test_a_foreign_binary_invalidates_the_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner()
            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            paths = native_overlay_paths(home)
            paths["binary"].write_bytes(b"tampered")
            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            self.assertEqual(runner.compiles, 2)
            self.assertEqual(paths["binary"].read_bytes(), b"fake-binary")
            # One-generation rollback copy of the replaced binary.
            self.assertEqual(paths["backup"].read_bytes(), b"tampered")
            self.assertTrue(paths["backupProvenance"].exists())

    def test_glass_compile_failure_degrades_to_the_legacy_recipe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner(fail_first_compile=True)

            binary = build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            provenance = json.loads(native_overlay_paths(home)["provenance"].read_text(encoding="utf-8"))
            self.assertTrue(binary.exists())
            self.assertFalse(provenance["glassEnabled"])
            self.assertIn("glass-compile-failed", provenance["recipeFallback"])
            self.assertEqual(provenance["flags"], ["-O", "-framework", "AppKit", "-framework", "WebKit"])
            self.assertEqual(runner.compiles, 2)

    def test_legacy_compile_failure_still_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX15.2.sdk"], glass_sdks=[])
            runner = StubRunner(compile_rc=1)

            from tamahermes.overlay import NativeOverlayUnavailable

            with self.assertRaises(NativeOverlayUnavailable):
                build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

            self.assertFalse(native_overlay_paths(home)["binary"].exists())

    def test_git_provenance_degrades_when_git_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])

            build_native_overlay_helper(home, runner=StubRunner(git=False), toolchain_root=clt, machine="arm64")

            provenance = json.loads(native_overlay_paths(home)["provenance"].read_text(encoding="utf-8"))
            self.assertIsNone(provenance["gitHead"])
            self.assertIsNone(provenance["gitBranch"])
            self.assertIsNone(provenance["gitDirty"])

    def test_build_touches_nothing_outside_the_given_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            clt = make_fake_toolchain(Path(tmp), sdk_names=["MacOSX27.sdk"], glass_sdks=["MacOSX27.sdk"])
            runner = StubRunner()

            build_native_overlay_helper(home, runner=runner, toolchain_root=clt, machine="arm64")

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
