import AppKit
import Foundation
import WebKit

struct OverlayConfig: Decodable {
    let visible: Bool?
    let x: Double?
    let y: Double?
    let width: Double?
    let height: Double?
    let htmlPath: String?
    let hoverX: Double?
    let hoverY: Double?
    let hoverWidth: Double?
    let hoverHeight: Double?
    let hoverDelaySeconds: Double?
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
    private var lastHTMLPath: String = ""
    private var lastHTMLModified: Date?
    private var hoverStartedAt: Date?
    private var lastSfxId: String?
    private var lastSfxFilename: String?
    private var lastSfxPlayedAt: String?
    private var lastSfxError: String?
    private var activeSounds: [NSSound] = []

    init(configPath: String) {
        self.configPath = configPath
        let root = (configPath as NSString).deletingLastPathComponent
        self.statusPath = root + "/overlay-helper-status.json"
        self.sfxPath = root + "/overlay-sfx-request.json"
        self.interactionPath = root + "/overlay-interaction-request.json"
        super.init()
        buildPanel()
        Timer.scheduledTimer(withTimeInterval: 0.25, repeats: true) { [weak self] _ in
            self?.tick()
        }
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

        let content = NSView(frame: initialFrame)
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor.clear.cgColor
        panel.contentView = content

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
        let width = max(120, config.width ?? 260)
        let height = max(80, config.height ?? 120)
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
            "updatedAt": ISO8601DateFormatter().string(from: Date()),
        ]
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted]) else {
            return
        }
        try? data.write(to: URL(fileURLWithPath: statusPath))
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "tamahermes",
              let body = message.body as? [String: Any],
              let event = body["event"] as? String,
              ["care", "feed", "rest"].contains(event) else { return }
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

    private func reloadIfNeeded(htmlPath: String?) {
        guard let htmlPath = htmlPath else { return }
        let url = URL(fileURLWithPath: htmlPath)
        let modified = (try? FileManager.default.attributesOfItem(atPath: htmlPath)[.modificationDate]) as? Date
        if htmlPath == lastHTMLPath && modified == lastHTMLModified {
            return
        }
        lastHTMLPath = htmlPath
        lastHTMLModified = modified
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

    @objc private func tick() {
        guard let config = readConfig() else {
            panel.orderOut(nil)
            return
        }
        playSfxIfNeeded()
        panel.ignoresMouseEvents = !(config.visible == true)
        panel.setFrame(clampedFrame(for: config), display: true)
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
