import AppKit
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
}

struct SfxRequest: Decodable {
    let id: String?
    let filePath: String?
    let volume: Double?
    let expiresAt: Double?
}

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

    init(configPath: String) {
        self.configPath = configPath
        let root = (configPath as NSString).deletingLastPathComponent
        self.statusPath = root + "/overlay-helper-status.json"
        self.sfxPath = root + "/overlay-sfx-request.json"
        self.interactionPath = root + "/overlay-interaction-request.json"
        super.init()
        buildPanel()
        buildStatusItem()
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
app.run()
