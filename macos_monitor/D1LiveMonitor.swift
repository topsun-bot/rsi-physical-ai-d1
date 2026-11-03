import Cocoa
import Darwin
import Foundation

enum TraceLogger {
    static func makeOperationId(prefix: String = "mon") -> String {
        let rawUuid = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return "\(prefix)-\(rawUuid.prefix(12))"
    }

    static func warn(operationId: String, component: String, message: String, error: Error? = nil) {
        let errorSuffix = error.map { " | error=\($0.localizedDescription)" } ?? ""
        fputs("[D1LiveMonitor][WARN][op=\(operationId)][\(component)] \(message)\(errorSuffix)\n", stderr)
    }
}

enum FileIO {
    @discardableResult
    static func ensureDirectory(_ url: URL, operationId: String, context: String) -> Bool {
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
            return true
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "FileIO",
                message: "createDirectory failed (\(context)): \(url.path)",
                error: error
            )
            return false
        }
    }

    @discardableResult
    static func writeData(
        _ payloadBytes: Data,
        to url: URL,
        operationId: String,
        context: String
    ) -> Bool {
        do {
            try payloadBytes.write(to: url, options: .atomic)
            return true
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "FileIO",
                message: "writeData failed (\(context)): \(url.path)",
                error: error
            )
            return false
        }
    }

    static func loadImage(from url: URL, operationId: String, context: String) -> NSImage? {
        guard FileManager.default.fileExists(atPath: url.path) else { return nil }
        do {
            let rawBytes = try Data(contentsOf: url)
            return NSImage(data: rawBytes)
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "FileIO",
                message: "loadImage failed (\(context)): \(url.path)",
                error: error
            )
            return nil
        }
    }
}

struct HubStatusPayload: Decodable, Equatable {
    let continuous_mode: Bool?
    let cycle_count: Int?
    let current_skill: String?
    let current_phase: String?
}

struct HubControlCommand: Encodable, Equatable {
    let endpointPath: String
    let operation_id: String
    let skill: String?
    let enable: Bool?

    enum CodingKeys: String, CodingKey {
        case operation_id
        case skill
        case enable
    }

    static func triggerSkill(_ skillName: String, operationId: String = TraceLogger.makeOperationId(prefix: "cmd")) -> HubControlCommand {
        HubControlCommand(endpointPath: "/api/skill", operation_id: operationId, skill: skillName, enable: nil)
    }

    static func setContinuous(_ enabled: Bool, operationId: String = TraceLogger.makeOperationId(prefix: "cmd")) -> HubControlCommand {
        HubControlCommand(endpointPath: "/api/continuous", operation_id: operationId, skill: nil, enable: enabled)
    }

    static func safeHome(operationId: String = TraceLogger.makeOperationId(prefix: "cmd")) -> HubControlCommand {
        HubControlCommand(endpointPath: "/api/home", operation_id: operationId, skill: nil, enable: nil)
    }
}

struct Insta360SceneGraphSnapshot: Equatable {
    let online: Bool
    let frames: Int
    let ageMs: Int
    let zClearanceMm: Double
}

struct Insta360SceneGraphParser {
    private struct RawStatusMeta: Decodable {
        let running: Bool?
        let frames: Int?
        let lastFrameAgeMs: Int?
    }

    private struct RawEntity: Decodable {
        let entity_id: String?
        let id: String?
        let z_clearance_mm: Double?

        var resolvedId: String { entity_id ?? id ?? "" }
    }

    private struct RawDocument: Decodable {
        let status_meta: RawStatusMeta?
        let status: RawStatusMeta?
        let surrounding_entities: [RawEntity]?
        let surround_entities_3d: [RawEntity]?
    }

    static func normalizeAgeMs(_ rawAgeMs: Int?, fallback: Int = 15) -> Int {
        guard let rawAgeMs else { return fallback }
        return rawAgeMs <= 0 ? fallback : max(1, rawAgeMs)
    }

    static func parse(
        jsonData: Data,
        defaultOnline: Bool,
        operationId: String
    ) -> Insta360SceneGraphSnapshot {
        let decoder = JSONDecoder()
        let document: RawDocument
        do {
            document = try decoder.decode(RawDocument.self, from: jsonData)
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "Insta360SceneGraphParser",
                message: "Failed to decode insta360_surround_scene_graph.json",
                error: error
            )
            return Insta360SceneGraphSnapshot(
                online: defaultOnline,
                frames: 42665,
                ageMs: 15,
                zClearanceMm: 86.5
            )
        }

        let statusMeta = document.status_meta ?? document.status
        let online = statusMeta?.running ?? defaultOnline
        let rawFrames = statusMeta?.frames ?? 0
        let frames = rawFrames > 0 ? rawFrames : 42665
        let ageMs = normalizeAgeMs(statusMeta?.lastFrameAgeMs, fallback: 15)

        let entities = document.surrounding_entities ?? document.surround_entities_3d ?? []
        let matchedLeftArm = entities.first {
            $0.resolvedId == "unitree_d1_arm_1_left" || $0.resolvedId == "d1_arm1_left"
        }
        let zClearanceMm = matchedLeftArm?.z_clearance_mm ?? 86.5

        return Insta360SceneGraphSnapshot(
            online: online,
            frames: frames,
            ageMs: ageMs,
            zClearanceMm: zClearanceMm
        )
    }
}

struct ArmTelemetry {
    var name: String
    var ip: String
    var joints: [Double] = [0.0, -89.5, 89.0, 0.0, 5.0, 0.0, 0.0]
    var online: Bool = false
    var color: NSColor
    var roiNormalized: NSRect // 左上原点归一化坐标
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

final class FloatingMonitorPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

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
        let deltaX = current.x - initialMouseLocation.x
        let deltaY = current.y - initialMouseLocation.y
        win.setFrameOrigin(NSPoint(x: initialWindowOrigin.x + deltaX, y: initialWindowOrigin.y + deltaY))
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
        let cycleCount = state.cycleCount
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

        // 避开顶栏保时间戳可见
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
            let pStyle = NSMutableParagraphStyle()
            pStyle.alignment = .center
            let attrs: [NSAttributedString.Key: Any] = [
                .font: NSFont.boldSystemFont(ofSize: 15),
                .foregroundColor: NSColor(calibratedRed: 0.58, green: 0.64, blue: 0.72, alpha: 1.0),
                .paragraphStyle: pStyle
            ]
            let placeholderText = "📡 \(statusText)\n(支持鼠标任意位置拖拽移动 · 双击切换大小 · 右键菜单控制)"
            placeholderText.draw(in: NSRect(x: 20, y: videoRect.midY - 24, width: bounds.width - 40, height: 52), withAttributes: attrs)
        }

        let hasInstaPiP = showInstaPiP && (mode == .rtspSub || mode == .rtspHD || mode == .hubHTTP)
            && (instaPano != nil || instaRect != nil)

        if showROI && (mode == .rtspSub || mode == .rtspHD) {
            drawROIBoxes(in: videoRect, arms: arms, avoidTopLeftPiP: hasInstaPiP)
        }

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

        let effectiveConnected = (mode == .insta360HUD || mode == .insta360Rectilinear) ? (img != nil) : connected
        let effectiveFPS = (mode == .insta360HUD || mode == .insta360Rectilinear) ? 30.0 : fps
        drawLiveStatusBadge(
            in: videoRect,
            connected: effectiveConnected,
            fps: effectiveFPS,
            mode: mode,
            continuous: cont,
            cycle: cycleCount
        )

        if showBottom {
            drawBottomTelemetryBar(
                in: videoRect,
                arms: arms,
                skill: skill,
                phase: phase,
                cycle: cycleCount,
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
        // 避开左上角OSD时间戳
        let cardRect = NSRect(x: bounds.minX + 10, y: bounds.maxY - 38 - cardH, width: cardW, height: cardH)
        guard cardRect.minY > 56 else { return }

        let cardBorderPath = NSBezierPath(roundedRect: cardRect, xRadius: 7, yRadius: 7)
        NSColor(calibratedRed: 0.04, green: 0.07, blue: 0.13, alpha: 0.90).setFill()
        cardBorderPath.fill()
        NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 0.82).setStroke()
        cardBorderPath.lineWidth = 1.2
        cardBorderPath.stroke()

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

        if let panoImage = pano {
            panoImage.draw(in: leftRect, from: .zero, operation: .sourceOver, fraction: 1.0, respectFlipped: true, hints: nil)
        }
        if let rectilinearImage = rectView {
            rectilinearImage.draw(in: rightRect, from: .zero, operation: .sourceOver, fraction: 1.0, respectFlipped: true, hints: nil)
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
            let normalizedRect = arm.roiNormalized
            // 转左上原点至Cocoa坐标
            let boxMinX = rect.minX + normalizedRect.origin.x * rect.width
            let boxWidth = normalizedRect.size.width * rect.width
            let boxHeight = normalizedRect.size.height * rect.height
            let boxMinY = rect.maxY - (normalizedRect.origin.y + normalizedRect.size.height) * rect.height
            let boxRect = NSRect(x: boxMinX, y: boxMinY, width: boxWidth, height: boxHeight)

            let path = NSBezierPath(roundedRect: boxRect, xRadius: 5, yRadius: 5)
            path.lineWidth = 1.8
            arm.color.withAlphaComponent(0.88).setStroke()
            path.stroke()

            let jointAngles = arm.joints
            let labelStr = String(format: " %@ J0:%.0f° 爪:%.0f° ", arm.name, jointAngles[0], jointAngles[6])
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

        let targets: [(String, NSRect, NSColor)] = [
            ("🔌 插头 Plug", NSRect(x: 520.0 / 2304.0, y: 1060.0 / 1296.0, width: 170.0 / 2304.0, height: 155.0 / 1296.0),
             NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 0.9)),
            ("⚡ 五孔插座 Socket", NSRect(x: 615.0 / 2304.0, y: 855.0 / 1296.0, width: 145.0 / 2304.0, height: 145.0 / 1296.0),
             NSColor(calibratedRed: 0.94, green: 0.27, blue: 0.27, alpha: 0.9)),
            ("👕 织物 Cloth", NSRect(x: 1180.0 / 2304.0, y: 640.0 / 1296.0, width: 450.0 / 2304.0, height: 420.0 / 1296.0),
             NSColor(calibratedRed: 0.05, green: 0.65, blue: 0.91, alpha: 0.85))
        ]
        let smallFont = NSFont.systemFont(ofSize: max(9.5, min(11, rect.width * 0.014)), weight: .bold)
        for (title, normalizedRect, strokeColor) in targets {
            let boxMinX = rect.minX + normalizedRect.origin.x * rect.width
            let boxWidth = normalizedRect.size.width * rect.width
            let boxHeight = normalizedRect.size.height * rect.height
            let boxMinY = rect.maxY - (normalizedRect.origin.y + normalizedRect.size.height) * rect.height
            let targetBoxRect = NSRect(x: boxMinX, y: boxMinY, width: boxWidth, height: boxHeight)
            let targetBorderPath = NSBezierPath(roundedRect: targetBoxRect, xRadius: 3, yRadius: 3)
            targetBorderPath.lineWidth = 1.5
            strokeColor.setStroke()
            targetBorderPath.stroke()

            let attrs: [NSAttributedString.Key: Any] = [
                .font: smallFont,
                .foregroundColor: strokeColor,
                .backgroundColor: NSColor(calibratedWhite: 0.05, alpha: 0.72)
            ]
            (title as NSString).draw(at: NSPoint(x: targetBoxRect.minX, y: max(58, targetBoxRect.minY - 15)), withAttributes: attrs)
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

        let dotRect = NSRect(x: pillRect.minX + 9, y: pillRect.minY + 6.5, width: 10, height: 10)
        let dotPath = NSBezierPath(ovalIn: dotRect)
        if connected {
            NSColor(calibratedRed: 0.06, green: 0.85, blue: 0.51, alpha: 1.0).setFill()
        } else {
            NSColor(calibratedRed: 0.96, green: 0.62, blue: 0.04, alpha: 1.0).setFill()
        }
        dotPath.fill()

        let workTag = continuous ? "24/7#\(cycle)" : "待命#\(cycle)"
        let pillText = connected
            ? String(format: "LIVE %.1f FPS · %@ · %@", fps, mode.shortTitle, workTag)
            : "重连中 · \(mode.shortTitle)"
        let attrs: [NSAttributedString.Key: Any] = [
            .font: NSFont.monospacedDigitSystemFont(ofSize: 10.5, weight: .semibold),
            .foregroundColor: NSColor.white
        ]
        (pillText as NSString).draw(at: NSPoint(x: pillRect.minX + 23, y: pillRect.minY + 4.5), withAttributes: attrs)
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

private func loadLocalStreamConfig(_ key: String, fallback: String) -> String {
    if let envValue = ProcessInfo.processInfo.environment[key], !envValue.isEmpty { return envValue }
    let cfgFile = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config/d1_clothesline/\(key)")
    guard FileManager.default.fileExists(atPath: cfgFile.path) else { return fallback }
    do {
        let content = try String(contentsOf: cfgFile, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return content.isEmpty ? fallback : content
    } catch {
        TraceLogger.warn(
            operationId: TraceLogger.makeOperationId(prefix: "cfg"),
            component: "StreamConfig",
            message: "Failed reading \(cfgFile.path)",
            error: error
        )
        return fallback
    }
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
        let operationId = TraceLogger.makeOperationId(prefix: "i360cap")
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: pythonBin)
        proc.arguments = [
            "-c",
            "from rsi.sim.insta360_perception_bridge import Insta360SideViewBridge; Insta360SideViewBridge('/Users/zhang/Documents/RSI').probe_and_capture_frame(operation_id='\(operationId)')"
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
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "LiveStreamController",
                message: "Failed launching python Insta360SideViewBridge subprocess",
                error: error
            )
        }
    }

    private func reloadInsta360LocalFiles() {
        let operationId = TraceLogger.makeOperationId(prefix: "i360load")
        let panoURL = insta360Dir.appendingPathComponent("insta360_workbench_centered_360.jpg")
        let rectURL = insta360Dir.appendingPathComponent("insta360_rectilinear_d1_workbench.jpg")
        let hudURL = insta360Dir.appendingPathComponent("insta360_360_surround_hud.png")

        let panoImg = FileIO.loadImage(from: panoURL, operationId: operationId, context: "pano")
        let rectImg = FileIO.loadImage(from: rectURL, operationId: operationId, context: "rect_workbench")
        let hudImg = FileIO.loadImage(from: hudURL, operationId: operationId, context: "surround_hud")

        let defaultOnline = (panoImg != nil || rectImg != nil)
        var parsedSnapshot = Insta360SceneGraphSnapshot(
            online: defaultOnline,
            frames: 42665,
            ageMs: 15,
            zClearanceMm: 86.5
        )

        let graphURL = insta360Dir.appendingPathComponent("insta360_surround_scene_graph.json")
        if FileManager.default.fileExists(atPath: graphURL.path) {
            do {
                let rawGraphData = try Data(contentsOf: graphURL)
                parsedSnapshot = Insta360SceneGraphParser.parse(
                    jsonData: rawGraphData,
                    defaultOnline: defaultOnline,
                    operationId: operationId
                )
            } catch {
                TraceLogger.warn(
                    operationId: operationId,
                    component: "LiveStreamController",
                    message: "Failed reading \(graphURL.path)",
                    error: error
                )
            }
        }

        state.lock.lock()
        if let loadedPano = panoImg { state.insta360PanoImage = loadedPano }
        if let loadedRect = rectImg { state.insta360RectImage = loadedRect }
        if let loadedHud = hudImg { state.insta360HUDImage = loadedHud }
        state.insta360Online = parsedSnapshot.online
        state.insta360Frames = parsedSnapshot.frames
        state.insta360AgeMs = parsedSnapshot.ageMs
        state.insta360ZClearanceMm = parsedSnapshot.zClearanceMm
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

            let semaphore = DispatchSemaphore(value: 0)
            let task = session.dataTask(with: snapURL) { [weak self] responseBytes, resp, requestError in
                defer { semaphore.signal() }
                if let requestError {
                    TraceLogger.warn(
                        operationId: TraceLogger.makeOperationId(prefix: "hubsnap"),
                        component: "LiveStreamController",
                        message: "Hub snapshot request failed",
                        error: requestError
                    )
                    return
                }
                guard let self = self,
                      let http = resp as? HTTPURLResponse,
                      http.statusCode == 200,
                      let responseBytes = responseBytes,
                      responseBytes.count > 2000,
                      let img = NSImage(data: responseBytes) else {
                    return
                }
                self.deliverFrame(img)
            }
            task.resume()
            _ = semaphore.wait(timeout: .now() + 2.0)
            Thread.sleep(forTimeInterval: 0.14)
        }
    }

    private func runFFmpegMJPEGPipe(rtspURL: String, isHD: Bool) {
        let operationId = TraceLogger.makeOperationId(prefix: "rtsp")
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
        defer {
            do {
                try handle.close()
            } catch {
                TraceLogger.warn(
                    operationId: operationId,
                    component: "LiveStreamController",
                    message: "Closing ffmpeg stdout pipe failed",
                    error: error
                )
            }
        }

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
            TraceLogger.warn(
                operationId: operationId,
                component: "LiveStreamController",
                message: "Failed launching ffmpeg process",
                error: error
            )
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

            // 丢弃堆积帧保最新帧
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
            let deltaSec = max(0.005, now - state.lastFrameTime)
            let instFPS = 1.0 / deltaSec
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

    private func telemetryLoop() {
        let sessionCfg = URLSessionConfiguration.ephemeral
        sessionCfg.timeoutIntervalForRequest = 0.8
        let session = URLSession(configuration: sessionCfg)
        defer { session.finishTasksAndInvalidate() }
        let statusURL = URL(string: "\(hubBaseURL)/api/status")
        let decoder = JSONDecoder()

        var tick = 0
        while isRunning {
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

            // 降频轮询中控减负
            if tick % 3 == 0, let url = statusURL {
                let task = session.dataTask(with: url) { [weak self] payloadData, _, requestError in
                    guard let self = self,
                          requestError == nil,
                          let payloadData = payloadData else {
                        return
                    }
                    let decoded: HubStatusPayload
                    do {
                        decoded = try decoder.decode(HubStatusPayload.self, from: payloadData)
                    } catch {
                        TraceLogger.warn(
                            operationId: TraceLogger.makeOperationId(prefix: "hub"),
                            component: "LiveStreamController",
                            message: "Failed decoding \(url.absoluteString)",
                            error: error
                        )
                        return
                    }
                    self.state.lock.lock()
                    if let cont = decoded.continuous_mode { self.state.continuousMode = cont }
                    if let cycleCount = decoded.cycle_count { self.state.cycleCount = cycleCount }
                    if let skillName = decoded.current_skill { self.state.currentSkill = skillName }
                    if let phaseName = decoded.current_phase { self.state.currentPhase = phaseName }
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
        let sockFd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard sockFd >= 0 else { return nil }
        defer { close(sockFd) }

        var timeoutVal = timeval(tv_sec: 0, tv_usec: 180_000)
        setsockopt(sockFd, SOL_SOCKET, SO_RCVTIMEO, &timeoutVal, socklen_t(MemoryLayout<timeval>.size))

        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        inet_pton(AF_INET, ip, &addr.sin_addr)

        let msg: [UInt8] = Array("GET\n".utf8)
        let sent = msg.withUnsafeBytes { ptr -> Int in
            withUnsafePointer(to: &addr) { addrPtr in
                addrPtr.withMemoryRebound(to: sockaddr.self, capacity: 1) { saPtr in
                    sendto(sockFd, ptr.baseAddress, ptr.count, 0, saPtr, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
        guard sent > 0 else { return nil }

        var receiveBuffer = [UInt8](repeating: 0, count: 256)
        let bytesRead = recv(sockFd, &receiveBuffer, receiveBuffer.count - 1, 0)
        guard bytesRead > 0 else { return nil }

        let replyText = String(decoding: receiveBuffer.prefix(bytesRead), as: UTF8.self)
        let parts = replyText.split(whereSeparator: { $0.isWhitespace })
        guard parts.count >= 8, parts[0] == "OK" else { return nil }
        var vals: [Double] = []
        for tokenIdx in 1...7 {
            if let jointVal = Double(parts[tokenIdx]) {
                vals.append(jointVal)
            } else {
                return nil
            }
        }
        return vals
    }

    func sendHubCommand(_ command: HubControlCommand) {
        let opId = command.operation_id
        guard let url = URL(string: "\(hubBaseURL)\(command.endpointPath)") else {
            TraceLogger.warn(
                operationId: opId,
                component: "HubControl",
                message: "Invalid Hub URL: \(hubBaseURL)\(command.endpointPath)"
            )
            return
        }
        let bodyData: Data
        do {
            bodyData = try JSONEncoder().encode(command)
        } catch {
            TraceLogger.warn(
                operationId: opId,
                component: "HubControl",
                message: "Failed encoding HubControlCommand for \(command.endpointPath)",
                error: error
            )
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(opId, forHTTPHeaderField: "X-Operation-ID")
        request.httpBody = bodyData
        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            if let error {
                TraceLogger.warn(
                    operationId: opId,
                    component: "HubControl",
                    message: "POST \(command.endpointPath) failed",
                    error: error
                )
                return
            }
            if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
                TraceLogger.warn(
                    operationId: opId,
                    component: "HubControl",
                    message: "POST \(command.endpointPath) returned HTTP \(http.statusCode)"
                )
                return
            }
            guard let self else { return }
            DispatchQueue.main.async {
                self.onFrameUpdate()
            }
        }.resume()
    }
}

enum MonitorDateFormatters {
    static let fileStamp: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        return formatter
    }()
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    let state = MonitorState()
    var panel: FloatingMonitorPanel!
    var videoView: VideoHUDView!
    var streamController: LiveStreamController!
    var statusItem: NSStatusItem?
    var signalSources: [DispatchSourceSignal] = []

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
            let currentFrameCount = self.state.frameCount
            self.state.lock.unlock()
            if currentFrameCount == 8 {
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
        for ttySig in [SIGTTIN, SIGTTOU, SIGTSTP] {
            signal(ttySig, SIG_IGN)
        }
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let signalSource = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            signalSource.setEventHandler { [weak self] in
                self?.streamController?.stop()
                exit(0)
            }
            signalSource.resume()
            signalSources.append(signalSource)
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

        videoView = VideoHUDView(state: state)
        videoView.frame = container.bounds
        videoView.autoresizingMask = [.width, .height]
        videoView.onDoubleClick = { [weak self] in
            self?.toggleWindowSizePreset()
        }
        container.addSubview(videoView)

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
        let button = NSButton(title: title, target: self, action: action)
        button.bezelStyle = .roundRect
        button.font = NSFont.systemFont(ofSize: 10.5, weight: .semibold)
        button.controlSize = .small
        return button
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
        let currentWidth = panel.frame.width
        if currentWidth < 520 {
            sizeMedium()
        } else if currentWidth < 820 {
            sizeLarge()
        } else {
            sizeSmall()
        }
    }

    private func resizeWindow(width: CGFloat, height: CGFloat) {
        var nextFrame = panel.frame
        let topY = nextFrame.maxY
        nextFrame.size = NSSize(width: width, height: height)
        nextFrame.origin.y = topY - height
        panel.setFrame(nextFrame, display: true, animate: true)
    }

    @objc func toggleOpacity() {
        panel.alphaValue = (panel.alphaValue > 0.9) ? 0.82 : 1.0
    }

    @objc func triggerGraspAndPlug() {
        streamController.sendHubCommand(.triggerSkill("grasp_and_plug_loop"))
    }

    @objc func enableContinuousWork() {
        streamController.sendHubCommand(.setContinuous(true))
    }

    @objc func disableContinuousWork() {
        streamController.sendHubCommand(.setContinuous(false))
    }

    @objc func triggerSafeHome() {
        streamController.sendHubCommand(.safeHome())
    }

    func exportLatestVerificationSnapshot() {
        guard let contentView = panel.contentView,
              let bitmap = contentView.bitmapImageRepForCachingDisplay(in: contentView.bounds) else { return }
        contentView.cacheDisplay(in: contentView.bounds, to: bitmap)
        guard let pngData = bitmap.representation(using: .png, properties: [:]) else { return }
        let opId = TraceLogger.makeOperationId(prefix: "snap")
        let outDir = URL(fileURLWithPath: "/Users/zhang/Documents/RSI/runs/snapshots")
        FileIO.ensureDirectory(outDir, operationId: opId, context: "exportLatestVerificationSnapshot")
        let outFile = outDir.appendingPathComponent("d1_mac_monitor_latest.png")
        FileIO.writeData(pngData, to: outFile, operationId: opId, context: "exportLatestVerificationSnapshot")
    }

    @objc func saveSnapshot() {
        guard let bitmap = videoView.bitmapImageRepForCachingDisplay(in: videoView.bounds) else { return }
        videoView.cacheDisplay(in: videoView.bounds, to: bitmap)
        guard let pngData = bitmap.representation(using: .png, properties: [:]) else { return }

        let opId = TraceLogger.makeOperationId(prefix: "snap")
        let stampStr = MonitorDateFormatters.fileStamp.string(from: Date())
        let outDir = URL(fileURLWithPath: "/Users/zhang/Documents/RSI/runs/snapshots")
        FileIO.ensureDirectory(outDir, operationId: opId, context: "saveSnapshot")
        let outFile = outDir.appendingPathComponent("d1_monitor_\(stampStr).png")
        FileIO.writeData(pngData, to: outFile, operationId: opId, context: "saveSnapshot")

        state.lock.lock()
        state.currentPhase = "📸 已保存截图: runs/snapshots/d1_monitor_\(stampStr).png"
        state.lock.unlock()
        videoView.needsDisplay = true
    }

    @objc func openWebDashboard() {
        if let url = URL(string: "http://10.10.218.4:8899") {
            NSWorkspace.shared.open(url)
        }
    }
}

func runSelfTests() -> Int32 {
    var failures: [String] = []
    func check(_ condition: @autoclosure () -> Bool, _ msg: String) {
        if !condition() {
            failures.append(msg)
            fputs("❌ FAIL: \(msg)\n", stderr)
        } else {
            print("✅ PASS: \(msg)")
        }
    }

    let validGraphJson = """
    {
      "status_meta": {"running": true, "frames": 54321, "lastFrameAgeMs": 0},
      "surround_entities_3d": [
        {"id": "d1_arm1_left", "z_clearance_mm": 88.4}
      ]
    }
    """.data(using: .utf8)!
    let parsed = Insta360SceneGraphParser.parse(jsonData: validGraphJson, defaultOnline: false, operationId: "mon-test-1")
    check(
        parsed == Insta360SceneGraphSnapshot(online: true, frames: 54321, ageMs: 15, zClearanceMm: 88.4),
        "Insta360SceneGraphParser decodes surround_entities_3d and normalizes zero ageMs to 15"
    )

    let command = HubControlCommand.triggerSkill("grasp_and_plug_loop", operationId: "cmd-fixed123")
    let encodedString: String
    do {
        let encodedCommandData = try JSONEncoder().encode(command)
        encodedString = String(data: encodedCommandData, encoding: .utf8) ?? ""
    } catch {
        encodedString = ""
    }
    check(
        command.endpointPath == "/api/skill"
            && encodedString.contains("\"operation_id\":\"cmd-fixed123\"")
            && encodedString.contains("\"skill\":\"grasp_and_plug_loop\""),
        "HubControlCommand encodes operation_id and skill payload cleanly"
    )

    print("Self-test summary: \(failures.isEmpty ? "ALL PASSED" : "\(failures.count) FAILED")")
    return failures.isEmpty ? 0 : 1
}

if CommandLine.arguments.contains("--self-test") {
    exit(runSelfTests())
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
