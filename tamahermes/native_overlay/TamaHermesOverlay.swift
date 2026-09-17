import AppKit
import Carbon
import Foundation
import WebKit

struct OverlayConfig: Decodable {
    let visible: Bool?
    let x: Double?
    let y: Double?
    let width: Double?
    let height: Double?
    let scale: Double?
    let htmlPath: String?
    let hoverX: Double?
    let hoverY: Double?
    let hoverWidth: Double?
    let hoverHeight: Double?
    let hoverDelaySeconds: Double?
    // Additive keys (schema id unchanged). An old config file without them
    // decodes to nil, which keeps today's behaviour; a new config file with
    // extra keys still decodes because Swift's Decodable ignores unknowns.
    let mode: String?
    let minWidth: Double?
    let minHeight: Double?
    /// Contract 1.4: the user-configurable show/hide combo. The Python loop
    /// writes it into *every* config payload (documented default Cmd+Shift+H),
    /// so an old config that omits it falls back to that same default below.
    let hideHotkey: String?
}

struct SfxRequest: Decodable {
    let id: String?
    let filePath: String?
    let volume: Double?
    let expiresAt: Double?
}

// MARK: - Hide/show hotkey (pure logic, exercised by tests/test_swift_hotkey.swift)

/// A parsed `hideHotkey` combo: one Carbon virtual key code plus a Carbon
/// modifier mask. Deliberately free of AppKit so the Swift harness can compile
/// the same source and drive it. The registration the helper performs is
/// `RegisterEventHotKey(combo.keyCode, combo.modifiers, ...)`, so this value is
/// the registration request.
struct HotKeyCombo: Equatable {
    let keyCode: UInt32
    let modifiers: UInt32

    /// The documented default of the `hideHotkey` key (contract 1.4). The Python
    /// side owns the same default in overlay-state.json (`DEFAULT_HIDE_HOTKEY`);
    /// a config payload that predates the key falls back to this value.
    static let documentedDefault = "Cmd+Shift+H"

    /// Carbon signature for this helper's single registered hotkey.
    static let signature: OSType = 0x4556_4F50  // 'EVOP'

    /// Parse a combo such as `Cmd+Shift+H`.
    ///
    /// Tokens are case-insensitive and `+`-separated. Modifiers: `Cmd`/`Command`,
    /// `Shift`, `Opt`/`Option`/`Alt`, `Ctrl`/`Control`. The key is one letter or
    /// digit, or `Space`/`Tab`/`Return`/`Escape`.
    ///
    /// Returns nil for anything else, *including a modifier-less key*: a bare
    /// key would swallow ordinary typing in every application, so an unusable
    /// combo leaves the helper with no global hotkey rather than a key grab.
    static func parse(_ raw: String?) -> HotKeyCombo? {
        guard let raw else { return nil }
        let tokens = raw
            .split(separator: "+")
            .map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return nil }

        var modifiers: UInt32 = 0
        var keyToken: String?
        for token in tokens {
            switch token {
            case "cmd", "command", "⌘": modifiers |= UInt32(cmdKey)
            case "shift", "⇧": modifiers |= UInt32(shiftKey)
            case "opt", "option", "alt", "⌥": modifiers |= UInt32(optionKey)
            case "ctrl", "control", "⌃": modifiers |= UInt32(controlKey)
            default:
                // A second non-modifier token means the combo is malformed.
                if keyToken != nil { return nil }
                keyToken = token
            }
        }
        guard modifiers != 0, let keyToken, let keyCode = keyCode(for: keyToken) else { return nil }
        return HotKeyCombo(keyCode: keyCode, modifiers: modifiers)
    }

    static func keyCode(for token: String) -> UInt32? {
        switch token {
        case "space": return UInt32(kVK_Space)
        case "tab": return UInt32(kVK_Tab)
        case "return", "enter": return UInt32(kVK_Return)
        case "escape", "esc": return UInt32(kVK_Escape)
        default: break
        }
        guard token.count == 1, let character = token.first else { return nil }
        return letterKeyCodes[character] ?? digitKeyCodes[character]
    }

    // Carbon virtual key codes (HIToolbox Events.h).
    private static let letterKeyCodes: [Character: UInt32] = [
        "a": UInt32(kVK_ANSI_A), "b": UInt32(kVK_ANSI_B), "c": UInt32(kVK_ANSI_C),
        "d": UInt32(kVK_ANSI_D), "e": UInt32(kVK_ANSI_E), "f": UInt32(kVK_ANSI_F),
        "g": UInt32(kVK_ANSI_G), "h": UInt32(kVK_ANSI_H), "i": UInt32(kVK_ANSI_I),
        "j": UInt32(kVK_ANSI_J), "k": UInt32(kVK_ANSI_K), "l": UInt32(kVK_ANSI_L),
        "m": UInt32(kVK_ANSI_M), "n": UInt32(kVK_ANSI_N), "o": UInt32(kVK_ANSI_O),
        "p": UInt32(kVK_ANSI_P), "q": UInt32(kVK_ANSI_Q), "r": UInt32(kVK_ANSI_R),
        "s": UInt32(kVK_ANSI_S), "t": UInt32(kVK_ANSI_T), "u": UInt32(kVK_ANSI_U),
        "v": UInt32(kVK_ANSI_V), "w": UInt32(kVK_ANSI_W), "x": UInt32(kVK_ANSI_X),
        "y": UInt32(kVK_ANSI_Y), "z": UInt32(kVK_ANSI_Z),
    ]

    private static let digitKeyCodes: [Character: UInt32] = [
        "0": UInt32(kVK_ANSI_0), "1": UInt32(kVK_ANSI_1), "2": UInt32(kVK_ANSI_2),
        "3": UInt32(kVK_ANSI_3), "4": UInt32(kVK_ANSI_4), "5": UInt32(kVK_ANSI_5),
        "6": UInt32(kVK_ANSI_6), "7": UInt32(kVK_ANSI_7), "8": UInt32(kVK_ANSI_8),
        "9": UInt32(kVK_ANSI_9),
    ]
}

// HOTKEY-GATE-BEGIN
/// Rate limit for hotkey-driven visibility flips (contract 1.6).
///
/// A held global hotkey auto-repeats, so this gate allows at most one flip per
/// cooldown window ("boundary input produces at most one flip"), and refuses
/// every flip once the helper has been stopped (contract 1.3c). Pure value type,
/// no AppKit: `tests/test_swift_hotkey.swift` compiles this block verbatim and
/// exercises it.
struct HotKeyFlipGate {
    static let cooldownSeconds: Double = 0.2

    private var lastFlipAt: Double?
    private(set) var stopped = false

    /// The kill-switch state: a stopped helper never flips the panel again.
    mutating func stop() {
        stopped = true
    }

    mutating func reset() {
        stopped = false
        lastFlipAt = nil
    }

    /// True when a flip is allowed now; records it when it returns true.
    mutating func allowsFlip(now: Double) -> Bool {
        if stopped { return false }
        if let last = lastFlipAt, now - last < Self.cooldownSeconds { return false }
        lastFlipAt = now
        return true
    }
}
// HOTKEY-GATE-END

final class NonActivatingPanel: NSPanel {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class OverlayController: NSObject, WKScriptMessageHandler {
    private let configPath: String
    private let statusPath: String
    private let sfxPath: String
    private let interactionPath: String
    private var panel: NonActivatingPanel!
    private var webView: WKWebView!
    private var content: NSView!
    private var lastHTMLPath: String = ""
    private var lastHTMLModified: Date?
    private var lastHTMLHash: UInt64?
    private var hoverStartedAt: Date?
    private var lastSfxId: String?
    private var lastSfxFilename: String?
    private var lastSfxPlayedAt: String?
    private var lastSfxError: String?
    private var activeSounds: [NSSound] = []
    private var dragMonitor: Any?
    private var dragOrigin: NSPoint?
    private var dragMouseOrigin: NSPoint?
    private var dragLastEventAt: Date?
    private var chromeView: NSView?
    private var chromeKind: String = ""
    private var chromeCornerRadius: CGFloat = -1
    private var lastStatusDigest: Data?
    private var tickTimer: Timer?
    private var tickInterval: TimeInterval = 0.25
    private var statusItem: NSStatusItem?
    private var lastMode: String = "expanded"
    private var lastHiddenRequested: Bool = false
    // Hide/show hotkey (contract 1.3/1.6) + the optimistic direction of a flip
    // that the Python loop has not published back into the config yet.
    private var hotKeyRef: EventHotKeyRef?
    private var hotKeyHandlerRef: EventHandlerRef?
    private var attemptedHotKey: String = ""
    private var hotKeyFlipGate = HotKeyFlipGate()
    private var pendingHiddenRequest: Bool?

    /// The combo currently armed by Carbon, or "" when none is registered.
    private var registeredHotKey: String {
        hotKeyRef != nil ? attemptedHotKey : ""
    }

    deinit {
        // Contract 1.3(c): the overlay stop path tears the helper down, so the
        // hotkey must go with it — a stopped overlay never keeps a global key.
        stopHotKeys()
    }

    init(configPath: String) {
        self.configPath = configPath
        let root = (configPath as NSString).deletingLastPathComponent
        self.statusPath = root + "/overlay-helper-status.json"
        self.sfxPath = root + "/overlay-sfx-request.json"
        self.interactionPath = root + "/overlay-interaction-request.json"
        super.init()
        buildPanel()
        buildStatusItem()
        // Contract 1.3: the hotkey is registered at construction rather than on
        // first use, so a panel that starts hidden can still be brought back.
        registerConfiguredHotKey(readConfig())
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(accessibilityOptionsChanged),
            name: NSWorkspace.accessibilityDisplayOptionsDidChangeNotification,
            object: nil
        )
        scheduleTick(0.25)
        tick()
    }

    private func buildPanel() {
        let initialFrame = NSRect(x: 0, y: 0, width: 260, height: 120)
        panel = NonActivatingPanel(
            contentRect: initialFrame,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.isReleasedWhenClosed = false
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient, .ignoresCycle]
        panel.ignoresMouseEvents = true

        content = NSView(frame: initialFrame)
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.clear.cgColor
        panel.contentView = content

        // Chrome goes in FIRST so the WebView stays the topmost content layer:
        // the glass/vibrancy material is chrome *behind* transparent HTML, never
        // part of the content layer itself.
        refreshChrome(mode: "expanded")

        let configuration = WKWebViewConfiguration()
        configuration.userContentController.add(self, name: "tamahermes")
        configuration.suppressesIncrementalRendering = false
        webView = WKWebView(frame: content.bounds, configuration: configuration)
        webView.autoresizingMask = [.width, .height]
        webView.wantsLayer = true
        webView.setValue(false, forKey: "drawsBackground")
        webView.layer?.backgroundColor = NSColor.clear.cgColor
        if #available(macOS 12.0, *) {
            webView.underPageBackgroundColor = .clear
        }
        content.addSubview(webView)
    }

    // MARK: - Chrome (three-step degradation chain)

    private var reduceTransparency: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldReduceTransparency
    }

    private var increaseContrast: Bool {
        NSWorkspace.shared.accessibilityDisplayShouldIncreaseContrast
    }

    private var darkModeActive: Bool {
        NSApp.effectiveAppearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua
    }

    private func cornerRadius(forMode mode: String) -> CGFloat {
        // Expanded panel radius and the fully rounded pill (design lock).
        mode == "collapsed" ? 19 : 16
    }

    private func desiredChromeKind() -> String {
        if reduceTransparency || increaseContrast {
            // Hard switch: no translucency at all under the a11y settings.
            return "opaque"
        }
        #if EVOPET_GLASS
        if #available(macOS 26.0, *) {
            return "glass"
        }
        #endif
        return "vibrancy"
    }

    @discardableResult
    private func refreshChrome(mode: String) -> Bool {
        let kind = desiredChromeKind()
        let radius = cornerRadius(forMode: mode)
        if kind == chromeKind, radius == chromeCornerRadius, chromeView != nil {
            return false
        }
        chromeView?.removeFromSuperview()
        let chrome = makeChrome(kind: kind, cornerRadius: radius)
        chrome.frame = content.bounds
        chrome.autoresizingMask = [.width, .height]
        content.addSubview(chrome, positioned: .below, relativeTo: webView)
        chromeView = chrome
        chromeKind = kind
        chromeCornerRadius = radius
        return true
    }

    private func makeChrome(kind: String, cornerRadius: CGFloat) -> NSView {
        let host = NSView()
        host.wantsLayer = true
        host.layer?.cornerRadius = cornerRadius
        host.layer?.masksToBounds = true
        switch kind {
        case "glass":
            #if EVOPET_GLASS
            if #available(macOS 26.0, *) {
                let glass = NSGlassEffectView()
                glass.style = .regular
                glass.cornerRadius = cornerRadius
                glass.contentView = NSView()
                if #available(macOS 27.0, *) {
                    glass.effectIsInteractive = true
                }
                let container = NSGlassEffectContainerView()
                container.spacing = 0
                container.contentView = glass
                container.wantsLayer = true
                host.layer?.backgroundColor = NSColor.clear.cgColor
                host.addSubview(container)
                container.frame = host.bounds
                container.autoresizingMask = [.width, .height]
                return host
            }
            #endif
            return makeChrome(kind: "vibrancy", cornerRadius: cornerRadius)
        case "vibrancy":
            let effect = NSVisualEffectView()
            effect.material = .hudWindow
            effect.blendingMode = .behindWindow
            effect.state = .active
            host.layer?.backgroundColor = NSColor.clear.cgColor
            host.addSubview(effect)
            effect.frame = host.bounds
            effect.autoresizingMask = [.width, .height]
            return host
        default:
            // Opaque backing (also the terminal fallback): a never-transparent
            // panel so text stays readable over any desktop.
            host.layer?.backgroundColor = NSColor.windowBackgroundColor.cgColor
            return host
        }
    }

    @objc private func accessibilityOptionsChanged() {
        // Force the next tick to rebuild the chrome for the new a11y flags;
        // the status digest changes too, so the mirror file is rewritten.
        chromeKind = ""
    }

    // MARK: - Config and geometry

    private func readConfig() -> OverlayConfig? {
        do {
            let data = try Data(contentsOf: URL(fileURLWithPath: configPath))
            return try JSONDecoder().decode(OverlayConfig.self, from: data)
        } catch {
            return nil
        }
    }

    private func screen(forTopLeftX x: Double?, topLeftY y: Double?) -> NSScreen {
        guard let x = x, let y = y else {
            return NSScreen.main ?? NSScreen.screens.first!
        }
        for screen in NSScreen.screens {
            let topLeft = NSPoint(x: x, y: screen.frame.maxY - y)
            if screen.frame.contains(topLeft) {
                return screen
            }
        }
        return NSScreen.main ?? NSScreen.screens.first!
    }

    private func clampedFrame(for config: OverlayConfig) -> NSRect {
        let mode = config.mode ?? "expanded"
        let scale = max(0.75, min(1.75, config.scale ?? 1.0))
        // The pill is not user-scalable: collapsed mode skips the scale multiply
        // and brings its own floors, so a 38 pt pill is never clamped away.
        let modeScale = mode == "collapsed" ? 1.0 : scale
        let width = max(config.minWidth ?? 120, (config.width ?? 260) * modeScale)
        let height = max(config.minHeight ?? 80, (config.height ?? 120) * modeScale)
        let screen = screen(forTopLeftX: config.x, topLeftY: config.y)
        let frame = screen.visibleFrame
        let rawX = config.x ?? (frame.maxX - width - 18)
        let rawTopY = config.y ?? (screen.frame.maxY - frame.minY - height - 72)
        let rawY = screen.frame.maxY - rawTopY - height
        let x = min(max(rawX, frame.minX + 8), frame.maxX - width - 8)
        let y = min(max(rawY, frame.minY + 8), frame.maxY - height - 8)
        return NSRect(x: x, y: y, width: width, height: height)
    }

    private func mouseTopLeftPoint() -> NSPoint {
        let mouse = NSEvent.mouseLocation
        for screen in NSScreen.screens {
            if screen.frame.contains(mouse) {
                return NSPoint(x: mouse.x, y: screen.frame.maxY - mouse.y)
            }
        }
        let screen = NSScreen.main ?? NSScreen.screens.first!
        return NSPoint(x: mouse.x, y: screen.frame.maxY - mouse.y)
    }

    private func hoverReady(_ config: OverlayConfig, point: NSPoint) -> Bool {
        guard
            let x = config.hoverX,
            let y = config.hoverY,
            let width = config.hoverWidth,
            let height = config.hoverHeight,
            width > 0,
            height > 0
        else {
            hoverStartedAt = nil
            return false
        }

        let padding = 16.0
        let inside = point.x >= x - padding
            && point.x <= x + width + padding
            && point.y >= y - padding
            && point.y <= y + height + padding
        if !inside {
            hoverStartedAt = nil
            return false
        }

        let started = hoverStartedAt ?? Date()
        hoverStartedAt = started
        return Date().timeIntervalSince(started) >= (config.hoverDelaySeconds ?? 1.0)
    }

    // MARK: - Status mirror

    private func writeStatus(config: OverlayConfig, point: NSPoint, ready: Bool) {
        func jsonValue(_ value: Double?) -> Any {
            guard let value = value else { return NSNull() }
            return value
        }
        let payload: [String: Any] = [
            "schema": "tamahermes.native_overlay.helper_status.v1",
            "visibleRequested": config.visible == true,
            "hoverReady": ready,
            "panelVisible": panel.isVisible,
            "mouseX": point.x,
            "mouseY": point.y,
            "hoverX": jsonValue(config.hoverX),
            "hoverY": jsonValue(config.hoverY),
            "hoverWidth": jsonValue(config.hoverWidth),
            "hoverHeight": jsonValue(config.hoverHeight),
            "lastSfxId": lastSfxId ?? NSNull(),
            "lastSfxFilename": lastSfxFilename ?? NSNull(),
            "lastSfxPlayedAt": lastSfxPlayedAt ?? NSNull(),
            "lastSfxError": lastSfxError ?? NSNull(),
            // Additive a11y/appearance mirror (schema id unchanged): CSS cannot
            // read Reduce Transparency / Increase Contrast, so the helper
            // reports what the native chrome is actually drawing.
            "mode": config.mode ?? "expanded",
            // Contract 1.4: what the helper was asked to register, and what
            // Carbon actually armed. A refused combo reads back as null rather
            // than silently claiming a hotkey that is not there.
            "hideHotkey": config.hideHotkey ?? NSNull(),
            "hotkey": registeredHotKey.isEmpty ? NSNull() : registeredHotKey,
            "reduceTransparency": reduceTransparency,
            "increaseContrast": increaseContrast,
            "darkMode": darkModeActive,
            "updatedAt": ISO8601DateFormatter().string(from: Date()),
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted, .sortedKeys]) else {
            return
        }
        // The digest skips `updatedAt` only: an idle helper writes the status
        // file once instead of 4 times a second.
        var stable = payload
        stable.removeValue(forKey: "updatedAt")
        let digest = (try? JSONSerialization.data(withJSONObject: stable, options: [.sortedKeys])) ?? Data()
        if digest == lastStatusDigest {
            return
        }
        lastStatusDigest = digest
        try? data.write(to: URL(fileURLWithPath: statusPath))
    }

    // MARK: - Interaction file (helper -> Python)

    private func adjustScale(by delta: Double) {
        guard let data = FileManager.default.contents(atPath: configPath),
              var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        let current = (object["scale"] as? NSNumber)?.doubleValue ?? 1.0
        object["scale"] = max(0.75, min(1.75, current + delta))
        guard JSONSerialization.isValidJSONObject(object), let output = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted]) else { return }
        try? output.write(to: URL(fileURLWithPath: configPath + ".tmp"))
        _ = try? FileManager.default.replaceItemAt(URL(fileURLWithPath: configPath), withItemAt: URL(fileURLWithPath: configPath + ".tmp"))
    }

    private func writeInteraction(event: String) {
        let payload: [String: Any] = [
            "schema": "tamahermes.native_overlay.interaction.v1",
            "id": UUID().uuidString,
            "event": event,
            "updatedAt": Date().timeIntervalSince1970,
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]) else { return }
        let temporary = interactionPath + ".tmp"
        try? data.write(to: URL(fileURLWithPath: temporary))
        let destination = URL(fileURLWithPath: interactionPath)
        if FileManager.default.fileExists(atPath: interactionPath) {
            _ = try? FileManager.default.replaceItemAt(destination, withItemAt: URL(fileURLWithPath: temporary))
        } else {
            _ = try? FileManager.default.moveItem(at: URL(fileURLWithPath: temporary), to: destination)
        }
    }

    // MARK: - Global hide/show hotkey (Carbon registration, contract 1.3)

    /// Register the combo published by the Python loop in the config payload.
    ///
    /// Idempotent per requested combo: a tick that sees the same `hideHotkey`
    /// does no Carbon work. A combo that will not parse falls back to the
    /// documented default, and a modifier-less combo is refused outright — the
    /// helper then runs with no hotkey rather than grabbing a bare key from
    /// every other application.
    private func registerConfiguredHotKey(_ config: OverlayConfig?) {
        let configured = config?.hideHotkey?.trimmingCharacters(in: .whitespaces) ?? ""
        let requested = configured.isEmpty ? HotKeyCombo.documentedDefault : configured
        guard requested != attemptedHotKey else { return }
        attemptedHotKey = requested
        unregisterHotKey()
        guard let combo = HotKeyCombo.parse(requested) ?? HotKeyCombo.parse(HotKeyCombo.documentedDefault) else { return }
        installHotKeyHandlerIfNeeded()
        guard hotKeyHandlerRef != nil else { return }
        var reference: EventHotKeyRef?
        let status = RegisterEventHotKey(
            combo.keyCode,
            combo.modifiers,
            EventHotKeyID(signature: HotKeyCombo.signature, id: 1),
            GetApplicationEventTarget(),
            0,
            &reference
        )
        guard status == noErr, let reference else { return }
        hotKeyRef = reference
        hotKeyFlipGate.reset()
    }

    private func installHotKeyHandlerIfNeeded() {
        guard hotKeyHandlerRef == nil else { return }
        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let status = InstallEventHandler(
            GetApplicationEventTarget(),
            OverlayController.hotKeyEventHandler,
            1,
            &eventType,
            Unmanaged.passUnretained(self).toOpaque(),
            &hotKeyHandlerRef
        )
        if status != noErr {
            hotKeyHandlerRef = nil
        }
    }

    /// Unregister the combo (the process keeps running; used by the kill-switch).
    private func unregisterHotKey() {
        if let reference = hotKeyRef {
            UnregisterEventHotKey(reference)
        }
        hotKeyRef = nil
    }

    /// Contract 1.3(c): the kill-switch state. The registration goes first, then
    /// the gate refuses every later flip.
    func stopHotKeys() {
        hotKeyFlipGate.stop()
        unregisterHotKey()
        if let handler = hotKeyHandlerRef {
            RemoveEventHandler(handler)
            hotKeyHandlerRef = nil
        }
        attemptedHotKey = ""
        pendingHiddenRequest = nil
    }

    /// Carbon delivers a registered hotkey as an ordinary application event: the
    /// overlay only reads the one it registered, and nothing is captured or
    /// swallowed on the way to the foreground application.
    fileprivate func handleHotKeyEvent(_ event: EventRef?) {
        guard hotKeyRef != nil else { return }  // stopped/unregistered: ignore
        guard let event else { return }
        var identifier = EventHotKeyID()
        let status = GetEventParameter(
            event,
            EventParamName(kEventParamDirectObject),
            EventParamType(typeEventHotKeyID),
            nil,
            MemoryLayout<EventHotKeyID>.size,
            nil,
            &identifier
        )
        guard status == noErr, identifier.signature == HotKeyCombo.signature else { return }
        toggleHiddenRequest()
    }

    /// Flip the panel through the existing interaction file — the same channel
    /// the HUD buttons and the status menu use, so the Python loop stays the one
    /// writer of `hudHidden`. At most one flip per cooldown window (contract
    /// 1.6): a held key auto-repeats, and repeats must not flicker the panel.
    private func toggleHiddenRequest() {
        guard hotKeyFlipGate.allowsFlip(now: Date().timeIntervalSince1970) else { return }
        let event = (pendingHiddenRequest ?? lastHiddenRequested) ? "show" : "hide"
        writeInteraction(event: event)
        pendingHiddenRequest = (event == "hide")
    }

    private static let hotKeyEventHandler: EventHandlerUPP = { _, event, userData in
        guard let userData else { return noErr }
        Unmanaged<OverlayController>.fromOpaque(userData).takeUnretainedValue().handleHotKeyEvent(event)
        return noErr
    }

    // MARK: - Drag (screen-space math, preserved from the live build)

    private func beginDrag() {
        endDrag()
        dragOrigin = panel.frame.origin
        dragMouseOrigin = NSEvent.mouseLocation
        dragLastEventAt = Date()
        // All drag math stays in global screen coordinates (Cocoa base):
        // NSEvent.mouseLocation is screen-space, matching panel.frame.origin.
        // event.locationInWindow must NOT be mixed in here (window-local space).
        let handler: (NSEvent) -> NSEvent? = { [weak self] event in
            guard let self else { return event }
            if event.type == .leftMouseUp {
                self.persistPanelPosition()
                self.endDrag()
            } else if event.type == .leftMouseDragged,
                      let origin = self.dragOrigin,
                      let mouse = self.dragMouseOrigin {
                let current = NSEvent.mouseLocation
                self.dragLastEventAt = Date()
                self.panel.setFrameOrigin(NSPoint(x: origin.x + current.x - mouse.x, y: origin.y + current.y - mouse.y))
                self.persistPanelPosition()
            }
            return event
        }
        dragMonitor = NSEvent.addLocalMonitorForEvents(matching: [.leftMouseDragged, .leftMouseUp], handler: handler)
        if dragMonitor == nil {
            dragMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDragged, .leftMouseUp]) { [weak self] event in
                guard let self else { return }
                if event.type == .leftMouseUp {
                    self.persistPanelPosition()
                    self.endDrag()
                    return
                }
                guard event.type == .leftMouseDragged,
                      let origin = self.dragOrigin,
                      let mouse = self.dragMouseOrigin else { return }
                let current = NSEvent.mouseLocation
                self.dragLastEventAt = Date()
                self.panel.setFrameOrigin(NSPoint(x: origin.x + current.x - mouse.x, y: origin.y + current.y - mouse.y))
                self.persistPanelPosition()
            }
        }
    }

    private func endDrag() {
        if let monitor = dragMonitor { NSEvent.removeMonitor(monitor) }
        dragMonitor = nil
        dragOrigin = nil
        dragMouseOrigin = nil
        dragLastEventAt = nil
    }

    private func persistPanelPosition() {
        let screen = panel.screen ?? NSScreen.main ?? NSScreen.screens.first!
        let origin = panel.frame.origin
        let topLeftY = screen.frame.maxY - origin.y - panel.frame.height
        guard let data = FileManager.default.contents(atPath: configPath),
              var object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { return }
        object["x"] = origin.x
        object["y"] = topLeftY
        guard JSONSerialization.isValidJSONObject(object), let output = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted]) else { return }
        try? output.write(to: URL(fileURLWithPath: configPath + ".tmp"))
        _ = try? FileManager.default.replaceItemAt(URL(fileURLWithPath: configPath), withItemAt: URL(fileURLWithPath: configPath + ".tmp"))
    }

    private func movePanel(dx: Double, dy: Double) {
        panel.setFrameOrigin(NSPoint(x: panel.frame.origin.x + dx, y: panel.frame.origin.y - dy))
        persistPanelPosition()
    }

    // MARK: - WebView messages

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "tamahermes",
              let body = message.body as? [String: Any],
              let event = body["event"] as? String else { return }
        if event == "scale-up" { adjustScale(by: 0.1); return }
        if event == "scale-down" { adjustScale(by: -0.1); return }
        if event == "drag-start" { beginDrag(); return }
        if event == "drag-end" { persistPanelPosition(); endDrag(); return }
        if event == "drag",
           let dx = body["dx"] as? Double,
           let dy = body["dy"] as? Double {
            movePanel(dx: dx, dy: dy)
            return
        }
        if ["hide", "show", "collapse", "expand"].contains(event) { writeInteraction(event: event); return }
        guard ["care", "feed", "clean", "play", "rest"].contains(event) else { return }
        writeInteraction(event: event)
    }

    // MARK: - Menu-bar global toggle

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = item.button {
            let image = NSImage(systemSymbolName: "tortoise.fill", accessibilityDescription: "EvoPet HUD")
            image?.isTemplate = true
            button.image = image
            button.toolTip = "EvoPet HUD"
        }
        let menu = NSMenu()
        menu.autoenablesItems = false
        for (title, event) in [
            ("Expand HUD", "expand"),
            ("Collapse to pill", "collapse"),
            ("Hide HUD", "hide"),
            ("Show HUD", "show"),
        ] {
            let menuItem = NSMenuItem(title: title, action: #selector(statusMenuAction(_:)), keyEquivalent: "")
            menuItem.target = self
            menuItem.representedObject = event
            menu.addItem(menuItem)
        }
        menu.addItem(NSMenuItem.separator())
        item.menu = menu
        statusItem = item
    }

    @objc private func statusMenuAction(_ sender: NSMenuItem) {
        guard let event = sender.representedObject as? String else { return }
        // The status item never keys the panel and never activates the app: it
        // only writes the same interaction file the WebView writes.
        writeInteraction(event: event)
    }

    private func updateStatusMenu() {
        guard let menu = statusItem?.menu else { return }
        let hidden = lastHiddenRequested
        for item in menu.items {
            switch item.representedObject as? String {
            case "expand": item.isEnabled = !hidden && lastMode == "collapsed"
            case "collapse": item.isEnabled = !hidden && lastMode == "expanded"
            case "hide": item.isEnabled = !hidden
            case "show": item.isEnabled = hidden
            default: break
            }
        }
    }

    // MARK: - Rendering and reload (hash-guarded, preserved)

    private func contentHash(of url: URL) -> UInt64? {
        guard let data = try? Data(contentsOf: url) else { return nil }
        var hash: UInt64 = 14_619_821_125_433_631
        for byte in data {
            hash ^= UInt64(byte)
            hash &*= 1_096_133_665_776_312_903
        }
        return hash
    }

    private func reloadIfNeeded(htmlPath: String?) {
        guard let htmlPath = htmlPath else { return }
        let url = URL(fileURLWithPath: htmlPath)
        let modified = (try? FileManager.default.attributesOfItem(atPath: htmlPath)[.modificationDate]) as? Date
        if htmlPath == lastHTMLPath && modified == lastHTMLModified {
            return
        }
        // Python may rewrite overlay.html between modes; mtime alone would
        // reload (and flicker) when only the timestamp changed. Compare content
        // hash and skip the reload for identical bytes.
        let hash = contentHash(of: url)
        if htmlPath == lastHTMLPath && hash != nil && hash == lastHTMLHash {
            lastHTMLModified = modified
            return
        }
        lastHTMLPath = htmlPath
        lastHTMLModified = modified
        lastHTMLHash = hash
        webView.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    private func playSfxIfNeeded() {
        guard
            let data = try? Data(contentsOf: URL(fileURLWithPath: sfxPath)),
            let request = try? JSONDecoder().decode(SfxRequest.self, from: data),
            let requestId = request.id,
            requestId != lastSfxId,
            let filePath = request.filePath,
            (request.expiresAt ?? Date().timeIntervalSince1970 + 1.0) >= Date().timeIntervalSince1970
        else {
            return
        }
        lastSfxId = requestId
        let url = URL(fileURLWithPath: filePath)
        guard let sound = NSSound(contentsOf: url, byReference: true) else {
            lastSfxFilename = url.lastPathComponent
            lastSfxError = "load-failed"
            return
        }
        sound.volume = Float(max(0.0, min(1.0, request.volume ?? 1.0)))
        activeSounds.append(sound)
        if sound.play() {
            lastSfxFilename = url.lastPathComponent
            lastSfxPlayedAt = ISO8601DateFormatter().string(from: Date())
            lastSfxError = nil
        } else {
            lastSfxFilename = url.lastPathComponent
            lastSfxError = "play-failed"
        }
        activeSounds.removeAll { !$0.isPlaying }
    }

    // MARK: - Tick

    private func scheduleTick(_ interval: TimeInterval) {
        guard tickTimer == nil || interval != tickInterval else { return }
        tickInterval = interval
        tickTimer?.invalidate()
        tickTimer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    @objc private func tick() {
        guard let config = readConfig() else {
            panel.orderOut(nil)
            return
        }
        // Cadence: 0.25 s keeps dragging smooth while the panel is on screen;
        // an invisible panel only needs to wake for a state change once a
        // second. Same single timer, interval adjusted by visibility.
        scheduleTick(panel.isVisible ? 0.25 : 1.0)
        playSfxIfNeeded()
        panel.ignoresMouseEvents = !(config.visible == true)
        if dragMonitor != nil {
            // Watchdog: guarantee drag sessions end even if mouse-up was lost
            // (monitor dropped, event swallowed). Clicks are unaffected: they
            // only open a session via drag-start and close on mouse-up above.
            let mouseReleased = NSEvent.pressedMouseButtons == 0
            let stale = dragLastEventAt.map { Date().timeIntervalSince($0) > 10 } ?? false
            if mouseReleased || stale {
                persistPanelPosition()
                endDrag()
            }
        }
        let mode = config.mode ?? "expanded"
        if refreshChrome(mode: mode) {
            chromeView?.frame = content.bounds
        }
        if dragMonitor == nil {
            panel.setFrame(clampedFrame(for: config), display: true)
        }
        reloadIfNeeded(htmlPath: config.htmlPath)
        let point = mouseTopLeftPoint()
        let hasHoverTarget = config.hoverX != nil && config.hoverY != nil && config.hoverWidth != nil && config.hoverHeight != nil
        let ready = config.visible == true && (!hasHoverTarget || hoverReady(config, point: point))
        if ready {
            if !panel.isVisible {
                panel.orderFrontRegardless()
            }
        } else {
            panel.orderOut(nil)
        }
        lastMode = mode
        lastHiddenRequested = config.visible != true
        if pendingHiddenRequest == lastHiddenRequested {
            // The loop has published the flip the hotkey asked for: drop the
            // optimistic override so the next press reads the real state again.
            pendingHiddenRequest = nil
        }
        registerConfiguredHotKey(config)
        updateStatusMenu()
        writeStatus(config: config, point: point, ready: ready)
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write(Data("usage: TamaHermesOverlay <config.json>\n".utf8))
    exit(2)
}

let controller = OverlayController(configPath: CommandLine.arguments[1])

// Contract 1.3(c) kill-switch: `tamahermes overlay stop` reaches the helper as
// SIGTERM from the sidecar loop. The hotkey is surrendered first, then the
// default disposition is restored and re-raised so the process still reports as
// signal-terminated to anything watching it (an ignored signal would let the
// registration outlive a stopped overlay).
signal(SIGTERM, SIG_IGN)
let terminationSource = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
terminationSource.setEventHandler {
    controller.stopHotKeys()
    signal(SIGTERM, SIG_DFL)
    raise(SIGTERM)
}
terminationSource.resume()

app.run()
