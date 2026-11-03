import Cocoa
import Darwin
import Foundation

// MARK: - Models & Config

struct ArmTelemetry {
    var name: String
    var ip: String
    var joints: [Double] = [0.0, -89.5, 89.0, 0.0, 5.0, 0.0, 0.0]
    var online: Bool = false
    var color: NSColor
    var roiNormalized: NSRect // Normalized (0..1) in top-left origin image coords (2304x1296)
}

enum StreamMode: Int, CaseIterable {
    case rtspSub = 0
    case rtspHD = 1
    case hubHTTP = 2
    case insta360Rectilinear = 3
    case insta360HUD = 4

    var title: String {
        switch self {
        case .rtspSub: return "RTSP 流畅 (20fps)"
        case .rtspHD:  return "RTSP 超清 (2K)"
        case .hubHTTP: return "4GPU AI融合流"
        case .insta360Rectilinear: return "Insta360 针孔无畸变侧视 (1440×900)"
        case .insta360HUD: return "Insta360 360°全景五联HUD (1920×1080)"
        }
    }

    var shortTitle: String {
        switch self {
        case .rtspSub: return "流畅20fps"
        case .rtspHD:  return "超清2K"
        case .hubHTTP: return "AI流"
        case .insta360Rectilinear: return "Insta360侧视"
        case .insta360HUD: return "Insta360五联"
        }
    }
}

final class MonitorState {
    let lock = NSLock()

    var currentImage: NSImage?
    var fps: Double = 0.0
    var frameCount: UInt64 = 0
    var lastFrameTime: TimeInterval = 0
    var streamConnected: Bool = false
    var streamStatusText: String = "正在连接视频流..."

    var showROIOverlay: Bool = true
    var showBottomTelemetry: Bool = true
    var showInsta360PiP: Bool = true
    var alwaysOnTop: Bool = true
    var streamMode: StreamMode = .rtspSub

    // Insta360 X4 Air (10.10.137.106) live state
    var insta360PanoImage: NSImage?
    var insta360RectImage: NSImage?
    var insta360HUDImage: NSImage?
    var insta360Online: Bool = false
    var insta360Frames: Int = 42665
    var insta360AgeMs: Int = 15
    var insta360ZClearanceMm: Double = 86.5

    var continuousMode: Bool = false
    var cycleCount: Int = 0
    var currentSkill: String = "grasp_and_plug_loop"
    var currentPhase: String = "实时遥测监控就绪"

    var arms: [String: ArmTelemetry] = [
        "left_103": ArmTelemetry(
            name: "左臂103",
            ip: "10.10.137.103",
            color: NSColor(calibratedRed: 0.06, green: 0.73, blue: 0.51, alpha: 1.0),
            roiNormalized: NSRect(x: 150.0 / 2304.0, y: 340.0 / 1296.0, width: 490.0 / 2304.0, height: 840.0 / 1296.0)
        ),
        "center_102": ArmTelemetry(
            name: "中臂102",
            ip: "10.10.137.102",
            color: NSColor(calibratedRed: 0.23, green: 0.51, blue: 0.96, alpha: 1.0),
            roiNormalized: NSRect(x: 680.0 / 2304.0, y: 310.0 / 1296.0, width: 660.0 / 2304.0, height: 870.0 / 1296.0)
        ),
        "right_101": ArmTelemetry(
            name: "右臂101",
            ip: "10.10.137.101",
            color: NSColor(calibratedRed: 0.66, green: 0.33, blue: 0.97, alpha: 1.0),
            roiNormalized: NSRect(x: 1480.0 / 2304.0, y: 290.0 / 1296.0, width: 520.0 / 2304.0, height: 860.0 / 1296.0)
        )
    ]
}

// MARK: - Custom Draggable Floating Panel

final class FloatingMonitorPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

// MARK: - Video + Vector HUD Canvas View

final class VideoHUDView: NSView {
    let state: MonitorState
    var onDoubleClick: (() -> Void)?
    private var initialMouseLocation: NSPoint = .zero
    private var initialWindowOrigin: NSPoint = .zero

    init(state: MonitorState) {
        self.state = state
        super.init(frame: .zero)
        self.wantsLayer = true
        self.layer?.backgroundColor = NSColor(calibratedRed: 0.04, green: 0.06, blue: 0.10, alpha: 1.0).cgColor
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    override var mouseDownCanMoveWindow: Bool { true }

    override func mouseDown(with event: NSEvent) {
        if event.clickCount == 2 {
            onDoubleClick?()
            return
        }
        guard let win = self.window else { return }
        initialMouseLocation = NSEvent.mouseLocation
        initialWindowOrigin = win.frame.origin
    }

    override func mouseDragged(with event: NSEvent) {
        guard let win = self.window else { return }
        let current = NSEvent.mouseLocation
        let dx = current.x - initialMouseLocation.x
        let dy = current.y - initialMouseLocation.y
        win.setFrameOrigin(NSPoint(x: initialWindowOrigin.x + dx, y: initialWindowOrigin.y + dy))
    }

    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        guard let ctx = NSGraphicsContext.current?.cgContext else { return }

        let bounds = self.bounds
        ctx.setFillColor(NSColor(calibratedRed: 0.04, green: 0.06, blue: 0.10, alpha: 1.0).cgColor)
        ctx.fill(bounds)

        state.lock.lock()
        let rawImg = state.currentImage
        let instaPano = state.insta360PanoImage
        let instaRect = state.insta360RectImage
        let instaHUD = state.insta360HUDImage
        let instaOnline = state.insta360Online
        let instaFrames = state.insta360Frames
        let instaAgeMs = state.insta360AgeMs
        let instaZ = state.insta360ZClearanceMm
        let showROI = state.showROIOverlay
        let showBottom = state.showBottomTelemetry
        let showInstaPiP = state.showInsta360PiP
        let fps = state.fps
        let connected = state.streamConnected
        let statusText = state.streamStatusText
        let mode = state.streamMode
        let cyc = state.cycleCount
        let skill = state.currentSkill
        let phase = state.currentPhase
        let cont = state.continuousMode
        let arms = state.arms
        state.lock.unlock()

        let img: NSImage?
        switch mode {
        case .insta360HUD:
            img = instaHUD ?? instaRect ?? rawImg
        case .insta360Rectilinear:
            img = instaRect ?? instaPano ?? rawImg
        default:
            img = rawImg
        }

        // 1. Draw Live Video Frame below the 36px top header bar so camera OSD timestamp is 100% visible
        let headerOffset: CGFloat = 36
        let videoRect = NSRect(x: 0, y: 0, width: bounds.width, height: max(60, bounds.height - headerOffset))
        if let image = img {
            image.draw(
                in: videoRect,
                from: .zero,
                operation: .sourceOver,
                fraction: 1.0,
                respectFlipped: true,
                hints: [.interpolation: NSNumber(value: NSImageInterpolation.high.rawValue)]
            )
        } else {
            // Loading placeholder
            let pStyle = NSMutableParagraphStyle()
            pStyle.alignment = .center
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.boldSystemFont(ofSize: 15),
                .foregroundColor: NSColor(calibratedRed: 0.58, green: 0.64, blue: 0.72, alpha: 1.0),
                .paragraphStyle: pStyle
            ]
            let str = "📡 \(statusText)\n(支持鼠标任意位置拖拽移动 · 双击切换大小 · 右键菜单控制)"
            str.draw(in: NSRect(x: 20, y: videoRect.midY - 24, width: bounds.width - 40, height: 52), withAttributes: attrs)
        }

        let hasInstaPiP = showInstaPiP && (mode == .rtspSub || mode == .rtspHD || mode == .hubHTTP)
            && (instaPano != nil || instaRect != nil)

        // 2. Vector ROI Overlay (only when enabled on overhead RTSP stream)
        if showROI && (mode == .rtspSub || mode == .rtspHD) {
            drawROIBoxes(in: videoRect, arms: arms, avoidTopLeftPiP: hasInstaPiP)
        }

        // 2b. Insta360 X4 Air Dual-View Picture-in-Picture (360° Panorama + Rectilinear Pinhole Side View)
        if hasInstaPiP {
            drawInsta360PiPCard(
                in: videoRect,
                pano: instaPano,
                rectView: instaRect,
                online: instaOnline,
                frames: instaFrames,
                ageMs: instaAgeMs,
                zClearanceMm: instaZ
            )
        }

        // 3. Status Pill in Top-Right of videoRect (keeps top-left camera OSD timestamp 100% clear)
        let effectiveConnected = (mode == .insta360HUD || mode == .insta360Rectilinear) ? (img != nil) : connected
        let effectiveFPS = (mode == .insta360HUD || mode == .insta360Rectilinear) ? 30.0 : fps
        drawLiveStatusBadge(
            in: videoRect,
            connected: effectiveConnected,
            fps: effectiveFPS,
            mode: mode,
            continuous: cont,
            cycle: cyc
        )

        // 4. Bottom Telemetry Overlay Bar
        if showBottom {
            drawBottomTelemetryBar(
                in: videoRect,
                arms: arms,
                skill: skill,
                phase: phase,
                cycle: cyc,
                continuous: cont,
                instaOnline: instaOnline,
                instaFrames: instaFrames,
                instaAgeMs: instaAgeMs,
                instaZ: instaZ
            )
        }
    }

    private func drawInsta360PiPCard(
        in bounds: NSRect,
        pano: NSImage?,
        rectView: NSImage?,
        online: Bool,
        frames: Int,
        ageMs: Int,
        zClearanceMm: Double
    ) {
        let cardW = min(max(bounds.width * 0.36, 236), 310)
        let headerH: CGFloat = 17
        let subW = (cardW - 12) / 2.0
        let subH = round(subW * 0.56)
        let cardH = headerH + subH + 8
        // Place below the top-left camera OSD timestamp (leaves top 38px completely clear)
        let cardRect = NSRect(x: bounds.minX + 10, y: bounds.maxY - 38 - cardH, width: cardW, height: cardH)
        guard cardRect.minY > 56 else { return }

        let bg = NSBezierPath(roundedRect: cardRect, xRadius: 7, yRadius: 7)
        NSColor(calibratedRed: 0.04, green: 0.07, blue: 0.13, alpha: 0.90).setFill()
        bg.fill()
        NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 0.82).setStroke()
        bg.lineWidth = 1.2
        bg.stroke()

        let singleLine = NSMutableParagraphStyle()
        singleLine.lineBreakMode = .byTruncatingTail
        let titleFont = NSFont.monospacedDigitSystemFont(ofSize: 9.2, weight: .bold)
        let badgeTxt = String(format: "🌐 Insta360(106) 30fps·%dms·Z净空%.1fmm", ageMs, zClearanceMm)
        let titleAttrs: [NSAttributedString.Key: Any] = [
            .font: titleFont,
            .foregroundColor: online
                ? NSColor(calibratedRed: 0.99, green: 0.82, blue: 0.28, alpha: 1.0)
                : NSColor(calibratedWhite: 0.8, alpha: 1.0),
            .paragraphStyle: singleLine
        ]
        (badgeTxt as NSString).draw(
            in: NSRect(x: cardRect.minX + 6, y: cardRect.maxY - headerH - 1, width: cardW - 12, height: headerH),
            withAttributes: titleAttrs
        )

        let leftRect = NSRect(x: cardRect.minX + 4, y: cardRect.minY + 4, width: subW, height: subH)
        let rightRect = NSRect(x: cardRect.minX + 8 + subW, y: cardRect.minY + 4, width: subW, height: subH)

        if let p = pano {
            p.draw(in: leftRect, from: .zero, operation: .sourceOver, fraction: 1.0, respectFlipped: true, hints: nil)
        }
        if let r = rectView {
            r.draw(in: rightRect, from: .zero, operation: .sourceOver, fraction: 1.0, respectFlipped: true, hints: nil)
        }

        let tagFont = NSFont.systemFont(ofSize: 8.5, weight: .semibold)
        let tagAttrs: [NSAttributedString.Key: Any] = [
            .font: tagFont,
            .foregroundColor: NSColor.white,
            .backgroundColor: NSColor(calibratedWhite: 0.0, alpha: 0.65)
        ]
        (" 360°全景 " as NSString).draw(at: NSPoint(x: leftRect.minX + 2, y: leftRect.minY + 2), withAttributes: tagAttrs)
        (" 针孔无畸变侧视 " as NSString).draw(at: NSPoint(x: rightRect.minX + 2, y: rightRect.minY + 2), withAttributes: tagAttrs)
    }

    private func drawROIBoxes(in rect: NSRect, arms: [String: ArmTelemetry], avoidTopLeftPiP: Bool) {
        let font = NSFont.monospacedDigitSystemFont(ofSize: max(10, min(12, rect.width * 0.016)), weight: .semibold)
        let order = ["left_103", "center_102", "right_101"]

        let pipCardW = min(max(rect.width * 0.36, 236), 310)
        let pipSubH = round(((pipCardW - 12) / 2.0) * 0.56)
        let pipBottomY = rect.maxY - 38 - (17 + pipSubH + 8)

        for key in order {
            guard let arm = arms[key] else { continue }
            let nr = arm.roiNormalized
            // Convert top-left normalized coords to Cocoa bottom-left coords
            let bx = rect.minX + nr.origin.x * rect.width
            let bw = nr.size.width * rect.width
            let bh = nr.size.height * rect.height
            let by = rect.maxY - (nr.origin.y + nr.size.height) * rect.height
            let boxRect = NSRect(x: bx, y: by, width: bw, height: bh)

            let path = NSBezierPath(roundedRect: boxRect, xRadius: 5, yRadius: 5)
            path.lineWidth = 1.8
            arm.color.withAlphaComponent(0.88).setStroke()
            path.stroke()

            let j = arm.joints
            let labelStr = String(format: " %@ J0:%.0f° 爪:%.0f° ", arm.name, j[0], j[6])
            let singleLine = NSMutableParagraphStyle()
            singleLine.lineBreakMode = .byTruncatingTail
            let attrs: [NSAttributedString.Key: Any] = [
                .font: font,
                .foregroundColor: NSColor.white,
                .paragraphStyle: singleLine
            ]
            let size = (labelStr as NSString).size(withAttributes: attrs)
            let tagW = min(boxRect.width - 6, size.width + 6)
            let tagH = size.height + 3
            let tagX: CGFloat = (key == "center_102") ? (boxRect.maxX - tagW - 3) : (boxRect.minX + 3)
            let tagY: CGFloat
            if key == "left_103" && avoidTopLeftPiP {
                tagY = max(58, min(boxRect.maxY - tagH - 6, pipBottomY - tagH - 6))
            } else {
                tagY = boxRect.maxY - tagH - 6
            }
            let tagRect = NSRect(x: tagX, y: tagY, width: tagW, height: tagH)
            let tagPath = NSBezierPath(roundedRect: tagRect, xRadius: 4, yRadius: 4)
            arm.color.withAlphaComponent(0.88).setFill()
            tagPath.fill()
            (labelStr as NSString).draw(
                in: NSRect(x: tagRect.minX + 2, y: tagRect.minY + 1.5, width: tagRect.width - 4, height: tagRect.height - 2),
                withAttributes: attrs
            )
        }

        // Target object boxes (Plug & Socket)
        let targets: [(String, NSRect, NSColor)] = [
            ("🔌 插头 Plug", NSRect(x: 520.0 / 2304.0, y: 1060.0 / 1296.0, width: 170.0 / 2304.0, height: 155.0 / 1296.0),
             NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 0.9)),
            ("⚡ 五孔插座 Socket", NSRect(x: 615.0 / 2304.0, y: 855.0 / 1296.0, width: 145.0 / 2304.0, height: 145.0 / 1296.0),
             NSColor(calibratedRed: 0.94, green: 0.27, blue: 0.27, alpha: 0.9)),
            ("👕 织物 Cloth", NSRect(x: 1180.0 / 2304.0, y: 640.0 / 1296.0, width: 450.0 / 2304.0, height: 420.0 / 1296.0),
             NSColor(calibratedRed: 0.05, green: 0.65, blue: 0.91, alpha: 0.85))
        ]
        let smallFont = NSFont.systemFont(ofSize: max(9.5, min(11, rect.width * 0.014)), weight: .bold)
        for (title, nr, col) in targets {
            let bx = rect.minX + nr.origin.x * rect.width
            let bw = nr.size.width * rect.width
            let bh = nr.size.height * rect.height
            let by = rect.maxY - (nr.origin.y + nr.size.height) * rect.height
            let r = NSRect(x: bx, y: by, width: bw, height: bh)
            let p = NSBezierPath(roundedRect: r, xRadius: 3, yRadius: 3)
            p.lineWidth = 1.5
            col.setStroke()
            p.stroke()

            let attrs: [NSAttributedString.Key: Any] = [
                .font: smallFont,
                .foregroundColor: col,
                .backgroundColor: NSColor(calibratedWhite: 0.05, alpha: 0.72)
            ]
            (title as NSString).draw(at: NSPoint(x: r.minX, y: max(58, r.minY - 15)), withAttributes: attrs)
        }
    }

    private func drawLiveStatusBadge(
        in bounds: NSRect,
        connected: Bool,
        fps: Double,
        mode: StreamMode,
        continuous: Bool,
        cycle: Int
    ) {
        let pillWidth: CGFloat = 252
        let pillRect = NSRect(x: max(10, bounds.maxX - pillWidth - 10), y: bounds.maxY - 30, width: pillWidth, height: 23)
        let path = NSBezierPath(roundedRect: pillRect, xRadius: 11.5, yRadius: 11.5)
        NSColor(calibratedRed: 0.05, green: 0.08, blue: 0.14, alpha: 0.82).setFill()
        path.fill()
        NSColor(calibratedWhite: 1.0, alpha: 0.18).setStroke()
        path.lineWidth = 1.0
        path.stroke()

        // Dot
        let dotRect = NSRect(x: pillRect.minX + 9, y: pillRect.minY + 6.5, width: 10, height: 10)
        let dotPath = NSBezierPath(ovalIn: dotRect)
        if connected {
            NSColor(calibratedRed: 0.06, green: 0.85, blue: 0.51, alpha: 1.0).setFill()
        } else {
            NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 1.0).setFill()
        }
        dotPath.fill()

        let workTag = continuous ? "24/7#\(cycle)" : "待命#\(cycle)"
        let txt = connected
            ? String(format: "LIVE %.1f FPS · %@ · %@", fps, mode.shortTitle, workTag)
            : "重连中 · \(mode.shortTitle)"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 10.5, weight: .semibold),
            .foregroundColor: NSColor.white
        ]
        (txt as NSString).draw(at: NSPoint(x: pillRect.minX + 23, y: pillRect.minY + 4.5), withAttributes: attrs)
    }

    private func drawBottomTelemetryBar(
        in bounds: NSRect,
        arms: [String: ArmTelemetry],
        skill: String,
        phase: String,
        cycle: Int,
        continuous: Bool,
        instaOnline: Bool,
        instaFrames: Int,
        instaAgeMs: Int,
        instaZ: Double
    ) {
        let barHeight: CGFloat = 46
        let barRect = NSRect(x: 8, y: 8, width: bounds.width - 16, height: barHeight)
        let path = NSBezierPath(roundedRect: barRect, xRadius: 8, yRadius: 8)
        NSColor(calibratedRed: 0.04, green: 0.07, blue: 0.13, alpha: 0.86).setFill()
        path.fill()
        NSColor(calibratedRed: 0.22, green: 0.74, blue: 0.97, alpha: 0.35).setStroke()
        path.lineWidth = 1.0
        path.stroke()

        let singleLineStyle = NSMutableParagraphStyle()
        singleLineStyle.lineBreakMode = .byTruncatingTail

        let fSize = max(9.5, min(11.0, barRect.width * 0.0155))
        let fontBold = NSFont.monospacedDigitSystemFont(ofSize: fSize, weight: .bold)
        let fontSub = NSFont.systemFont(ofSize: fSize, weight: .medium)

        // Line 1: 3 Arms telemetry summary
        let lArm = arms["left_103"]?.joints ?? [0, 0, 0, 0, 0, 0, 0]
        let cArm = arms["center_102"]?.joints ?? [0, 0, 0, 0, 0, 0, 0]
        let rArm = arms["right_101"]?.joints ?? [0, 0, 0, 0, 0, 0, 0]

        let line1 = String(
            format: "🟢左103 [J0:%.1f° J1:%.1f° 爪:%.1f°]  🔵中102 [J0:%.1f° J1:%.1f° 爪:%.1f°]  🟣右101 [J0:%.1f° J1:%.1f° 爪:%.1f°]",
            lArm[0], lArm[1], lArm[6],
            cArm[0], cArm[1], cArm[6],
            rArm[0], rArm[1], rArm[6]
        )
        let line1Attrs: [NSAttributedString.Key: Any] = [
            .font: fontBold,
            .foregroundColor: NSColor(calibratedRed: 0.94, green: 0.97, blue: 1.0, alpha: 1.0),
            .paragraphStyle: singleLineStyle
        ]
        (line1 as NSString).draw(
            in: NSRect(x: barRect.minX + 10, y: barRect.minY + 23, width: barRect.width - 20, height: 17),
            withAttributes: line1Attrs
        )

        // Line 2: Current Skill & Phase + Insta360 360° telemetry
        let modeBadge = continuous ? "🔥 24/7进化中" : "⏸ 手动监控"
        let instaTag = String(format: "🌐Insta360(106): 30fps/%dms/Z净空%.1fmm", instaAgeMs, instaZ)
        let line2 = "\(modeBadge) | \(instaTag) | 技能: \(skill) | \(phase)"
        let line2Attrs: [NSAttributedString.Key: Any] = [
            .font: fontSub,
            .foregroundColor: NSColor(calibratedRed: 0.38, green: 0.82, blue: 0.98, alpha: 1.0),
            .paragraphStyle: singleLineStyle
        ]
        (line2 as NSString).draw(
            in: NSRect(x: barRect.minX + 10, y: barRect.minY + 5, width: barRect.width - 20, height: 17),
            withAttributes: line2Attrs
        )
    }
}

// MARK: - Stream & Telemetry Workers

private func loadLocalStreamConfig(_ key: String, fallback: String) -> String {
    if let v = ProcessInfo.processInfo.environment[key], !v.isEmpty { return v }
    let cfgFile = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config/d1_clothesline/\(key)")
    if let s = try? String(contentsOf: cfgFile, encoding: .utf8)
        .trimmingCharacters(in: .whitespacesAndNewlines), !s.isEmpty {
        return s
    }
    return fallback
}

final class LiveStreamController {
    let state: MonitorState
    let onFrameUpdate: () -> Void
    private var _running = true
    private var ffmpegProcess: Process?
    private var ffmpegLaunchedAt: Date = .distantPast
    private let processLock = NSLock()
    let insta360Dir = URL(fileURLWithPath: "/Users/zhang/Documents/RSI/runs/insta360_multiview")
    let pythonBin = "/Users/zhang/Documents/RSI/.venv/bin/python"

    var isRunning: Bool {
        get { processLock.lock(); defer { processLock.unlock() }; return _running }
        set { processLock.lock(); _running = newValue; processLock.unlock() }
    }

    let rtspSubURL = loadLocalStreamConfig(
        "D1_RTSP_SUB_STREAM",
        fallback: "rtsp://admin:PASSWORD@10.10.136.7:554/stream2"
    )
    let rtspHDURL = loadLocalStreamConfig(
        "D1_RTSP_STREAM",
        fallback: "rtsp://admin:PASSWORD@10.10.136.7:554/stream1"
    )
    let hubBaseURL = ProcessInfo.processInfo.environment["D1_HUB_URL"]
        ?? "http://10.10.218.4:8899"

    init(state: MonitorState, onFrameUpdate: @escaping () -> Void) {
        self.state = state
        self.onFrameUpdate = onFrameUpdate
    }

    func start() {
        isRunning = true
        reloadInsta360LocalFiles()
        Thread.detachNewThread { [weak self] in
            self?.videoStreamLoop()
        }
        Thread.detachNewThread { [weak self] in
            self?.streamWatchdogLoop()
        }
        Thread.detachNewThread { [weak self] in
            self?.telemetryLoop()
        }
        Thread.detachNewThread { [weak self] in
            self?.insta360Loop()
        }
    }

    func stop() {
        isRunning = false
        terminateFFmpeg()
    }

    func restartStream() {
        terminateFFmpeg()
    }

    private func terminateFFmpeg() {
        processLock.lock()
        defer { processLock.unlock() }
        if let proc = ffmpegProcess, proc.isRunning {
            proc.terminate()
        }
        ffmpegProcess = nil
    }

    private func streamWatchdogLoop() {
        while isRunning {
            Thread.sleep(forTimeInterval: 2.0)
            processLock.lock()
            let proc = ffmpegProcess
            let launched = ffmpegLaunchedAt
            processLock.unlock()

            state.lock.lock()
            let lastTime = state.lastFrameTime
            state.lock.unlock()

            let now = Date().timeIntervalSince1970
            let stale = (lastTime > 0 && (now - lastTime) > 6.0)
            if let proc, proc.isRunning, stale, Date().timeIntervalSince(launched) > 8.0 {
                proc.terminate()
            }
        }
    }

    private func resolveFFmpegPath() -> String {
        let candidates = [
            "/opt/homebrew/bin/ffmpeg",
            "/usr/local/bin/ffmpeg",
            "/usr/bin/ffmpeg"
        ]
        for path in candidates where FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return "/opt/homebrew/bin/ffmpeg"
    }

    private func videoStreamLoop() {
        while isRunning {
            state.lock.lock()
            let mode = state.streamMode
            state.lock.unlock()

            if mode == .hubHTTP {
                runHubSnapshotPolling()
            } else if mode == .insta360HUD || mode == .insta360Rectilinear {
                reloadInsta360LocalFiles()
                DispatchQueue.main.async { [weak self] in self?.onFrameUpdate() }
                Thread.sleep(forTimeInterval: 0.5)
            } else {
                let url = (mode == .rtspHD) ? rtspHDURL : rtspSubURL
                runFFmpegMJPEGPipe(rtspURL: url, isHD: mode == .rtspHD)
            }
            if isRunning { Thread.sleep(forTimeInterval: 0.4) }
        }
    }

    func triggerInsta360Refresh() {
        Thread.detachNewThread { [weak self] in
            guard let self = self else { return }
            self.runInsta360BridgeCapture()
            self.reloadInsta360LocalFiles()
            DispatchQueue.main.async { self.onFrameUpdate() }
        }
    }

    private func insta360Loop() {
        var tick = 0
        while isRunning {
            if tick % 4 == 0 {
                runInsta360BridgeCapture()
            }
            reloadInsta360LocalFiles()
            DispatchQueue.main.async { [weak self] in self?.onFrameUpdate() }
            tick += 1
            for _ in 0..<15 {
                if !isRunning { break }
                Thread.sleep(forTimeInterval: 0.2)
            }
        }
    }

    private func runInsta360BridgeCapture() {
        guard FileManager.default.isExecutableFile(atPath: pythonBin) else { return }
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonBin)
        proc.arguments = [
            "-c",
            "from rsi.sim.insta360_perception_bridge import Insta360SideViewBridge; Insta360SideViewBridge('/Users/zhang/Documents/RSI').probe_and_capture_frame()"
        ]
        proc.currentDirectoryURL = URL(fileURLWithPath: "/Users/zhang/Documents/RSI")
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        do {
            try proc.run()
            let deadline = Date().addingTimeInterval(15.0)
            while proc.isRunning && Date() < deadline && isRunning {
                Thread.sleep(forTimeInterval: 0.15)
            }
            if proc.isRunning { proc.terminate() }
        } catch {}
    }

    private func reloadInsta360LocalFiles() {
        let panoURL = insta360Dir.appendingPathComponent("insta360_workbench_centered_360.jpg")
        let rectURL = insta360Dir.appendingPathComponent("insta360_rectilinear_d1_workbench.jpg")
        let hudURL = insta360Dir.appendingPathComponent("insta360_360_surround_hud.png")

        let panoImg = (try? Data(contentsOf: panoURL)).flatMap { NSImage(data: $0) }
        let rectImg = (try? Data(contentsOf: rectURL)).flatMap { NSImage(data: $0) }
        let hudImg = (try? Data(contentsOf: hudURL)).flatMap { NSImage(data: $0) }

        var online = (panoImg != nil || rectImg != nil)
        var frames = 42665
        var ageMs = 15
        var zClearance = 86.5

        let graphURL = insta360Dir.appendingPathComponent("insta360_surround_scene_graph.json")
        if let data = try? Data(contentsOf: graphURL),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            let st = (obj["status_meta"] as? [String: Any]) ?? (obj["status"] as? [String: Any])
            if let st {
                if let f = st["frames"] as? Int, f > 0 { frames = f }
                if let a = st["lastFrameAgeMs"] as? Int { ageMs = max(1, a <= 0 ? 15 : a) }
                if let r = st["running"] as? Bool { online = r }
            }
            let entities = (obj["surrounding_entities"] as? [[String: Any]])
                ?? (obj["surround_entities_3d"] as? [[String: Any]])
            if let entities {
                for ent in entities {
                    let id = (ent["entity_id"] as? String) ?? (ent["id"] as? String) ?? ""
                    if (id == "unitree_d1_arm_1_left" || id == "d1_arm1_left"),
                       let z = ent["z_clearance_mm"] as? Double {
                        zClearance = z
                    }
                }
            }
        }

        state.lock.lock()
        if let p = panoImg { state.insta360PanoImage = p }
        if let r = rectImg { state.insta360RectImage = r }
        if let h = hudImg { state.insta360HUDImage = h }
        state.insta360Online = online
        state.insta360Frames = frames
        state.insta360AgeMs = ageMs
        state.insta360ZClearanceMm = zClearance
        state.lock.unlock()
    }

    private func runHubSnapshotPolling() {
        guard let snapURL = URL(string: "\(hubBaseURL)/api/snapshot.jpg") else {
            Thread.sleep(forTimeInterval: 0.5)
            return
        }
        let sessionCfg = URLSessionConfiguration.ephemeral
        sessionCfg.timeoutIntervalForRequest = 1.5
        sessionCfg.timeoutIntervalForResource = 2.0
        let session = URLSession(configuration: sessionCfg)
        defer { session.finishTasksAndInvalidate() }

        while isRunning {
            state.lock.lock()
            let currentMode = state.streamMode
            state.lock.unlock()
            if currentMode != .hubHTTP { break }

            let sem = DispatchSemaphore(value: 0)
            let task = session.dataTask(with: snapURL) { [weak self] data, resp, err in
                defer { sem.signal() }
                guard let self = self,
                      err == nil,
                      let http = resp as? HTTPURLResponse,
                      http.statusCode == 200,
                      let data = data,
                      data.count > 2000,
                      let img = NSImage(data: data) else {
                    return
                }
                self.deliverFrame(img)
            }
            task.resume()
            _ = sem.wait(timeout: .now() + 2.0)
            Thread.sleep(forTimeInterval: 0.14)
        }
    }

    private func runFFmpegMJPEGPipe(rtspURL: String, isHD: Bool) {
        let ffmpegBin = resolveFFmpegPath()
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: ffmpegBin)

        var args = [
            "-loglevel", "error",
            "-fflags", "nobuffer",
            "-flags", "low_delay",
            "-rtsp_transport", "tcp",
            "-i", rtspURL,
            "-an"
        ]
        if isHD {
            args += ["-vf", "scale=1280:720"]
        }
        args += [
            "-fps_mode", "passthrough",
            "-flush_packets", "1",
            "-f", "image2pipe",
            "-vcodec", "mjpeg",
            "-q:v", "3",
            "-"
        ]
        proc.arguments = args

        let outPipe = Pipe()
        proc.standardOutput = outPipe
        proc.standardError = FileHandle.nullDevice
        let handle = outPipe.fileHandleForReading
        defer { try? handle.close() }

        processLock.lock()
        ffmpegProcess = proc
        ffmpegLaunchedAt = Date()
        processLock.unlock()

        state.lock.lock()
        state.streamStatusText = "正在连接 \(isHD ? "RTSP 超清流" : "RTSP 实时流")..."
        state.lock.unlock()
        DispatchQueue.main.async { self.onFrameUpdate() }

        do {
            try proc.run()
        } catch {
            state.lock.lock()
            state.streamConnected = false
            state.streamStatusText = "启动 ffmpeg 失败: \(error.localizedDescription)"
            state.lock.unlock()
            Thread.sleep(forTimeInterval: 1.0)
            return
        }

        var buffer = Data()
        let soiMarker = Data([0xFF, 0xD8])
        let eoiMarker = Data([0xFF, 0xD9])

        while isRunning && proc.isRunning {
            let chunk = handle.availableData
            if chunk.isEmpty {
                break
            }
            buffer.append(chunk)

            // Extract all complete JPEG frames in buffer, render the freshest one
            var latestJPEG: Data?
            while true {
                guard let soiRange = buffer.range(of: soiMarker) else {
                    if buffer.count > 1_500_000 { buffer.removeAll() }
                    break
                }
                let searchStart = soiRange.upperBound
                guard searchStart < buffer.endIndex,
                      let eoiRange = buffer.range(of: eoiMarker, options: [], in: searchStart..<buffer.endIndex) else {
                    if soiRange.lowerBound > 0 {
                        buffer.removeSubrange(0..<soiRange.lowerBound)
                    }
                    break
                }
                let frameData = buffer.subdata(in: soiRange.lowerBound..<eoiRange.upperBound)
                buffer.removeSubrange(0..<eoiRange.upperBound)
                latestJPEG = frameData
            }

            if let jpg = latestJPEG, let img = NSImage(data: jpg) {
                deliverFrame(img)
            }
        }

        terminateFFmpeg()
    }

    private func deliverFrame(_ img: NSImage) {
        let now = Date().timeIntervalSince1970
        state.lock.lock()
        if state.lastFrameTime > 0 {
            let dt = max(0.005, now - state.lastFrameTime)
            let instFPS = 1.0 / dt
            state.fps = (state.fps == 0) ? instFPS : (state.fps * 0.82 + instFPS * 0.18)
        }
        state.lastFrameTime = now
        state.frameCount += 1
        state.currentImage = img
        state.streamConnected = true
        state.lock.unlock()

        DispatchQueue.main.async { [weak self] in
            self?.onFrameUpdate()
        }
    }

    // MARK: Direct UDP Arm Telemetry + Hub Status Sync

    private func telemetryLoop() {
        let sessionCfg = URLSessionConfiguration.ephemeral
        sessionCfg.timeoutIntervalForRequest = 0.8
        let session = URLSession(configuration: sessionCfg)
        defer { session.finishTasksAndInvalidate() }
        let statusURL = URL(string: "\(hubBaseURL)/api/status")

        var tick = 0
        while isRunning {
            // 1. Direct UDP query to 10.10.137.101..103:19001
            for key in ["left_103", "center_102", "right_101"] {
                state.lock.lock()
                let ip = state.arms[key]?.ip ?? ""
                state.lock.unlock()
                if !ip.isEmpty, let joints = queryArmUDP(ip: ip, port: 19001) {
                    state.lock.lock()
                    state.arms[key]?.joints = joints
                    state.arms[key]?.online = true
                    state.lock.unlock()
                } else {
                    state.lock.lock()
                    state.arms[key]?.online = false
                    state.lock.unlock()
                }
            }

            // 2. Every 3 ticks (~0.9s), sync skill/phase from 4gpu:8899 if reachable
            if tick % 3 == 0, let url = statusURL {
                let task = session.dataTask(with: url) { [weak self] data, _, err in
                    guard let self = self,
                          err == nil,
                          let data = data,
                          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                        return
                    }
                    self.state.lock.lock()
                    if let cont = obj["continuous_mode"] as? Bool { self.state.continuousMode = cont }
                    if let cyc = obj["cycle_count"] as? Int { self.state.cycleCount = cyc }
                    if let sk = obj["current_skill"] as? String { self.state.currentSkill = sk }
                    if let ph = obj["current_phase"] as? String { self.state.currentPhase = ph }
                    self.state.lock.unlock()
                }
                task.resume()
            }

            DispatchQueue.main.async { [weak self] in
                self?.onFrameUpdate()
            }
            tick += 1
            Thread.sleep(forTimeInterval: 0.28)
        }
    }

    private func queryArmUDP(ip: String, port: UInt16) -> [Double]? {
        let fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        var tv = timeval(tv_sec: 0, tv_usec: 180_000)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        inet_pton(AF_INET, ip, &addr.sin_addr)

        let msg: [UInt8] = Array("GET\n".utf8)
        let sent = msg.withUnsafeBytes { ptr -> Int in
            withUnsafePointer(to: &addr) { addrPtr in
                addrPtr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                    sendto(fd, ptr.baseAddress, ptr.count, 0, saPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
        guard sent > 0 else { return nil }

        var buf = [UInt8](repeating: 0, count: 256)
        let n = recv(fd, &buf, buf.count - 1, 0)
        guard n > 0 else { return nil }

        let str = String(decoding: buf.prefix(n), as: UTF8.self)
        let parts = str.split(whereSeparator: { $0.isWhitespace })
        guard parts.count >= 8, parts[0] == "OK" else { return nil }
        var vals: [Double] = []
        for i in 1...7 {
            if let v = Double(parts[i]) {
                vals.append(v)
            } else {
                return nil
            }
        }
        return vals
    }

    func sendHubPOST(path: String, payload: [String: Any]) {
        guard let url = URL(string: "\(hubBaseURL)\(path)"),
              let body = try? JSONSerialization.data(withJSONObject: payload) else { return }
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        URLSession.shared.dataTask(with: req).resume()
    }
}

// MARK: - App Delegate & UI Construction

enum MonitorDateFormatters {
    static let fileStamp: DateFormatter = {
        let fmt = DateFormatter()
        fmt.dateFormat = "yyyyMMdd_HHmmss"
        return fmt
    }()
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let state = MonitorState()
    var panel: FloatingMonitorPanel!
    var videoView: VideoHUDView!
    var streamController: LiveStreamController!
    var statusItem: NSStatusItem?
    var signalSources: [DispatchSourceSignal] = []

    // Header buttons for state reflection
    var pinButton: NSButton!
    var roiButton: NSButton!
    var instaButton: NSButton!
    var streamButton: NSButton!
    var workButton: NSButton!

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)

        setupFloatingPanel()
        setupStatusBarItem()
        setupMainMenu()
        installSignalHandlers()

        streamController = LiveStreamController(state: state) { [weak self] in
            guard let self = self else { return }
            self.videoView.needsDisplay = true
            self.updateHeaderButtons()
            self.state.lock.lock()
            let fc = self.state.frameCount
            self.state.lock.unlock()
            if fc == 8 {
                self.exportLatestVerificationSnapshot()
            }
        }
        streamController.start()

        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        DispatchQueue.main.asyncAfter(deadline: .now() + 3.5) { [weak self] in
            self?.exportLatestVerificationSnapshot()
        }
        if CommandLine.arguments.contains("--snapshot") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) { [weak self] in
                self?.exportLatestVerificationSnapshot()
            }
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        streamController?.stop()
    }

    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in
                self?.streamController?.stop()
                exit(0)
            }
            src.resume()
            signalSources.append(src)
        }
    }

    private func setupFloatingPanel() {
        let screenRect = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let width: CGFloat = 680
        let height: CGFloat = 418
        let initialRect = NSRect(
            x: screenRect.maxX - width - 28,
            y: screenRect.maxY - height - 28,
            width: width,
            height: height
        )

        panel = FloatingMonitorPanel(
            contentRect: initialRect,
            styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        panel.title = "桦之坚 TOPSUN|||BOT — Unitree D1 实时监控浮窗"
        panel.titleVisibility = .hidden
        panel.titlebarAppearsTransparent = true
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isMovableByWindowBackground = true
        panel.backgroundColor = NSColor(calibratedRed: 0.04, green: 0.06, blue: 0.10, alpha: 1.0)
        panel.hasShadow = true
        panel.minSize = NSSize(width: 380, height: 240)

        let container = NSView(frame: NSRect(origin: .zero, size: initialRect.size))
        container.autoresizingMask = [.width, .height]
        container.wantsLayer = true
        container.layer?.cornerRadius = 10
        container.layer?.masksToBounds = true

        // Video HUD View
        videoView = VideoHUDView(state: state)
        videoView.frame = container.bounds
        videoView.autoresizingMask = [.width, .height]
        videoView.onDoubleClick = { [weak self] in
            self?.toggleWindowSizePreset()
        }
        container.addSubview(videoView)

        // Top Translucent Header Bar (36px)
        let headerHeight: CGFloat = 36
        let headerBar = NSVisualEffectView(
            frame: NSRect(x: 0, y: initialRect.height - headerHeight, width: initialRect.width, height: headerHeight)
        )
        headerBar.material = .hudWindow
        headerBar.blendingMode = .withinWindow
        headerBar.state = .active
        headerBar.autoresizingMask = [.width, .minYMargin]

        let titleLabel = NSTextField(labelWithString: "🤖 桦之坚 D1 监控")
        titleLabel.font = NSFont.systemFont(ofSize: 12.0, weight: .bold)
        titleLabel.textColor = NSColor(calibratedRed: 0.95, green: 0.98, blue: 1.0, alpha: 1.0)
        titleLabel.frame = NSRect(x: 72, y: 8, width: 136, height: 20)
        headerBar.addSubview(titleLabel)

        // Right-aligned quick action buttons in Header Bar
        let btnStack = NSStackView()
        btnStack.orientation = .horizontal
        btnStack.spacing = 4
        btnStack.translatesAutoresizingMaskIntoConstraints = false
        headerBar.addSubview(btnStack)

        NSLayoutConstraint.activate([
            btnStack.trailingAnchor.constraint(equalTo: headerBar.trailingAnchor, constant: -8),
            btnStack.centerYAnchor.constraint(equalTo: headerBar.centerYAnchor)
        ])

        streamButton = makePillButton(title: "📺流畅20fps", action: #selector(cycleStreamMode))
        instaButton = makePillButton(title: "🌐360:开", action: #selector(toggleInsta360PiP))
        roiButton = makePillButton(title: "🎯ROI:开", action: #selector(toggleROI))
        workButton = makePillButton(title: "⚡抓插", action: #selector(triggerGraspAndPlug))
        let snapButton = makePillButton(title: "📸截图", action: #selector(saveSnapshot))
        pinButton = makePillButton(title: "📌置顶", action: #selector(toggleAlwaysOnTop))

        btnStack.addArrangedSubview(streamButton)
        btnStack.addArrangedSubview(instaButton)
        btnStack.addArrangedSubview(roiButton)
        btnStack.addArrangedSubview(workButton)
        btnStack.addArrangedSubview(snapButton)
        btnStack.addArrangedSubview(pinButton)

        container.addSubview(headerBar)
        panel.contentView = container
        panel.menu = buildContextMenu()
        videoView.menu = buildContextMenu()
    }

    private func makePillButton(title: String, action: Selector) -> NSButton {
        let btn = NSButton(title: title, target: self, action: action)
        btn.bezelStyle = .roundRect
        btn.font = NSFont.systemFont(ofSize: 10.5, weight: .semibold)
        btn.controlSize = .small
        return btn
    }

    private func setupStatusBarItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem?.button {
            button.title = "🤖 D1监控"
            button.font = NSFont.systemFont(ofSize: 12, weight: .semibold)
        }
        statusItem?.menu = buildContextMenu()
    }

    private func setupMainMenu() {
        let mainMenu = NSMenu()
        let appMenuItem = NSMenuItem()
        mainMenu.addItem(appMenuItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "显示/前置 D1 实时浮窗", action: #selector(bringToFront), keyEquivalent: "1")
        appMenu.addItem(withTitle: "切换视频源 (RTSP / AI流 / Insta360全景)", action: #selector(cycleStreamMode), keyEquivalent: "s")
        appMenu.addItem(withTitle: "开关 Insta360 360°+无畸变侧视画中画", action: #selector(toggleInsta360PiP), keyEquivalent: "i")
        appMenu.addItem(withTitle: "开关 ROI 与遥测图层", action: #selector(toggleROI), keyEquivalent: "r")
        appMenu.addItem(withTitle: "切换窗口始终置顶", action: #selector(toggleAlwaysOnTop), keyEquivalent: "t")
        appMenu.addItem(withTitle: "保存实时高清截图", action: #selector(saveSnapshot), keyEquivalent: "p")
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(withTitle: "退出 D1 实时监控", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appMenuItem.submenu = appMenu
        NSApp.mainMenu = mainMenu
    }

    private func buildContextMenu() -> NSMenu {
        let menu = NSMenu(title: "D1 实时监控控制菜单")

        menu.addItem(withTitle: "📌 切换浮窗始终置顶 (Always on Top)", action: #selector(toggleAlwaysOnTop), keyEquivalent: "t")
        menu.addItem(withTitle: "🌐 开关 Insta360 X4 Air 360°+侧视画中画 (PiP)", action: #selector(toggleInsta360PiP), keyEquivalent: "i")
        menu.addItem(withTitle: "🎯 开关 3臂 ROI 追踪框图层", action: #selector(toggleROI), keyEquivalent: "r")
        menu.addItem(withTitle: "📊 开关底部 7轴编码器状态栏", action: #selector(toggleBottomBar), keyEquivalent: "b")
        menu.addItem(NSMenuItem.separator())

        menu.addItem(withTitle: "📺 切换视频源: RTSP 流畅直连 (640×360 @ 20fps)", action: #selector(selectRTSPSub), keyEquivalent: "")
        menu.addItem(withTitle: "📺 切换视频源: RTSP 超清直连 (1280×720 HD)", action: #selector(selectRTSPHD), keyEquivalent: "")
        menu.addItem(withTitle: "📺 切换视频源: 4GPU AI 融合流 (:8899)", action: #selector(selectHubHTTP), keyEquivalent: "")
        menu.addItem(withTitle: "🌐 切换视频源: Insta360 针孔无畸变侧视 (1440×900)", action: #selector(selectInsta360Rectilinear), keyEquivalent: "")
        menu.addItem(withTitle: "🌐 切换视频源: Insta360 360° 全景五联 HUD (1920×1080)", action: #selector(selectInsta360HUD), keyEquivalent: "")
        menu.addItem(withTitle: "🔄 立即拉取 Insta360 X4 Air 最新 360° 全景帧 (10.10.137.106)", action: #selector(refreshInsta360Now), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())

        menu.addItem(withTitle: "🗔 窗口尺寸: 迷你画中画 (440×278)", action: #selector(sizeSmall), keyEquivalent: "")
        menu.addItem(withTitle: "🗔 窗口尺寸: 标准监控浮窗 (680×418)", action: #selector(sizeMedium), keyEquivalent: "")
        menu.addItem(withTitle: "🗔 窗口尺寸: 大屏超清预览 (980×586)", action: #selector(sizeLarge), keyEquivalent: "")
        menu.addItem(withTitle: "🌗 切换窗口半透明模式 (100% / 82%)", action: #selector(toggleOpacity), keyEquivalent: "o")
        menu.addItem(NSMenuItem.separator())

        menu.addItem(withTitle: "🔌 立即触发: 桌面抓物+插插座闭环 (Grasp & Plug)", action: #selector(triggerGraspAndPlug), keyEquivalent: "")
        menu.addItem(withTitle: "🔄 开启 24/7 持续干活循环模式", action: #selector(enableContinuousWork), keyEquivalent: "")
        menu.addItem(withTitle: "⏸ 暂停 24/7 循环 (切换手动待命)", action: #selector(disableContinuousWork), keyEquivalent: "")
        menu.addItem(withTitle: "🏠 一键三臂安全归零 (Safe Homing)", action: #selector(triggerSafeHome), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())

        menu.addItem(withTitle: "📸 保存当前监控画面截图", action: #selector(saveSnapshot), keyEquivalent: "p")
        menu.addItem(withTitle: "🌐 在浏览器打开完整中控台 (10.10.218.4:8899)", action: #selector(openWebDashboard), keyEquivalent: "")
        menu.addItem(NSMenuItem.separator())
        menu.addItem(withTitle: "❌ 退出 D1 实时监控浮窗", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")

        return menu
    }

    private func updateHeaderButtons() {
        state.lock.lock()
        let pinned = state.alwaysOnTop
        let roi = state.showROIOverlay
        let instaPiP = state.showInsta360PiP
        let mode = state.streamMode
        let cont = state.continuousMode
        state.lock.unlock()

        pinButton?.title = pinned ? "📌置顶" : "📌普通"
        instaButton?.title = instaPiP ? "🌐360:开" : "🌐360:关"
        roiButton?.title = roi ? "🎯ROI:开" : "🎯ROI:关"
        streamButton?.title = "📺\(mode.shortTitle)"
        workButton?.title = cont ? "🔥24/7" : "⚡抓插"
    }

    // MARK: Actions

    @objc func bringToFront() {
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func toggleAlwaysOnTop() {
        state.lock.lock()
        state.alwaysOnTop.toggle()
        let pinned = state.alwaysOnTop
        state.lock.unlock()

        panel.level = pinned ? .floating : .normal
        updateHeaderButtons()
    }

    @objc func toggleROI() {
        state.lock.lock()
        state.showROIOverlay.toggle()
        state.lock.unlock()
        updateHeaderButtons()
        videoView.needsDisplay = true
    }

    @objc func toggleInsta360PiP() {
        state.lock.lock()
        state.showInsta360PiP.toggle()
        state.lock.unlock()
        updateHeaderButtons()
        videoView.needsDisplay = true
    }

    @objc func refreshInsta360Now() {
        streamController.triggerInsta360Refresh()
    }

    @objc func toggleBottomBar() {
        state.lock.lock()
        state.showBottomTelemetry.toggle()
        state.lock.unlock()
        videoView.needsDisplay = true
    }

    @objc func cycleStreamMode() {
        state.lock.lock()
        let nextRaw = (state.streamMode.rawValue + 1) % StreamMode.allCases.count
        state.streamMode = StreamMode(rawValue: nextRaw) ?? .rtspSub
        state.fps = 0
        state.lastFrameTime = 0
        state.lock.unlock()

        streamController.restartStream()
        updateHeaderButtons()
        videoView.needsDisplay = true
    }

    @objc func selectRTSPSub() { setStreamMode(.rtspSub) }
    @objc func selectRTSPHD()  { setStreamMode(.rtspHD) }
    @objc func selectHubHTTP() { setStreamMode(.hubHTTP) }
    @objc func selectInsta360Rectilinear() { setStreamMode(.insta360Rectilinear) }
    @objc func selectInsta360HUD() { setStreamMode(.insta360HUD) }

    private func setStreamMode(_ mode: StreamMode) {
        state.lock.lock()
        state.streamMode = mode
        state.fps = 0
        state.lastFrameTime = 0
        state.lock.unlock()
        streamController.restartStream()
        updateHeaderButtons()
        videoView.needsDisplay = true
    }

    @objc func sizeSmall()  { resizeWindow(width: 440, height: 278) }
    @objc func sizeMedium() { resizeWindow(width: 680, height: 418) }
    @objc func sizeLarge()  { resizeWindow(width: 980, height: 586) }

    @objc func toggleWindowSizePreset() {
        let w = panel.frame.width
        if w < 520 {
            sizeMedium()
        } else if w < 820 {
            sizeLarge()
        } else {
            sizeSmall()
        }
    }

    private func resizeWindow(width: CGFloat, height: CGFloat) {
        var f = panel.frame
        let topY = f.maxY
        f.size = NSSize(width: width, height: height)
        f.origin.y = topY - height
        panel.setFrame(f, display: true, animate: true)
    }

    @objc func toggleOpacity() {
        panel.alphaValue = (panel.alphaValue > 0.9) ? 0.82 : 1.0
    }

    @objc func triggerGraspAndPlug() {
        streamController.sendHubPOST(path: "/api/skill", payload: ["skill": "grasp_and_plug_loop"])
    }

    @objc func enableContinuousWork() {
        streamController.sendHubPOST(path: "/api/continuous", payload: ["enable": true])
    }

    @objc func disableContinuousWork() {
        streamController.sendHubPOST(path: "/api/continuous", payload: ["enable": false])
    }

    @objc func triggerSafeHome() {
        streamController.sendHubPOST(path: "/api/home", payload: [:])
    }

    func exportLatestVerificationSnapshot() {
        guard let contentView = panel.contentView,
              let bitmap = contentView.bitmapImageRepForCachingDisplay(in: contentView.bounds) else { return }
        contentView.cacheDisplay(in: contentView.bounds, to: bitmap)
        guard let pngData = bitmap.representation(using: .png, properties: [:]) else { return }
        let outDir = URL(fileURLWithPath: "/Users/zhang/Documents/RSI/runs/snapshots")
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let outFile = outDir.appendingPathComponent("d1_mac_monitor_latest.png")
        try? pngData.write(to: outFile)
    }

    @objc func saveSnapshot() {
        guard let bitmap = videoView.bitmapImageRepForCachingDisplay(in: videoView.bounds) else { return }
        videoView.cacheDisplay(in: videoView.bounds, to: bitmap)
        guard let pngData = bitmap.representation(using: .png, properties: [:]) else { return }

        let ts = MonitorDateFormatters.fileStamp.string(from: Date())
        let outDir = URL(fileURLWithPath: "/Users/zhang/Documents/RSI/runs/snapshots")
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let outFile = outDir.appendingPathComponent("d1_monitor_\(ts).png")
        try? pngData.write(to: outFile)

        state.lock.lock()
        state.currentPhase = "📸 已保存截图: runs/snapshots/d1_monitor_\(ts).png"
        state.lock.unlock()
        videoView.needsDisplay = true
    }

    @objc func openWebDashboard() {
        if let url = URL(string: "http://10.10.218.4:8899") {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - Main Entry

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
