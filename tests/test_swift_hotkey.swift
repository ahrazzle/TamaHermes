// Swift unit tests for the native overlay's hide/show hotkey logic.
//
// This file is compiled and run by `tests/test_hotkey_config.py`, which pulls
// the hotkey logic (`HotKeyCombo` + the HOTKEY-GATE block) verbatim out of
// `tamahermes/native_overlay/TamaHermesOverlay.swift`, prepends the Carbon
// import and compiles it together with a copy of this file named `main.swift`.
// Nothing here launches the overlay: it exercises pure value logic only.
//
// Run it through the Python test (it is not standalone):
//   python3 -m unittest tests.test_hotkey_config

import Carbon
import Foundation

var failures: [String] = []
var currentTest = ""

func start(_ name: String) {
    currentTest = name
    print("== \(name)")
}

func check(_ condition: Bool, _ label: String) {
    print("\(condition ? "ok" : "FAIL") \(currentTest): \(label)")
    if !condition {
        failures.append("\(currentTest): \(label)")
    }
}

/// The registration the helper performs is `RegisterEventHotKey(combo.keyCode,
/// combo.modifiers, ...)`, so the parsed combo *is* the registration request.
/// This covers parsing (including the documented default) and syntax validity of
/// the logic the file under test contributes.
func test_swift_register_event_hotkey_syntax() {
    start("test_swift_register_event_hotkey_syntax")

    check(HotKeyCombo.documentedDefault == "Cmd+Shift+H", "documented default is Cmd+Shift+H")

    guard let combo = HotKeyCombo.parse(HotKeyCombo.documentedDefault) else {
        check(false, "the documented default parses")
        return
    }
    check(combo.keyCode == UInt32(kVK_ANSI_H), "default combo registers the H letter key code")
    check(combo.modifiers == UInt32(cmdKey | shiftKey), "default combo registers Cmd+Shift")

    check(HotKeyCombo.parse("cmd+shift+h")?.keyCode == combo.keyCode, "parsing is case-insensitive")
    check(HotKeyCombo.parse(" Cmd + Shift + H ")?.modifiers == combo.modifiers, "surrounding spaces are ignored")

    let ctrlOptJ = HotKeyCombo.parse("Ctrl+Opt+J")
    check(ctrlOptJ?.modifiers == UInt32(controlKey | optionKey), "Ctrl+Opt are both parsed")
    check(HotKeyCombo.parse("Alt+J")?.modifiers == UInt32(optionKey), "Alt is an alias for Opt")
    check(HotKeyCombo.parse("Command+J")?.modifiers == UInt32(cmdKey), "Command is an alias for Cmd")

    check(HotKeyCombo.parse("Cmd+5")?.keyCode == UInt32(kVK_ANSI_5), "digits parse")
    check(HotKeyCombo.parse("Cmd+Space")?.keyCode == UInt32(kVK_Space), "named keys parse")

    // Refusals: the helper must run with no hotkey rather than register
    // something that steals input from other applications.
    check(HotKeyCombo.parse("H") == nil, "a modifier-less key is refused")
    check(HotKeyCombo.parse(nil) == nil, "a missing combo is refused")
    check(HotKeyCombo.parse("") == nil, "an empty combo is refused")
    check(HotKeyCombo.parse("Cmd+Shift") == nil, "a combo without a key is refused")
    check(HotKeyCombo.parse("Cmd+Shift+H+J") == nil, "two keys are refused")
    check(HotKeyCombo.parse("Cmd+Shift+F13") == nil, "an unknown key is refused")
    check(HotKeyCombo.parse("Hyper+H") == nil, "an unknown modifier is refused")

    check(HotKeyCombo.signature == OSType(0x4556_4F50), "the hotkey id signature is stable")
}

/// Contract 1.3(c)/1.6: the handler must refuse flips once the helper has been
/// stopped, and a held key must not flip the panel more than once per window.
func test_swift_hotkey_handler_ignores_stop_state() {
    start("test_swift_hotkey_handler_ignores_stop_state")

    var gate = HotKeyFlipGate()
    check(gate.allowsFlip(now: 1_000.0), "a fresh gate allows the first flip")
    check(gate.allowsFlip(now: 1_000.0 + HotKeyFlipGate.cooldownSeconds / 2) == false, "a repeat inside the window is refused")
    check(gate.allowsFlip(now: 1_000.0 + HotKeyFlipGate.cooldownSeconds) == true, "a press at the window edge is allowed")

    gate.stop()
    check(gate.stopped, "the gate reports the stopped state")
    check(gate.allowsFlip(now: 1_000.0 + 600.0) == false, "a stopped gate refuses a flip far past the window")

    gate.reset()
    check(gate.allowsFlip(now: 2_000.0), "a reset gate allows a flip again")

    // 60 auto-repeat presses inside one second: one flip per window, never one
    // per press (the law's oscillation acceptance test).
    var repeats = HotKeyFlipGate()
    var flips = 0
    for index in 0..<60 {
        if repeats.allowsFlip(now: 3_000.0 + Double(index) / 60.0) {
            flips += 1
        }
    }
    check(flips == 5, "60 presses in one second (0.2 s window) produce 5 flips, got \(flips)")
}

test_swift_register_event_hotkey_syntax()
test_swift_hotkey_handler_ignores_stop_state()

if failures.isEmpty {
    print("ALL SWIFT HOTKEY TESTS PASSED")
    exit(0)
}
print("FAILURES: \(failures.count)")
for failure in failures {
    print("  \(failure)")
}
exit(1)
