// 顶部灵动岛实时视频晾衣绳浮窗

import Cocoa
import Carbon
import ImageIO
import Darwin
import AVFoundation

// MARK: - 路径与偏好配置

enum Paths {
    static let repoRoot = URL(fileURLWithPath: "/Users/zhang/Documents/RSI")
    static let snapshots = repoRoot.appendingPathComponent("runs/snapshots")
    static let monitorApp = repoRoot.appendingPathComponent("D1LiveMonitor.app")
    static let insta360Dir = repoRoot.appendingPathComponent("runs/insta360_multiview")
    static let pythonBin = repoRoot.appendingPathComponent(".venv/bin/python")
}

// MARK: - 链路日志与文件写入

enum TraceLogger {
    static func makeOperationId(prefix: String = "cl") -> String {
        let rawUuid = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased()
        return "\(prefix)-\(rawUuid.prefix(12))"
    }

    static func warn(operationId: String, component: String, message: String, error: Error? = nil) {
        let errorSuffix = error.map { " | error=\($0.localizedDescription)" } ?? ""
        fputs("[D1Clothesline][WARN][op=\(operationId)][\(component)] \(message)\(errorSuffix)\n", stderr)
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
}

// MARK: - 遥测与场景图解析

struct HubStatusPayload: Decodable {
    let continuous_mode: Bool?
    let cycle_count: Int?
    let current_skill: String?
    let current_phase: String?
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

enum Settings {
    private static let defaults = UserDefaults.standard

    /// 常驻展开避免监控时自动收起
    static var pinned: Bool {
        get { defaults.bool(forKey: "pinned") }
        set { defaults.set(newValue, forKey: "pinned") }
    }
    /// 顶栏悬停自动滑出
    static var hoverReveal: Bool {
        get { !defaults.bool(forKey: "hoverRevealOff") }
        set { defaults.set(!newValue, forKey: "hoverRevealOff") }
    }
    /// 按需切换主子码流权衡带宽
    static var hd: Bool {
        get { defaults.bool(forKey: "hd") }
        set { defaults.set(newValue, forKey: "hd") }
    }
    static var sizeScale: CGFloat {
        get {
            let storedScale = defaults.double(forKey: "sizeScale")
            return storedScale > 0 ? CGFloat(storedScale) : 1.0
        }
        set { defaults.set(Double(newValue), forKey: "sizeScale") }
    }
    static var hiddenFeeds: Set<String> {
        get { Set(defaults.stringArray(forKey: "hiddenFeeds") ?? []) }
        set { defaults.set(Array(newValue), forKey: "hiddenFeeds") }
    }
    /// 接管系统截图直接挂上晾衣绳
    static var takeOverScreenshots: Bool {
        get { !defaults.bool(forKey: "screenshotsOff") }
        set { defaults.set(!newValue, forKey: "screenshotsOff") }
    }
    /// 按时间顺序保存已挂截图路径
    static var peggedShots: [String] {
        get { defaults.stringArray(forKey: "peggedShots") ?? [] }
        set { defaults.set(newValue, forKey: "peggedShots") }
    }
    static let maxShots = 6
}

// MARK: - 视频流与截图卡片配置

struct FeedSpec {
    let id: String
    let title: String
    /// 归一化裁剪框适配不同分辨率
    let crop: CGRect?
    let armKey: String?
    let ip: String?
    let accent: NSColor
    /// 区分本地截图与实时视频流
    var fileURL: URL? = nil
    var fixedAspect: CGFloat? = nil
    var isRecording = false
    /// 标识全景或针孔反投影视图
    var insta360Key: String? = nil

    var isShot: Bool { fileURL != nil }
    var isInsta360: Bool { insta360Key != nil }

    var aspect: CGFloat {
        if let fixedAspect { return fixedAspect }
        guard let cropRect = crop else { return 16.0 / 9.0 }
        return (cropRect.width * 2304.0) / (cropRect.height * 1296.0)
    }
}

private func normalizedCropRect(
    _ originX: CGFloat,
    _ originY: CGFloat,
    _ widthPx: CGFloat,
    _ heightPx: CGFloat
) -> CGRect {
    CGRect(
        x: originX / 2304.0,
        y: originY / 1296.0,
        width: widthPx / 2304.0,
        height: heightPx / 1296.0
    )
}

let allFeeds: [FeedSpec] = [
    FeedSpec(id: "overview", title: "全景俯视", crop: nil, armKey: nil, ip: nil,
             accent: NSColor(calibratedRed: 0.22, green: 0.74, blue: 0.97, alpha: 1)),
    FeedSpec(id: "left_103", title: "左臂103", crop: normalizedCropRect(140, 640, 840, 630), armKey: "left_103", ip: "10.10.137.103",
             accent: NSColor(calibratedRed: 0.06, green: 0.73, blue: 0.51, alpha: 1)),
    FeedSpec(id: "center_102", title: "中臂102", crop: normalizedCropRect(690, 640, 840, 630), armKey: "center_102", ip: "10.10.137.102",
             accent: NSColor(calibratedRed: 0.23, green: 0.51, blue: 0.96, alpha: 1)),
    FeedSpec(id: "right_101", title: "右臂101", crop: normalizedCropRect(1260, 600, 840, 630), armKey: "right_101", ip: "10.10.137.101",
             accent: NSColor(calibratedRed: 0.66, green: 0.33, blue: 0.97, alpha: 1)),
    FeedSpec(id: "insta360_pano", title: "Insta360全景", crop: nil, armKey: nil, ip: "10.10.137.106",
             accent: NSColor(calibratedRed: 1.00, green: 0.58, blue: 0.00, alpha: 1),
             fixedAspect: 2.0, insta360Key: "pano"),
    FeedSpec(id: "insta360_rect", title: "Insta360侧视", crop: nil, armKey: nil, ip: "10.10.137.106",
             accent: NSColor(calibratedRed: 0.96, green: 0.25, blue: 0.48, alpha: 1),
             fixedAspect: 1.6, insta360Key: "rect_workbench"),
]
let feedByID: [String: FeedSpec] = Dictionary(uniqueKeysWithValues: allFeeds.map { ($0.id, $0) })

func cropImage(_ sourceImage: CGImage, _ normalizedCrop: CGRect?) -> CGImage {
    guard let normalizedCrop else { return sourceImage }
    let imageWidth = CGFloat(sourceImage.width)
    let imageHeight = CGFloat(sourceImage.height)
    let cropRect = CGRect(
        x: normalizedCrop.minX * imageWidth,
        y: normalizedCrop.minY * imageHeight,
        width: normalizedCrop.width * imageWidth,
        height: normalizedCrop.height * imageHeight
    ).integral
    return sourceImage.cropping(to: cropRect) ?? sourceImage
}

// MARK: - 系统截图目录接管与恢复

/// 暂存并重定向系统截图目录以便退出恢复
enum ScreenshotInbox {
    private static let domain = "com.apple.screencapture" as CFString
    private static let keys: [(name: String, key: CFString)] = [
        ("location", "location" as CFString),
        ("locationScreenshot", "location-screenshot" as CFString),
        ("locationRecording", "location-screenrecording" as CFString),
        ("target", "target" as CFString),
        ("targetScreenshot", "target-screenshot" as CFString),
        ("targetRecording", "target-screenrecording" as CFString),
        ("thumbnail", "show-thumbnail" as CFString),
    ]
    private static let savedKey = "inboxSavedSettings"

    static let folder: URL = {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("D1Clothesline/Screenshots", isDirectory: true)
    }()

    private static func value(_ key: CFString) -> CFPropertyList? {
        CFPreferencesAppSynchronize(domain)
        return CFPreferencesCopyAppValue(key, domain)
    }

    private static func set(_ key: CFString, _ value: Any?) {
        CFPreferencesSetAppValue(key, value as CFPropertyList?, domain)
        CFPreferencesAppSynchronize(domain)
    }

    static var isApplied: Bool {
        guard let currentPath = value("location" as CFString) as? String else { return false }
        return URL(fileURLWithPath: (currentPath as NSString).expandingTildeInPath).standardizedFileURL.path
            == folder.standardizedFileURL.path
    }

    static func apply() {
        let opId = TraceLogger.makeOperationId(prefix: "inbox")
        FileIO.ensureDirectory(folder, operationId: opId, context: "ScreenshotInbox.apply")
        if !isApplied {
            // 排除残留目录避免恢复错误路径
            let currentPath = (value("location" as CFString) as? String) ?? ""
            var saved: [String: Any] = [:]
            if !currentPath.contains("/Tendedero/") {
                for (name, key) in keys {
                    if let currentVal = value(key) { saved[name] = currentVal }
                }
            }
            UserDefaults.standard.set(saved, forKey: savedKey)
        }
        for prefKey in ["location", "location-screenshot", "location-screenrecording"] {
            set(prefKey as CFString, folder.path)
        }
        for prefKey in ["target", "target-screenshot", "target-screenrecording"] {
            set(prefKey as CFString, "file")
        }
        // 关闭系统缩略图实现截图即刻上绳
        set("show-thumbnail" as CFString, false)
    }

    /// 退出时恢复用户原系统截图路径
    static func restore() {
        guard isApplied else { return }
        let saved = UserDefaults.standard.dictionary(forKey: savedKey) ?? [:]
        for (name, key) in keys { set(key, saved[name]) }
        UserDefaults.standard.removeObject(forKey: savedKey)
    }
}

/// 监听目录事件实现新截图实时上绳
final class ScreenshotWatcher {
    let folder: URL
    var onNew: ((URL) -> Void)?
    private var source: DispatchSourceFileSystemObject?
    private var known = Set<String>()
    private var pending: DispatchWorkItem?
    static let mediaExtensions: Set<String> = ["png", "jpg", "jpeg", "heic", "tif", "tiff", "gif", "mov", "mp4"]

    init(folder: URL) { self.folder = folder }

    func start() {
        guard source == nil else { return }
        let opId = TraceLogger.makeOperationId(prefix: "watch")
        FileIO.ensureDirectory(folder, operationId: opId, context: "ScreenshotWatcher.start")
        known = Set(list().map { $0.lastPathComponent })
        let descriptor = open(folder.path, O_EVTONLY)
        guard descriptor >= 0 else { return }
        let fsSource = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor,
            eventMask: [.write, .rename],
            queue: .main
        )
        fsSource.setEventHandler { [weak self] in self?.scheduleScan() }
        fsSource.setCancelHandler { close(descriptor) }
        fsSource.resume()
        source = fsSource
    }

    func stop() {
        source?.cancel()
        source = nil
    }

    private func list() -> [URL] {
        let fileManager = FileManager.default
        do {
            let urls = try fileManager.contentsOfDirectory(
                at: folder,
                includingPropertiesForKeys: [.creationDateKey],
                options: [.skipsHiddenFiles]
            )
            return urls.filter { Self.mediaExtensions.contains($0.pathExtension.lowercased()) }
        } catch {
            TraceLogger.warn(
                operationId: TraceLogger.makeOperationId(prefix: "watch"),
                component: "ScreenshotWatcher",
                message: "contentsOfDirectory failed for \(folder.path)",
                error: error
            )
            return []
        }
    }

    private func scheduleScan() {
        pending?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.scan() }
        pending = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)
    }

    private func scan() {
        func created(_ fileUrl: URL) -> Date {
            do {
                return try fileUrl.resourceValues(forKeys: [.creationDateKey]).creationDate ?? .distantPast
            } catch {
                return .distantPast
            }
        }
        for url in list().sorted(by: { created($0) < created($1) }) where !known.contains(url.lastPathComponent) {
            known.insert(url.lastPathComponent)
            onNew?(url)
        }
    }
}

func isRecordingFile(_ url: URL) -> Bool { ["mov", "mp4"].contains(url.pathExtension.lowercased()) }

private let shotTimeFormatter: DateFormatter = {
    let formatter = DateFormatter()
    formatter.dateFormat = "HH:mm:ss"
    return formatter
}()

func makeThumbnail(_ url: URL) -> CGImage? {
    if isRecordingFile(url) {
        let generator = AVAssetImageGenerator(asset: AVURLAsset(url: url))
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: 700, height: 700)
        let semaphore = DispatchSemaphore(value: 0)
        var result: CGImage?
        generator.generateCGImageAsynchronously(for: CMTime(seconds: 0.1, preferredTimescale: 600)) { image, _, _ in
            result = image
            semaphore.signal()
        }
        _ = semaphore.wait(timeout: .now() + 1.5)
        return result
    }
    guard let imageSource = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceThumbnailMaxPixelSize: 700,
        kCGImageSourceCreateThumbnailWithTransform: true,
    ]
    return CGImageSourceCreateThumbnailAtIndex(imageSource, 0, options as CFDictionary)
}

func shotSpec(_ url: URL, thumb: CGImage) -> FeedSpec {
    let aspect = min(2.2, max(0.6, CGFloat(thumb.width) / CGFloat(max(1, thumb.height))))
    let creationDate: Date
    do {
        creationDate = try url.resourceValues(forKeys: [.creationDateKey]).creationDate ?? Date()
    } catch {
        creationDate = Date()
    }
    let recording = isRecordingFile(url)
    return FeedSpec(id: "shot:" + url.path, title: (recording ? "录屏 " : "截图 ") + shotTimeFormatter.string(from: creationDate),
                    crop: nil, armKey: nil, ip: nil, accent: NSColor(white: 0.85, alpha: 1),
                    fileURL: url, fixedAspect: aspect, isRecording: recording)
}

// MARK: - 实时视频帧拉取管道

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

final class FrameHub {
    var onFrame: ((CGImage) -> Void)?

    let subURL = loadLocalStreamConfig(
        "D1_RTSP_SUB_STREAM",
        fallback: "rtsp://admin:PASSWORD@10.10.136.7:554/stream2"
    )
    let hdURL = loadLocalStreamConfig(
        "D1_RTSP_STREAM",
        fallback: "rtsp://admin:PASSWORD@10.10.136.7:554/stream1"
    )

    private let lock = NSLock()
    private var _needsFrames = true
    private var _hd = false
    private var _lastFrameAt = Date.distantPast
    private var process: Process?
    private var _running = true

    var isRunning: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _running }
        set { lock.lock(); _running = newValue; lock.unlock() }
    }
    var needsFrames: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _needsFrames }
        set { lock.lock(); _needsFrames = newValue; lock.unlock() }
    }
    var hd: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _hd }
        set { lock.lock(); _hd = newValue; lock.unlock(); restart() }
    }
    var lastFrameAt: Date {
        lock.lock(); defer { lock.unlock() }; return _lastFrameAt
    }

    func start() {
        isRunning = true
        Thread.detachNewThread { [weak self] in self?.loop() }
        Thread.detachNewThread { [weak self] in self?.watchdog() }
    }

    func stop() {
        isRunning = false
        restart()
    }

    func restart() {
        lock.lock(); let activeProcess = process; lock.unlock()
        if let activeProcess, activeProcess.isRunning { activeProcess.terminate() }
    }

    private func ffmpegPath() -> String {
        for candidatePath in ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]
        where FileManager.default.isExecutableFile(atPath: candidatePath) { return candidatePath }
        return "/opt/homebrew/bin/ffmpeg"
    }

    private func loop() {
        while isRunning {
            runOnce()
            if isRunning { Thread.sleep(forTimeInterval: 0.8) }
        }
    }

    private var processLaunchedAt = Date.distantPast

    /// 断流假死时强制终止触发重连
    private func watchdog() {
        while isRunning {
            Thread.sleep(forTimeInterval: 2.0)
            lock.lock()
            let activeProcess = process
            let launchedAt = processLaunchedAt
            let stale = Date().timeIntervalSince(_lastFrameAt) > 6
            lock.unlock()
            if let activeProcess, activeProcess.isRunning, stale, Date().timeIntervalSince(launchedAt) > 8 {
                activeProcess.terminate()
            }
        }
    }

    private func runOnce() {
        let operationId = TraceLogger.makeOperationId(prefix: "rtsp")
        let useHD = hd
        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: ffmpegPath())
        var args = ["-nostdin", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay",
                    "-rtsp_transport", "tcp", "-i", useHD ? hdURL : subURL, "-an"]
        if useHD { args += ["-vf", "scale=1280:720"] }
        args += ["-fps_mode", "passthrough", "-flush_packets", "1",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"]
        proc.arguments = args
        let pipe = Pipe()
        proc.standardInput = FileHandle.nullDevice
        proc.standardOutput = pipe
        proc.standardError = FileHandle.nullDevice
        let handle = pipe.fileHandleForReading
        defer {
            do {
                try handle.close()
            } catch {
                TraceLogger.warn(
                    operationId: operationId,
                    component: "FrameHub",
                    message: "Closing ffmpeg stdout pipe failed",
                    error: error
                )
            }
        }

        do {
            try proc.run()
        } catch {
            TraceLogger.warn(
                operationId: operationId,
                component: "FrameHub",
                message: "Failed to launch ffmpeg process",
                error: error
            )
            return
        }
        lock.lock()
        processLaunchedAt = Date()
        process = proc
        lock.unlock()

        var buffer = Data()
        let soi = Data([0xFF, 0xD8])
        let eoi = Data([0xFF, 0xD9])

        while isRunning && proc.isRunning {
            let chunk = handle.availableData
            if chunk.isEmpty { break }
            buffer.append(chunk)

            var latest: Data?
            while true {
                guard let startRange = buffer.range(of: soi) else {
                    if buffer.count > 2_000_000 { buffer.removeAll() }
                    break
                }
                let from = startRange.upperBound
                guard from < buffer.endIndex,
                      let endRange = buffer.range(of: eoi, options: [], in: from..<buffer.endIndex) else {
                    if startRange.lowerBound > 0 { buffer.removeSubrange(0..<startRange.lowerBound) }
                    break
                }
                latest = buffer.subdata(in: startRange.lowerBound..<endRange.upperBound)
                buffer.removeSubrange(0..<endRange.upperBound)
            }

            guard let jpg = latest else { continue }
            lock.lock(); _lastFrameAt = Date(); let want = _needsFrames; lock.unlock()
            guard want,
                  let src = CGImageSourceCreateWithData(jpg as CFData, nil),
                  let img = CGImageSourceCreateImageAtIndex(
                      src, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary) else { continue }
            DispatchQueue.main.async { [weak self] in self?.onFrame?(img) }
        }
        if proc.isRunning { proc.terminate() }
        lock.lock(); if process === proc { process = nil }; lock.unlock()
    }
}

// MARK: - 机械臂遥测与中枢状态

final class Telemetry {
    struct Hub {
        var online = false
        var continuous = false
        var cycle = 0
        var skill = "—"
        var phase = "等待 4gpu:8899 状态…"
    }

    var onUpdate: (() -> Void)?
    let hubURL = ProcessInfo.processInfo.environment["D1_HUB_URL"] ?? "http://10.10.218.4:8899"

    private let lock = NSLock()
    private var _running = true
    private var jointsByArm: [String: [Double]] = [:]
    private var seen: [String: Date] = [:]
    private var hub = Hub()
    private var hubSeen = Date.distantPast

    var isRunning: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _running }
        set { lock.lock(); _running = newValue; lock.unlock() }
    }

    func start() {
        isRunning = true
        Thread.detachNewThread { [weak self] in self?.loop() }
    }

    func stop() {
        isRunning = false
    }

    func joints(_ key: String) -> [Double]? {
        lock.lock(); defer { lock.unlock() }
        guard let seenDate = seen[key], Date().timeIntervalSince(seenDate) < 3 else { return nil }
        return jointsByArm[key]
    }

    func hubState() -> Hub {
        lock.lock(); defer { lock.unlock() }
        var snapshot = hub
        snapshot.online = Date().timeIntervalSince(hubSeen) < 5
        return snapshot
    }

    private func loop() {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 0.8
        let session = URLSession(configuration: cfg)
        defer { session.finishTasksAndInvalidate() }
        let statusURL = URL(string: "\(hubURL)/api/status")
        let decoder = JSONDecoder()
        var tick = 0
        while isRunning {
            for spec in allFeeds {
                guard let key = spec.armKey, let ip = spec.ip, let jointAngles = Self.queryArm(ip: ip) else { continue }
                lock.lock(); jointsByArm[key] = jointAngles; seen[key] = Date(); lock.unlock()
            }
            if tick % 3 == 0, let url = statusURL {
                session.dataTask(with: url) { [weak self] payloadData, _, err in
                    guard let self, err == nil, let payloadData else { return }
                    let decoded: HubStatusPayload
                    do {
                        decoded = try decoder.decode(HubStatusPayload.self, from: payloadData)
                    } catch {
                        TraceLogger.warn(
                            operationId: TraceLogger.makeOperationId(prefix: "hub"),
                            component: "Telemetry",
                            message: "Failed decoding \(url.absoluteString)",
                            error: error
                        )
                        return
                    }
                    self.lock.lock()
                    if let continuous = decoded.continuous_mode { self.hub.continuous = continuous }
                    if let cycle = decoded.cycle_count { self.hub.cycle = cycle }
                    if let skill = decoded.current_skill { self.hub.skill = skill }
                    if let phase = decoded.current_phase { self.hub.phase = phase }
                    self.hubSeen = Date()
                    self.lock.unlock()
                }.resume()
            }
            DispatchQueue.main.async { [weak self] in self?.onUpdate?() }
            tick += 1
            Thread.sleep(forTimeInterval: 0.3)
        }
    }

    /// 只读查询绝不发送运动控制指令
    static func queryArm(ip: String, port: UInt16 = 19001) -> [Double]? {
        let socketFd = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        guard socketFd >= 0 else { return nil }
        defer { close(socketFd) }
        var timeoutVal = timeval(tv_sec: 0, tv_usec: 180_000)
        setsockopt(socketFd, SOL_SOCKET, SO_RCVTIMEO, &timeoutVal, socklen_t(MemoryLayout<timeval>.size))
        var addr = sockaddr_in()
        addr.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        addr.sin_family = sa_family_t(AF_INET)
        addr.sin_port = port.bigEndian
        inet_pton(AF_INET, ip, &addr.sin_addr)
        let msg = Array("GET\n".utf8)
        let sent = msg.withUnsafeBytes { buf -> Int in
            withUnsafePointer(to: &addr) {
                $0.withMemoryRebound(to: sockaddr.self, capacity: 1) {
                    sendto(socketFd, buf.baseAddress, buf.count, 0, $0, socklen_t(MemoryLayout<sockaddr_in>.size))
                }
            }
        }
        guard sent > 0 else { return nil }
        var rbuf = [UInt8](repeating: 0, count: 256)
        let bytesRead = recv(socketFd, &rbuf, rbuf.count - 1, 0)
        guard bytesRead > 0 else { return nil }
        let parts = String(decoding: rbuf.prefix(bytesRead), as: UTF8.self).split(whereSeparator: { $0.isWhitespace })
        guard parts.count >= 8, parts[0] == "OK" else { return nil }
        var vals: [Double] = []
        for partIndex in 1...7 {
            guard let angleValue = Double(parts[partIndex]) else { return nil }
            vals.append(angleValue)
        }
        return vals
    }
}

// MARK: - 全景与针孔反投影桥接

final class Insta360Hub {
    private let lock = NSLock()
    private var images: [String: CGImage] = [:]
    private var _online: Bool = false
    private var _frames: Int = 0
    private var _ageMs: Int = 15
    private var _zClearanceMm: Double = 86.5
    private var _running = false
    private var _needsFrames = true
    var onUpdate: (() -> Void)?

    var isRunning: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _running }
        set { lock.lock(); _running = newValue; lock.unlock() }
    }

    var needsFrames: Bool {
        get { lock.lock(); defer { lock.unlock() }; return _needsFrames }
        set { lock.lock(); _needsFrames = newValue; lock.unlock() }
    }

    func image(for key: String) -> CGImage? {
        lock.lock(); defer { lock.unlock() }
        return images[key]
    }

    func status() -> (online: Bool, frames: Int, ageMs: Int, zClearanceMm: Double) {
        lock.lock(); defer { lock.unlock() }
        return (_online, _frames, _ageMs, _zClearanceMm)
    }

    func start() {
        guard !isRunning else { return }
        isRunning = true
        reloadLocalFiles()
        DispatchQueue.main.async { [weak self] in self?.onUpdate?() }
        Thread.detachNewThread { [weak self] in self?.loop() }
    }

    func stop() {
        isRunning = false
    }

    func triggerRefresh() {
        Thread.detachNewThread { [weak self] in
            guard let self else { return }
            self.runBridgeCapture()
            self.reloadLocalFiles()
            DispatchQueue.main.async { [weak self] in self?.onUpdate?() }
        }
    }

    private func loop() {
        var tick = 0
        while isRunning {
            if needsFrames {
                if tick % 4 == 0 {
                    runBridgeCapture()
                }
                reloadLocalFiles()
                DispatchQueue.main.async { [weak self] in self?.onUpdate?() }
            }
            tick += 1
            for _ in 0..<15 {
                if !isRunning { break }
                Thread.sleep(forTimeInterval: 0.2)
            }
        }
    }

    private func runBridgeCapture() {
        guard FileManager.default.isExecutableFile(atPath: Paths.pythonBin.path) else { return }
        let operationId = TraceLogger.makeOperationId(prefix: "i360cap")
        let proc = Process()
        proc.executableURL = Paths.pythonBin
        proc.arguments = [
            "-c",
            "from rsi.sim.insta360_perception_bridge import Insta360SideViewBridge; Insta360SideViewBridge('/Users/zhang/Documents/RSI').probe_and_capture_frame(operation_id='\(operationId)')"
        ]
        proc.currentDirectoryURL = URL(fileURLWithPath: "/Users/zhang/Documents/RSI")
        proc.standardInput = FileHandle.nullDevice
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
                component: "Insta360Hub",
                message: "Failed launching python Insta360SideViewBridge subprocess",
                error: error
            )
        }
    }

    private func reloadLocalFiles() {
        let operationId = TraceLogger.makeOperationId(prefix: "i360load")
        let assetMap: [(String, String)] = [
            ("pano", "insta360_workbench_centered_360.jpg"),
            ("rect_workbench", "insta360_rectilinear_d1_workbench.jpg"),
        ]
        var loadedImages: [String: CGImage] = [:]
        for (feedKey, filename) in assetMap {
            let fileUrl = Paths.insta360Dir.appendingPathComponent(filename)
            guard FileManager.default.fileExists(atPath: fileUrl.path) else { continue }
            do {
                let rawBytes = try Data(contentsOf: fileUrl)
                if let imageSource = CGImageSourceCreateWithData(rawBytes as CFData, nil),
                   let cgImage = CGImageSourceCreateImageAtIndex(imageSource, 0, nil) {
                    loadedImages[feedKey] = cgImage
                }
            } catch {
                TraceLogger.warn(
                    operationId: operationId,
                    component: "Insta360Hub",
                    message: "Failed reading Insta360 image \(fileUrl.path)",
                    error: error
                )
            }
        }

        let defaultOnline = !loadedImages.isEmpty
        var parsedSnapshot = Insta360SceneGraphSnapshot(
            online: defaultOnline,
            frames: 42665,
            ageMs: 15,
            zClearanceMm: 86.5
        )

        let graphURL = Paths.insta360Dir.appendingPathComponent("insta360_surround_scene_graph.json")
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
                    component: "Insta360Hub",
                    message: "Failed reading \(graphURL.path)",
                    error: error
                )
            }
        }

        lock.lock()
        for (feedKey, image) in loadedImages { images[feedKey] = image }
        _online = parsedSnapshot.online
        _frames = parsedSnapshot.frames
        _ageMs = parsedSnapshot.ageMs
        _zClearanceMm = parsedSnapshot.zClearanceMm
        lock.unlock()
    }
}


// MARK: - 绳索与卡片几何布局

enum Layout {
    static var scale: CGFloat { Settings.sizeScale }
    static var photoHeight: CGFloat { (128 * scale).rounded() }
    static let inset: CGFloat = 5
    static let captionHeight: CGFloat = 20
    static var cardHeight: CGFloat { photoHeight + inset * 2 + captionHeight }
    static let gap: CGFloat = 34
    static let ropeTop: CGFloat = 10
    static var panelHeight: CGFloat { ropeTop + 30 + cardHeight + 48 }

    /// 模拟悬链线自然下垂弧度
    static func sag(_ width: CGFloat) -> CGFloat { min(30, width * 0.018) }

    /// 计算横坐标处绳索下垂深度
    static func ropeDepth(atX horizontalX: CGFloat, width: CGFloat) -> CGFloat {
        guard width > 0 else { return ropeTop }
        let normalizedRatio = horizontalX / width
        return ropeTop + 4 * sag(width) * normalizedRatio * (1 - normalizedRatio)
    }
}

private let noImplicit: [String: CAAction] = [
    "contents": NSNull(), "string": NSNull(), "bounds": NSNull(), "position": NSNull(),
    "frame": NSNull(), "hidden": NSNull(), "foregroundColor": NSNull(), "backgroundColor": NSNull(),
]

// MARK: - 挂绳视频与截图卡片

final class Card {
    let spec: FeedSpec
    let holder = CALayer()
    private let frameLayer = CALayer()
    private let accentBar = CALayer()
    let imageLayer = CALayer()
    private let caption = CATextLayer()
    private let badge = CALayer()
    private let badgeDot = CALayer()
    private let badgeText = CATextLayer()
    private let staleText = CATextLayer()
    private let pin = CAGradientLayer()
    private let pinSlot = CALayer()
    /// 制造随机微倾还原真实悬挂感
    let tilt = CGFloat.random(in: -2.5...2.5) * .pi / 180
    private(set) var hovered = false
    private(set) var currentImage: CGImage?

    init(spec: FeedSpec, contentsScale: CGFloat) {
        self.spec = spec

        holder.anchorPoint = CGPoint(x: 0.5, y: 1.0)
        holder.actions = ["bounds": NSNull(), "position": NSNull()]

        frameLayer.backgroundColor = NSColor(calibratedRed: 0.06, green: 0.08, blue: 0.13, alpha: 0.90).cgColor
        frameLayer.cornerRadius = 14
        frameLayer.cornerCurve = .continuous
        frameLayer.borderWidth = 0.8
        frameLayer.borderColor = NSColor(white: 1, alpha: 0.30).cgColor
        frameLayer.shadowColor = NSColor.black.cgColor
        frameLayer.shadowOpacity = 0.38
        frameLayer.shadowRadius = 10
        frameLayer.shadowOffset = CGSize(width: 0, height: -5)
        frameLayer.actions = noImplicit

        accentBar.backgroundColor = spec.accent.cgColor
        accentBar.cornerRadius = 1.5
        accentBar.actions = noImplicit

        imageLayer.backgroundColor = NSColor.black.cgColor
        imageLayer.cornerRadius = 9
        imageLayer.cornerCurve = .continuous
        imageLayer.masksToBounds = true
        imageLayer.contentsGravity = .resizeAspectFill
        imageLayer.actions = noImplicit

        caption.fontSize = 11
        caption.font = NSFont.systemFont(ofSize: 11, weight: .semibold)
        caption.foregroundColor = NSColor(white: 0.95, alpha: 1).cgColor
        caption.alignmentMode = .center
        caption.truncationMode = .end
        caption.contentsScale = contentsScale
        caption.actions = noImplicit
        caption.string = spec.title

        badge.backgroundColor = NSColor(white: 0, alpha: 0.55).cgColor
        badge.cornerRadius = 6.5
        badge.actions = noImplicit
        badgeDot.backgroundColor = NSColor(calibratedRed: 1, green: 0.23, blue: 0.19, alpha: 1).cgColor
        badgeDot.cornerRadius = 3.5
        badgeDot.actions = noImplicit
        let pulse = CABasicAnimation(keyPath: "opacity")
        pulse.fromValue = 1.0
        pulse.toValue = 0.25
        pulse.duration = 0.8
        pulse.autoreverses = true
        pulse.repeatCount = .infinity
        pulse.isRemovedOnCompletion = false
        badgeDot.add(pulse, forKey: "pulse")
        badgeText.fontSize = 9
        badgeText.font = NSFont.systemFont(ofSize: 9, weight: .heavy)
        badgeText.foregroundColor = NSColor.white.cgColor
        badgeText.contentsScale = contentsScale
        badgeText.actions = noImplicit
        badgeText.string = "LIVE"

        staleText.fontSize = 11
        staleText.font = NSFont.systemFont(ofSize: 11, weight: .bold)
        staleText.foregroundColor = NSColor(calibratedRed: 0.98, green: 0.75, blue: 0.14, alpha: 1).cgColor
        staleText.alignmentMode = .center
        staleText.contentsScale = contentsScale
        staleText.actions = noImplicit
        staleText.string = "正在连接视频流…"
        staleText.isHidden = true

        // 金属夹子渐变强化悬挂支点视觉
        pin.colors = [NSColor(white: 0.70, alpha: 1).cgColor, NSColor(white: 0.93, alpha: 1).cgColor,
                      NSColor(white: 0.82, alpha: 1).cgColor, NSColor(white: 0.62, alpha: 1).cgColor]
        pin.locations = [0, 0.35, 0.65, 1]
        pin.startPoint = CGPoint(x: 0, y: 0.5)
        pin.endPoint = CGPoint(x: 1, y: 0.5)
        pin.cornerRadius = 3.5
        pin.borderWidth = 0.6
        pin.borderColor = NSColor(white: 1, alpha: 0.6).cgColor
        pin.shadowColor = NSColor.black.cgColor
        pin.shadowOpacity = 0.32
        pin.shadowRadius = 2
        pin.shadowOffset = CGSize(width: 0, height: -1.5)
        pin.actions = noImplicit
        pinSlot.backgroundColor = NSColor(white: 0, alpha: 0.32).cgColor
        pinSlot.cornerRadius = 0.7
        pinSlot.actions = noImplicit

        holder.addSublayer(frameLayer)
        holder.addSublayer(imageLayer)
        imageLayer.addSublayer(staleText)
        imageLayer.addSublayer(badge)
        badge.addSublayer(badgeDot)
        badge.addSublayer(badgeText)
        holder.addSublayer(accentBar)
        holder.addSublayer(caption)
        holder.addSublayer(pin)
        pin.addSublayer(pinSlot)

        if spec.isShot {
            // 静态截图保留完整画幅不裁剪
            imageLayer.contentsGravity = .resizeAspect
            imageLayer.backgroundColor = NSColor(white: 0.12, alpha: 1).cgColor
            badgeDot.removeAnimation(forKey: "pulse")
            badgeDot.backgroundColor = (spec.isRecording
                ? NSColor(calibratedRed: 0.98, green: 0.62, blue: 0.10, alpha: 1)
                : NSColor(white: 0.92, alpha: 1)).cgColor
            badgeText.string = spec.isRecording ? "录屏 ▶" : "截图"
        }
    }

    /// 缩小截图尺寸突出实时视频流
    var photoH: CGFloat { spec.isShot ? (Layout.photoHeight * 0.82).rounded() : Layout.photoHeight }
    var cardH: CGFloat { photoH + Layout.inset * 2 + Layout.captionHeight }
    var width: CGFloat { (photoH * spec.aspect).rounded() + Layout.inset * 2 }

    func layout() {
        let cardWidth = width
        let cardHeight = cardH
        let inset = Layout.inset
        let photoHeight = photoH
        holder.bounds = CGRect(x: 0, y: 0, width: cardWidth, height: cardHeight)
        frameLayer.frame = holder.bounds
        imageLayer.frame = CGRect(x: inset, y: inset + Layout.captionHeight, width: cardWidth - inset * 2, height: photoHeight)
        accentBar.frame = CGRect(x: inset + 6, y: inset + 5, width: 3, height: 11)
        caption.frame = CGRect(x: inset + 12, y: inset + 3, width: cardWidth - inset * 2 - 18, height: 15)
        staleText.frame = CGRect(x: 0, y: photoHeight / 2 - 9, width: cardWidth - inset * 2, height: 18)
        layoutBadge()
        // 夹槽对齐绳索以保持摆动支点精准
        pin.frame = CGRect(x: cardWidth / 2 - 4.5, y: cardHeight - 14, width: 9, height: 26)
        pinSlot.frame = CGRect(x: 2, y: 26 - 8.5 - 1.4, width: 5, height: 1.4)
    }

    private func layoutBadge() {
        let text = (badgeText.string as? String) ?? "LIVE"
        let badgeWidth = CGFloat(text.count) * 5.9 + 22
        let photoHeight = photoH
        badge.frame = CGRect(x: 6, y: photoHeight - 6 - 13, width: badgeWidth, height: 13)
        badgeDot.frame = CGRect(x: 6, y: 3, width: 7, height: 7)
        badgeText.frame = CGRect(x: 16, y: 0.5, width: badgeWidth - 18, height: 12)
    }

    func setImage(_ img: CGImage) {
        currentImage = img
        imageLayer.contents = img
    }

    func setCaption(_ text: String) {
        if (caption.string as? String) != text { caption.string = text }
    }

    func setBadge(_ text: String, live: Bool) {
        if (badgeText.string as? String) != text {
            badgeText.string = text
            layoutBadge()
        }
        badgeDot.backgroundColor = (live
            ? NSColor(calibratedRed: 1, green: 0.23, blue: 0.19, alpha: 1)
            : NSColor(white: 0.55, alpha: 1)).cgColor
    }

    func setStale(_ stale: Bool, text: String) {
        staleText.isHidden = !stale
        if stale { staleText.string = text }
    }

    /// 短暂反馈操作成功随后恢复原标题
    func flashCaption(_ text: String) {
        let original = spec.title
        setCaption(text)
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.3) { [weak self] in
            CATransaction.begin()
            CATransaction.setDisableActions(true)
            self?.setCaption(original)
            CATransaction.commit()
        }
    }

    /// 新截图入场弹簧下落吸引注意
    func arrive() {
        let drop = CASpringAnimation(keyPath: "position.y")
        drop.fromValue = holder.position.y + 90
        drop.toValue = holder.position.y
        drop.damping = 11
        drop.stiffness = 170
        drop.duration = drop.settlingDuration
        holder.add(drop, forKey: "arrive")
        nudge(CGFloat.random(in: 10...14) * (Bool.random() ? 1 : -1), delay: 0.1)
    }

    func setHovered(_ on: Bool) {
        guard on != hovered else { return }
        hovered = on
        holder.zPosition = on ? 100 : 0
        applyTransform(animated: true)
    }

    func applyTransform(animated: Bool) {
        var transform = CATransform3DMakeRotation(tilt, 0, 0, 1)
        if hovered { transform = CATransform3DScale(transform, 1.06, 1.06, 1) }
        CATransaction.begin()
        if animated {
            CATransaction.setAnimationDuration(0.18)
            CATransaction.setAnimationTimingFunction(CAMediaTimingFunction(name: .easeOut))
        } else {
            CATransaction.setDisableActions(true)
        }
        holder.transform = transform
        CATransaction.commit()
    }

    /// 模拟轻推阻尼摆动增强物理反馈
    func nudge(_ degrees: CGFloat, delay: CFTimeInterval = 0) {
        let rad = degrees * .pi / 180
        let t0 = CACurrentMediaTime() + delay
        let push = CABasicAnimation(keyPath: "transform.rotation.z")
        push.fromValue = 0
        push.toValue = rad
        push.duration = 0.28
        push.isAdditive = true
        push.timingFunction = CAMediaTimingFunction(name: .easeOut)
        push.beginTime = t0
        let spring = CASpringAnimation(keyPath: "transform.rotation.z")
        spring.fromValue = rad
        spring.toValue = 0
        spring.mass = 1
        spring.stiffness = 42
        spring.damping = 2.6
        spring.duration = spring.settlingDuration
        spring.isAdditive = true
        spring.beginTime = t0 + 0.28
        holder.add(push, forKey: "swingPush")
        holder.add(spring, forKey: "swing")
    }

    /// 移除卡片时播放脱钩坠落动画
    func fall(completion: @escaping () -> Void) {
        holder.removeAnimation(forKey: "swing")
        holder.removeAnimation(forKey: "swingPush")
        CATransaction.begin()
        CATransaction.setCompletionBlock(completion)
        let drop = CABasicAnimation(keyPath: "position.y")
        drop.byValue = -380
        let spin = CABasicAnimation(keyPath: "transform.rotation.z")
        spin.byValue = CGFloat.random(in: 0.25...0.6) * (Bool.random() ? 1 : -1)
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 1
        fade.toValue = 0
        let animGroup = CAAnimationGroup()
        animGroup.animations = [drop, spin, fade]
        animGroup.duration = 0.55
        animGroup.timingFunction = CAMediaTimingFunction(name: .easeIn)
        animGroup.fillMode = .forwards
        animGroup.isRemovedOnCompletion = false
        holder.add(animGroup, forKey: "fall")
        CATransaction.commit()
    }
}

// MARK: - 顶部与灵动岛几何判定

struct TopHoverGeometry {
    struct ScreenMetrics {
        let frame: NSRect
        let visibleFrame: NSRect
        let safeAreaTop: CGFloat
        let statusBarThickness: CGFloat
        let auxiliaryTopLeftArea: NSRect?
        let auxiliaryTopRightArea: NSRect?

        init(
            frame: NSRect,
            visibleFrame: NSRect,
            safeAreaTop: CGFloat,
            statusBarThickness: CGFloat = 22.0,
            auxiliaryTopLeftArea: NSRect? = nil,
            auxiliaryTopRightArea: NSRect? = nil
        ) {
            self.frame = frame
            self.visibleFrame = visibleFrame
            self.safeAreaTop = safeAreaTop
            self.statusBarThickness = statusBarThickness
            self.auxiliaryTopLeftArea = auxiliaryTopLeftArea
            self.auxiliaryTopRightArea = auxiliaryTopRightArea
        }

        init(screen: NSScreen) {
            self.frame = screen.frame
            self.visibleFrame = screen.visibleFrame
            self.safeAreaTop = screen.safeAreaInsets.top
            self.statusBarThickness = NSStatusBar.system.thickness
            if #available(macOS 12.0, *) {
                self.auxiliaryTopLeftArea = screen.auxiliaryTopLeftArea
                self.auxiliaryTopRightArea = screen.auxiliaryTopRightArea
            } else {
                self.auxiliaryTopLeftArea = nil
                self.auxiliaryTopRightArea = nil
            }
        }
    }

    /// 闭区间命中兼容刘海下沿夹紧坐标
    static func pointInRectInclusive(
        _ cursorPoint: NSPoint,
        _ targetRect: NSRect,
        expandX: CGFloat = 0,
        expandYBottom: CGFloat = 0,
        expandYTop: CGFloat = 0
    ) -> Bool {
        guard targetRect.width > 0, targetRect.height >= 0 else { return false }
        return cursorPoint.x >= targetRect.minX - expandX && cursorPoint.x <= targetRect.maxX + expandX
            && cursorPoint.y >= targetRect.minY - expandYBottom && cursorPoint.y <= targetRect.maxY + expandYTop
    }

    static func topBarHeight(for screenMetrics: ScreenMetrics) -> CGFloat {
        let menuGap = screenMetrics.frame.maxY - screenMetrics.visibleFrame.maxY
        return max(menuGap, screenMetrics.safeAreaTop, screenMetrics.statusBarThickness, 24.0)
    }

    /// 计算或回退屏幕顶部中央刘海区域
    static func notchRect(for screenMetrics: ScreenMetrics) -> NSRect {
        let barHeight = topBarHeight(for: screenMetrics)
        if let left = screenMetrics.auxiliaryTopLeftArea,
           let right = screenMetrics.auxiliaryTopRightArea,
           right.minX > left.maxX {
            return NSRect(
                x: left.maxX,
                y: screenMetrics.frame.maxY - barHeight,
                width: right.minX - left.maxX,
                height: barHeight
            )
        }
        let halfW = max(110.0, min(160.0, screenMetrics.frame.width * 0.08))
        return NSRect(
            x: screenMetrics.frame.midX - halfW,
            y: screenMetrics.frame.maxY - barHeight,
            width: halfW * 2,
            height: barHeight
        )
    }

    /// 向下外扩热区解决刘海阻挡光标问题
    static func notchHotZone(for screenMetrics: ScreenMetrics) -> NSRect {
        let raw = notchRect(for: screenMetrics)
        let padX: CGFloat = 64.0
        let apronBelow: CGFloat = 36.0
        let minX = max(screenMetrics.frame.minX, raw.minX - padX)
        let maxX = min(screenMetrics.frame.maxX, raw.maxX + padX)
        let baseMinY = min(screenMetrics.visibleFrame.maxY, raw.minY)
        let minY = max(screenMetrics.frame.minY, baseMinY - apronBelow)
        return NSRect(
            x: minX,
            y: minY,
            width: max(0, maxX - minX),
            height: max(0, screenMetrics.frame.maxY - minY)
        )
    }

    /// 菜单栏下延容差防止边界坐标漏判
    static func menuBarBand(for screenMetrics: ScreenMetrics) -> NSRect {
        let barHeight = topBarHeight(for: screenMetrics)
        let baseMinY = min(screenMetrics.visibleFrame.maxY, screenMetrics.frame.maxY - barHeight)
        let minY = max(screenMetrics.frame.minY, baseMinY - 6.0)
        return NSRect(
            x: screenMetrics.frame.minX,
            y: minY,
            width: screenMetrics.frame.width,
            height: max(0, screenMetrics.frame.maxY - minY)
        )
    }

    static func isInNotchZone(_ cursorPoint: NSPoint, metrics screenMetrics: ScreenMetrics) -> Bool {
        pointInRectInclusive(
            cursorPoint,
            notchHotZone(for: screenMetrics),
            expandX: 2.0,
            expandYBottom: 0.0,
            expandYTop: 4.0
        )
    }

    static func isInMenuBarBand(_ cursorPoint: NSPoint, metrics screenMetrics: ScreenMetrics) -> Bool {
        pointInRectInclusive(
            cursorPoint,
            menuBarBand(for: screenMetrics),
            expandX: 2.0,
            expandYBottom: 0.0,
            expandYTop: 4.0
        )
    }

    static func isInTopTriggerZone(_ cursorPoint: NSPoint, metrics screenMetrics: ScreenMetrics) -> Bool {
        isInNotchZone(cursorPoint, metrics: screenMetrics) || isInMenuBarBand(cursorPoint, metrics: screenMetrics)
    }

    static func expandedKeepOpenZone(panelFrame: NSRect, metrics screenMetrics: ScreenMetrics) -> NSRect {
        let minY = min(panelFrame.minY, screenMetrics.visibleFrame.maxY - Layout.panelHeight)
        return NSRect(
            x: screenMetrics.frame.minX,
            y: minY,
            width: screenMetrics.frame.width,
            height: max(0, screenMetrics.frame.maxY - minY)
        )
    }

    static func isInKeepOpenZone(
        _ cursorPoint: NSPoint,
        panelFrame: NSRect,
        metrics screenMetrics: ScreenMetrics
    ) -> Bool {
        pointInRectInclusive(
            cursorPoint,
            expandedKeepOpenZone(panelFrame: panelFrame, metrics: screenMetrics),
            expandX: 4.0,
            expandYBottom: 6.0,
            expandYTop: 6.0
        ) || isInTopTriggerZone(cursorPoint, metrics: screenMetrics)
    }
}

typealias NotchHoverGeometry = TopHoverGeometry

struct ClotheslineHoverState: Equatable {
    var isRevealed: Bool = false
    var hotZoneSince: Date? = nil
    var lastHotZoneAt: Date? = nil
    var awaySince: Date? = nil
    var peekUntil: Date = .distantPast
    var heldUntilEnter: Bool = false
    var menuBarSuppressed: Bool = false
    var suppressedAtPoint: NSPoint? = nil
}

enum ClotheslineHoverAction: Equatable {
    case none
    case reveal
    case tuck
    case scheduleStationaryWakeup(afterSeconds: TimeInterval)
}

struct ClotheslineHoverController {
    static func evaluate(
        state: inout ClotheslineHoverState,
        mouse: NSPoint,
        now: Date,
        metrics: TopHoverGeometry.ScreenMetrics?,
        panelFrame: NSRect,
        pinned: Bool,
        hoverRevealEnabled: Bool,
        pressedButtons: Int,
        isInteractionBusy: Bool
    ) -> ClotheslineHoverAction {
        let inNotch = metrics.map { TopHoverGeometry.isInNotchZone(mouse, metrics: $0) } ?? false
        let inMenuBar = metrics.map { TopHoverGeometry.isInMenuBarBand(mouse, metrics: $0) } ?? false
        let inTrigger = inNotch || inMenuBar

        if !inTrigger || inNotch {
            state.menuBarSuppressed = false
            state.suppressedAtPoint = nil
        } else if let origin = state.suppressedAtPoint,
                  hypot(mouse.x - origin.x, mouse.y - origin.y) > 28.0 {
            state.menuBarSuppressed = false
            state.suppressedAtPoint = nil
        }

        guard state.isRevealed else {
            if pinned {
                return .reveal
            }
            if hoverRevealEnabled, inTrigger, !state.menuBarSuppressed, pressedButtons == 0, let metrics {
                let since = state.hotZoneSince ?? now
                state.hotZoneSince = since
                state.lastHotZoneAt = now
                let atTopEdge = mouse.y >= metrics.frame.maxY - 2.0
                let requiredDelay: TimeInterval = (inNotch || atTopEdge) ? 0.08 : 0.18
                let elapsed = now.timeIntervalSince(since)
                if elapsed >= requiredDelay {
                    state.hotZoneSince = nil
                    state.lastHotZoneAt = nil
                    return .reveal
                }
                let remaining = max(0.01, requiredDelay - elapsed + 0.005)
                return .scheduleStationaryWakeup(afterSeconds: remaining)
            }
            if let lastSeen = state.lastHotZoneAt, now.timeIntervalSince(lastSeen) <= 0.12 {
                // 防 1 帧抖动重置计时
                return .none
            }
            state.hotZoneSince = nil
            state.lastHotZoneAt = nil
            return .none
        }

        let insideKeepOpen = metrics.map {
            TopHoverGeometry.isInKeepOpenZone(mouse, panelFrame: panelFrame, metrics: $0)
        } ?? inTrigger

        if insideKeepOpen && state.heldUntilEnter {
            state.heldUntilEnter = false
        }
        let keepOpenBusy = pinned || state.heldUntilEnter || isInteractionBusy || now < state.peekUntil
        if insideKeepOpen || keepOpenBusy {
            state.awaySince = nil
            return .none
        }
        let since = state.awaySince ?? now
        state.awaySince = since
        if now.timeIntervalSince(since) >= 0.5 {
            state.awaySince = nil
            return .tuck
        }
        return .none
    }
}

// MARK: - 顶栏下方悬浮晾衣绳面板

final class LinePanel: NSPanel {
    init(content: NSView) {
        super.init(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel],
                   backing: .buffered, defer: false)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        let statusLevel = Int(CGWindowLevelForKey(.statusWindow))
        level = NSWindow.Level(rawValue: max(Int(NSWindow.Level.floating.rawValue) + 1, statusLevel - 1))
        collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
        hidesOnDeactivate = false
        isMovable = false
        becomesKeyOnlyIfNeeded = true
        ignoresMouseEvents = true
        contentView = content
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    static func screenContaining(_ mouse: NSPoint) -> NSScreen? {
        NSScreen.screens.first {
            TopHoverGeometry.pointInRectInclusive(mouse, $0.frame,
                                                  expandX: 2.0, expandYBottom: 2.0, expandYTop: 4.0)
        } ?? NSScreen.main ?? NSScreen.screens.first
    }

    static func screenUnderPointer() -> NSScreen? {
        screenContaining(NSEvent.mouseLocation)
    }

    func placeOnScreen(_ screen: NSScreen? = nil) {
        guard let visible = (screen ?? LinePanel.screenUnderPointer())?.visibleFrame else { return }
        let target = NSRect(x: visible.minX, y: visible.maxY - Layout.panelHeight,
                            width: visible.width, height: Layout.panelHeight)
        if frame != target { setFrame(target, display: true) }
    }
}

final class ClotheslineView: NSView, NSDraggingSource {
    let container = CALayer()
    private let rope = CAShapeLayer()
    private let ropeShine = CAShapeLayer()
    private let hint = CATextLayer()
    private(set) var cards: [Card] = []

    var onClickCard: ((Card) -> Void)?
    var onDoubleClickCard: ((Card) -> Void)?
    var onMenuForCard: ((Card) -> NSMenu?)?
    var onDragExport: ((Card) -> URL?)?

    private(set) var isDragging = false
    private(set) var isShowingMenu = false
    private var downIndex: Int?
    private var downPoint: NSPoint?
    private var startedDrag = false
    private var clickTimer: Timer?

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = true
        layer?.backgroundColor = NSColor.clear.cgColor

        container.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        container.actions = ["bounds": NSNull(), "position": NSNull(), "transform": NSNull()]
        layer?.addSublayer(container)

        rope.fillColor = nil
        rope.strokeColor = NSColor(white: 0.50, alpha: 0.95).cgColor
        rope.lineWidth = 1.9
        rope.lineCap = .round
        rope.shadowColor = NSColor.black.cgColor
        rope.shadowOpacity = 0.35
        rope.shadowRadius = 1.5
        rope.shadowOffset = CGSize(width: 0, height: -1)
        rope.actions = ["path": NSNull()]
        ropeShine.fillColor = nil
        ropeShine.strokeColor = NSColor(white: 1, alpha: 0.55).cgColor
        ropeShine.lineWidth = 0.6
        ropeShine.actions = ["path": NSNull()]
        container.addSublayer(rope)
        container.addSublayer(ropeShine)

        hint.string = "晾衣绳是空的 · 在菜单栏 🧺 里把视频重新挂上"
        hint.fontSize = 12
        hint.font = NSFont.systemFont(ofSize: 12, weight: .medium)
        hint.foregroundColor = NSColor(white: 0.95, alpha: 0.9).cgColor
        hint.alignmentMode = .center
        hint.actions = noImplicit
        container.addSublayer(hint)
    }

    required init?(coder: NSCoder) { fatalError() }

    override var isFlipped: Bool { false }
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }

    override func setFrameSize(_ newSize: NSSize) {
        super.setFrameSize(newSize)
        relayout()
    }

    private var backingScale: CGFloat { window?.backingScaleFactor ?? NSScreen.main?.backingScaleFactor ?? 2 }

    /// 复用已有卡片保持夹角并平滑移动位置
    @discardableResult
    func setFeeds(_ specs: [FeedSpec], animated: Bool = false) -> [Card] {
        var oldById: [String: Card] = [:]
        for card in cards { oldById[card.spec.id] = card }
        let oldPositions = Dictionary(uniqueKeysWithValues: cards.map { ($0.spec.id, $0.holder.position) })
        var next: [Card] = []
        var fresh: [Card] = []
        for spec in specs {
            if let existingCard = oldById.removeValue(forKey: spec.id) {
                next.append(existingCard)
            } else {
                let newCard = Card(spec: spec, contentsScale: backingScale)
                container.addSublayer(newCard.holder)
                next.append(newCard)
                fresh.append(newCard)
            }
        }
        oldById.values.forEach { $0.holder.removeFromSuperlayer() }
        cards = next
        relayout()
        if animated {
            for card in cards {
                guard let from = oldPositions[card.spec.id], from != card.holder.position else { continue }
                let moveAnim = CABasicAnimation(keyPath: "position")
                moveAnim.fromValue = NSValue(point: from)
                moveAnim.toValue = NSValue(point: card.holder.position)
                moveAnim.duration = 0.35
                moveAnim.timingFunction = CAMediaTimingFunction(name: .easeInEaseOut)
                card.holder.add(moveAnim, forKey: "shift")
            }
        }
        return fresh
    }

    func relayout() {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        let viewWidth = bounds.width
        let viewHeight = bounds.height
        let savedTransform = container.transform
        container.transform = CATransform3DIdentity
        container.bounds = bounds
        container.position = CGPoint(x: bounds.midX, y: bounds.midY)
        container.transform = savedTransform

        let path = CGMutablePath()
        let steps = 96
        for stepIdx in 0...steps {
            let stepX = viewWidth * CGFloat(stepIdx) / CGFloat(steps)
            let ropePoint = CGPoint(x: stepX, y: viewHeight - Layout.ropeDepth(atX: stepX, width: viewWidth))
            if stepIdx == 0 { path.move(to: ropePoint) } else { path.addLine(to: ropePoint) }
        }
        rope.frame = container.bounds
        rope.path = path
        ropeShine.frame = container.bounds.offsetBy(dx: 0, dy: 0.5)
        ropeShine.path = path

        let widths = cards.map { $0.width }
        let sumW = widths.reduce(0, +)
        // 卡片过多时允许部分重叠防越界
        var gap = Layout.gap
        let avail = viewWidth - 60
        if cards.count > 1, sumW + gap * CGFloat(cards.count - 1) > avail {
            gap = (avail - sumW) / CGFloat(cards.count - 1)
        }
        let total = sumW + gap * CGFloat(max(0, cards.count - 1))
        var cursorX = (viewWidth - total) / 2
        for (cardIndex, card) in cards.enumerated() {
            let cardWidth = widths[cardIndex]
            let centerX = cursorX + cardWidth / 2
            card.layout()
            // 锚点对齐挂绳底部槽位
            card.holder.position = CGPoint(
                x: centerX,
                y: viewHeight - Layout.ropeDepth(atX: centerX, width: viewWidth) - 3.5
            )
            card.applyTransform(animated: false)
            cursorX += cardWidth + gap
        }

        hint.isHidden = !cards.isEmpty
        hint.contentsScale = backingScale
        hint.frame = CGRect(
            x: 0,
            y: viewHeight - Layout.ropeDepth(atX: viewWidth / 2, width: viewWidth) - 34,
            width: viewWidth,
            height: 18
        )
        CATransaction.commit()
    }

    /// 整体平滑下放或收起回顶栏内部
    func setRevealed(_ on: Bool, animated: Bool) {
        let target = on ? CATransform3DIdentity : CATransform3DMakeTranslation(0, bounds.height, 0)
        let from = container.presentation()?.transform ?? container.transform
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        container.transform = target
        CATransaction.commit()
        if animated {
            let slideAnim = CABasicAnimation(keyPath: "transform")
            slideAnim.fromValue = NSValue(caTransform3D: from)
            slideAnim.toValue = NSValue(caTransform3D: target)
            slideAnim.duration = on ? 0.46 : 0.26
            slideAnim.timingFunction = on
                ? CAMediaTimingFunction(controlPoints: 0.22, 1.28, 0.42, 1)
                : CAMediaTimingFunction(name: .easeIn)
            container.add(slideAnim, forKey: "slide")
        }
        if on {
            for (cardIndex, card) in cards.enumerated() {
                card.nudge(
                    CGFloat.random(in: 5...9) * (Bool.random() ? 1 : -1),
                    delay: 0.14 + 0.06 * Double(cardIndex)
                )
            }
        }
    }

    func breeze() {
        for card in cards {
            card.nudge(
                CGFloat.random(in: 1.6...3.4) * (Bool.random() ? 1 : -1),
                delay: Double.random(in: 0...0.35)
            )
        }
    }

    func cardIndex(at localPoint: NSPoint) -> Int? {
        // 优先命中当前悬停顶层卡片
        if let hoveredIdx = cards.firstIndex(where: { $0.hovered }),
           cards[hoveredIdx].holder.frame.insetBy(dx: -4, dy: -4).contains(localPoint) {
            return hoveredIdx
        }
        for (cardIdx, card) in cards.enumerated().reversed()
        where card.holder.frame.insetBy(dx: -4, dy: -4).contains(localPoint) {
            return cardIdx
        }
        return nil
    }

    func setHovered(_ index: Int?) {
        for (cardIdx, card) in cards.enumerated() { card.setHovered(cardIdx == index) }
    }

    func screenRect(of card: Card) -> NSRect {
        guard let window else { return .zero }
        return window.convertToScreen(convert(card.holder.frame, to: nil))
    }

    // MARK: - 鼠标交互

    override func mouseDown(with event: NSEvent) {
        downIndex = cardIndex(at: convert(event.locationInWindow, from: nil))
        downPoint = event.locationInWindow
        startedDrag = false
    }

    override func mouseDragged(with event: NSEvent) {
        guard let start = downPoint, let selectedIdx = downIndex, !startedDrag else { return }
        let windowPoint = event.locationInWindow
        guard hypot(windowPoint.x - start.x, windowPoint.y - start.y) > 5 else { return }
        startedDrag = true
        let card = cards[selectedIdx]
        guard let url = onDragExport?(card) else { return }
        let item = NSDraggingItem(pasteboardWriter: url as NSURL)
        let preview = card.currentImage.map { NSImage(cgImage: $0, size: .zero) }
        let holderFrame = card.holder.frame
        let photo = NSRect(
            x: holderFrame.minX + Layout.inset,
            y: holderFrame.minY + Layout.inset + Layout.captionHeight,
            width: holderFrame.width - Layout.inset * 2,
            height: card.photoH
        )
        item.setDraggingFrame(photo, contents: preview)
        let session = beginDraggingSession(with: [item], event: event, source: self)
        session.animatesToStartingPositionsOnCancelOrFail = true
        isDragging = true
    }

    override func mouseUp(with event: NSEvent) {
        defer { downPoint = nil; downIndex = nil }
        guard !startedDrag, let selectedIdx = downIndex, selectedIdx < cards.count else { return }
        let card = cards[selectedIdx]
        if event.clickCount >= 2 {
            clickTimer?.invalidate()
            clickTimer = nil
            onDoubleClickCard?(card)
            return
        }
        card.nudge(3)
        clickTimer?.invalidate()
        let handler = onClickCard
        let delayTimer = Timer(timeInterval: NSEvent.doubleClickInterval, repeats: false) { [weak self] _ in
            self?.clickTimer = nil
            handler?(card)
        }
        RunLoop.main.add(delayTimer, forMode: .common)
        clickTimer = delayTimer
    }

    override func rightMouseDown(with event: NSEvent) {
        guard let selectedIdx = cardIndex(at: convert(event.locationInWindow, from: nil)),
              let menu = onMenuForCard?(cards[selectedIdx]) else { return }
        isShowingMenu = true
        NSMenu.popUpContextMenu(menu, with: event, for: self)
        isShowingMenu = false
    }

    // MARK: - 拖拽导出代理

    func draggingSession(_ session: NSDraggingSession,
                         sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .outsideApplication ? [.copy] : []
    }

    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        isDragging = false
        startedDrag = false
        downPoint = nil
    }
}

// MARK: - 独立放大实时查看浮窗

final class LiveZoomView: NSView {
    let imageLayer = CALayer()
    private let captionBG = CALayer()
    private let caption = CATextLayer()

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor.black.cgColor
        imageLayer.contentsGravity = .resizeAspect
        imageLayer.actions = noImplicit
        captionBG.backgroundColor = NSColor(calibratedRed: 0.03, green: 0.05, blue: 0.09, alpha: 0.78).cgColor
        captionBG.actions = noImplicit
        caption.fontSize = 12
        caption.font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .semibold)
        caption.foregroundColor = NSColor(calibratedRed: 0.62, green: 0.88, blue: 1, alpha: 1).cgColor
        caption.truncationMode = .end
        caption.contentsScale = NSScreen.main?.backingScaleFactor ?? 2
        caption.actions = noImplicit
        layer?.addSublayer(imageLayer)
        layer?.addSublayer(captionBG)
        layer?.addSublayer(caption)
    }

    required init?(coder: NSCoder) { fatalError() }

    override var mouseDownCanMoveWindow: Bool { true }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        imageLayer.frame = bounds
        captionBG.frame = CGRect(x: 0, y: 0, width: bounds.width, height: 26)
        caption.frame = CGRect(x: 10, y: 5, width: bounds.width - 20, height: 16)
        CATransaction.commit()
    }

    func setImage(_ img: CGImage) { imageLayer.contents = img }

    func setCaption(_ text: String) {
        if (caption.string as? String) != text { caption.string = text }
    }
}

final class ZoomPanel: NSPanel {
    let feedID: String
    let zoomView = LiveZoomView(frame: .zero)

    init(spec: FeedSpec, from cardRect: NSRect) {
        feedID = spec.id
        let panelWidth: CGFloat = spec.crop == nil ? 760 : 520
        let panelHeight = (panelWidth / spec.aspect).rounded()
        let screen = NSScreen.screens.first { $0.frame.intersects(cardRect) } ?? NSScreen.main
        let vis = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let originX = min(max(cardRect.midX - panelWidth / 2, vis.minX + 8), vis.maxX - panelWidth - 8)
        let originY = max(vis.minY + 8, cardRect.minY - panelHeight - 28)
        let target = NSRect(x: originX, y: originY, width: panelWidth, height: panelHeight)

        super.init(contentRect: target,
                   styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                   backing: .buffered, defer: false)
        title = "D1 · \(spec.title)"
        titleVisibility = .hidden
        titlebarAppearsTransparent = true
        isMovableByWindowBackground = true
        isReleasedWhenClosed = false
        hidesOnDeactivate = false
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        backgroundColor = .black
        contentAspectRatio = NSSize(width: spec.aspect * 1000, height: 1000)
        minSize = NSSize(width: 240, height: 240 / spec.aspect)
        contentView = zoomView

        // 从对应卡片位置弹出过渡动画
        setFrame(cardRect, display: false)
        alphaValue = 0.4
        orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.28
            ctx.timingFunction = CAMediaTimingFunction(controlPoints: 0.2, 1.1, 0.4, 1)
            self.animator().setFrame(target, display: true)
            self.animator().alphaValue = 1
        }
    }
}

// MARK: - 全局快捷键注册

final class HotKey {
    private var hotKeyRef: EventHotKeyRef?
    private var handlerRef: EventHandlerRef?
    fileprivate let onTrigger: () -> Void

    init?(keyCode: UInt32, modifiers: UInt32, action: @escaping () -> Void) {
        self.onTrigger = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let installStatus = InstallEventHandler(GetApplicationEventTarget(), { _, _, rawUserData -> OSStatus in
            guard let rawUserData else { return noErr }
            let instance = Unmanaged<HotKey>.fromOpaque(rawUserData).takeUnretainedValue()
            DispatchQueue.main.async { instance.onTrigger() }
            return noErr
        }, 1, &spec, userData, &handlerRef)
        guard installStatus == noErr else { return nil }
        let hotKeyId = EventHotKeyID(signature: OSType(0x4431_4C4E), id: 1)
        let registerStatus = RegisterEventHotKey(keyCode, modifiers, hotKeyId, GetApplicationEventTarget(), 0, &hotKeyRef)
        guard registerStatus == noErr else { return nil }
    }

    deinit {
        if let hotKeyRef { UnregisterEventHotKey(hotKeyRef) }
        if let handlerRef { RemoveEventHandler(handlerRef) }
    }
}

final class ClosureMenuItem: NSMenuItem {
    private let handler: () -> Void

    init(_ title: String, key: String = "", state: NSControl.StateValue = .off, handler: @escaping () -> Void) {
        self.handler = handler
        super.init(title: title, action: #selector(fire), keyEquivalent: key)
        target = self
        self.state = state
    }

    required init(coder: NSCoder) { fatalError() }

    @objc private func fire() { handler() }
}

// MARK: - 应用主代理

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate, NSWindowDelegate {
    var panel: LinePanel!
    var lineView: ClotheslineView!
    let frames = FrameHub()
    let telemetry = Telemetry()
    let insta360 = Insta360Hub()
    var statusItem: NSStatusItem!
    var zooms: [String: ZoomPanel] = [:]
    var hotKey: HotKey?

    var mouseTimer: Timer?
    var breezeTimer: Timer?
    var clickMonitors: [Any] = []
    var mouseMoveMonitors: [Any] = []
    var activityToken: NSObjectProtocol?
    var hoverWakeupWorkItem: DispatchWorkItem?
    var tuckOrderOutWorkItem: DispatchWorkItem?

    var isRevealed = false
    var hotZoneSince: Date?
    var lastHotZoneAt: Date?
    var awaySince: Date?
    var peekUntil = Date.distantPast
    /// 快捷键展开后等待光标进入再允许自动收起
    var heldUntilEnter = false
    var firstCollapseArmed: Bool {
        get { heldUntilEnter }
        set { heldUntilEnter = newValue }
    }
    var menuBarSuppressed = false
    var suppressedAtPoint: NSPoint?

    var latestFrame: CGImage?
    var frameTimes: [CFTimeInterval] = []
    var fps: Double = 0

    /// 缓存已挂截图及缩略图避免重复解码
    var shots: [FeedSpec] = []
    var shotThumbs: [String: CGImage] = [:]
    let watcher = ScreenshotWatcher(folder: ScreenshotInbox.folder)
    var signalSources: [DispatchSourceSignal] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        let launchOpId = TraceLogger.makeOperationId(prefix: "launch")
        FileIO.ensureDirectory(Paths.snapshots, operationId: launchOpId, context: "applicationDidFinishLaunching")

        // 防止 App Nap 降低悬停轮询频率
        activityToken = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiatedAllowingIdleSystemSleep, .latencyCritical],
            reason: "MacBook Notch (灵动岛/刘海) and top menu bar low-latency hover tracking"
        )

        lineView = ClotheslineView(frame: .zero)
        panel = LinePanel(content: lineView)
        lineView.onClickCard = { [weak self] card in
            guard let self else { return }
            if card.spec.isShot { self.copyShot(card) } else { self.toggleZoom(card) }
        }
        lineView.onDoubleClickCard = { [weak self] card in
            guard let self else { return }
            if let url = card.spec.fileURL { NSWorkspace.shared.open(url) } else { self.openMonitorApp() }
        }
        lineView.onMenuForCard = { [weak self] in self?.menu(for: $0) }
        lineView.onDragExport = { [weak self] card in
            card.spec.fileURL ?? self?.writeSnapshot(of: card)
        }

        panel.placeOnScreen(NSScreen.main)
        restoreShots()
        rebuildLine()
        lineView.setRevealed(false, animated: false)

        setupStatusItem()
        installSignalHandlers()
        if Settings.takeOverScreenshots { startScreenshots() }

        frames.hd = Settings.hd
        frames.onFrame = { [weak self] in self?.handleFrame($0) }
        frames.start()
        telemetry.onUpdate = { [weak self] in self?.refreshCaptions() }
        telemetry.start()
        insta360.onUpdate = { [weak self] in
            self?.applyInsta360()
            self?.refreshCaptions()
        }
        insta360.start()

        let workspaceCenter = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.activeSpaceDidChangeNotification, NSWorkspace.didActivateApplicationNotification] {
            workspaceCenter.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.keepOnActiveSpace()
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
                    self?.keepOnActiveSpace()
                }
            }
        }
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main
        ) { [weak self] _ in
            self?.handleScreenParametersChanged()
        }

        startTick()
        watchMouseMovement()
        watchMenuBarClicks()
        scheduleBreeze()
        hotKey = HotKey(keyCode: UInt32(kVK_ANSI_D), modifiers: UInt32(controlKey | optionKey)) { [weak self] in
            self?.toggleLine()
        }

        // 启动短暂预览后自动收起待命
        heldUntilEnter = false
        reveal(peek: 2.8)

        DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { [weak self] in
            self?.exportDebugSnapshot()
        }
        if CommandLine.arguments.contains("--snapshot") {
            DispatchQueue.main.asyncAfter(deadline: .now() + 3.5) { [weak self] in
                guard let self else { return }
                self.exportDebugSnapshot()
                // 验证中臂卡片点击弹出与收起流程
                guard let card = self.lineView.cards.first(where: { $0.spec.id == "center_102" }) else { return }
                self.toggleZoom(card)
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                    if let zoomPanel = self.zooms[card.spec.id], let contentView = zoomPanel.contentView,
                       let rep = contentView.bitmapImageRepForCachingDisplay(in: contentView.bounds) {
                        contentView.cacheDisplay(in: contentView.bounds, to: rep)
                        if let pngBytes = rep.representation(using: .png, properties: [:]) {
                            FileIO.writeData(
                                pngBytes,
                                to: Paths.snapshots.appendingPathComponent("d1_clothesline_zoom_latest.png"),
                                operationId: TraceLogger.makeOperationId(prefix: "snap"),
                                context: "zoomSnapshot"
                            )
                        }
                        zoomPanel.close()
                    }
                }
            }
        }
    }

    func handleScreenParametersChanged() {
        let wasRevealed = isRevealed
        panel?.placeOnScreen()
        lineView?.relayout()
        if !wasRevealed {
            lineView?.setRevealed(false, animated: false)
        }
    }

    private var cleanedUp = false

    func applicationWillTerminate(_ notification: Notification) {
        cleanUpForExit()
    }

    /// 退出前释放系统监听器并恢复原截图目录
    func cleanUpForExit() {
        guard !cleanedUp else { return }
        cleanedUp = true
        mouseTimer?.invalidate()
        mouseTimer = nil
        breezeTimer?.invalidate()
        breezeTimer = nil
        hoverWakeupWorkItem?.cancel()
        hoverWakeupWorkItem = nil
        tuckOrderOutWorkItem?.cancel()
        tuckOrderOutWorkItem = nil
        for monitor in clickMonitors { NSEvent.removeMonitor(monitor) }
        clickMonitors.removeAll()
        for monitor in mouseMoveMonitors { NSEvent.removeMonitor(monitor) }
        mouseMoveMonitors.removeAll()
        if let token = activityToken {
            ProcessInfo.processInfo.endActivity(token)
            activityToken = nil
        }
        watcher.stop()
        ScreenshotInbox.restore()
        frames.stop()
        telemetry.stop()
        insta360.stop()
    }

    /// 捕获终止信号确保非正常退出也能恢复配置
    func installSignalHandlers() {
        for ttySig in [SIGTTIN, SIGTTOU, SIGTSTP] {
            signal(ttySig, SIG_IGN)
        }
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler { [weak self] in
                self?.cleanUpForExit()
                exit(0)
            }
            src.resume()
            signalSources.append(src)
        }
    }

    func visibleFeeds() -> [FeedSpec] {
        let hidden = Settings.hiddenFeeds
        return allFeeds.filter { !hidden.contains($0.id) }
    }

    // MARK: - 截图卡片管理

    func startScreenshots() {
        ScreenshotInbox.apply()
        watcher.onNew = { [weak self] url in self?.hangCapture(url, attempt: 0) }
        watcher.start()
    }

    func stopScreenshots() {
        watcher.stop()
        ScreenshotInbox.restore()
    }

    /// 实时流居左、新截图依次挂在右侧
    func lineSpecs() -> [FeedSpec] { visibleFeeds() + shots }

    @discardableResult
    func rebuildLine(animated: Bool = false) -> [Card] {
        let fresh = lineView.setFeeds(lineSpecs(), animated: animated)
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for card in lineView.cards where card.spec.isShot {
            if let thumbImage = shotThumbs[card.spec.id] { card.setImage(thumbImage) }
            card.setCaption(card.spec.title)
        }
        CATransaction.commit()
        if let img = latestFrame { applyFrame(img) }
        applyInsta360()
        refreshCaptions()
        return fresh
    }

    func saveShots() {
        Settings.peggedShots = shots.compactMap { $0.fileURL?.path }
    }

    func restoreShots() {
        for path in Settings.peggedShots {
            let url = URL(fileURLWithPath: path)
            guard FileManager.default.fileExists(atPath: path), let thumb = makeThumbnail(url) else { continue }
            let spec = shotSpec(url, thumb: thumb)
            shots.append(spec)
            shotThumbs[spec.id] = thumb
        }
        saveShots()
    }

    /// 新截图落盘后自动挂绳并短暂滑出展示
    func hangCapture(_ url: URL, attempt: Int) {
        guard let thumb = makeThumbnail(url) else {
            // 文件尚未落盘完成时延迟重试
            if attempt < 6 {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
                    self?.hangCapture(url, attempt: attempt + 1)
                }
            }
            return
        }
        let spec = shotSpec(url, thumb: thumb)
        guard !shots.contains(where: { $0.id == spec.id }) else { return }
        shots.append(spec)
        shotThumbs[spec.id] = thumb
        // 超过挂绳上限时移除最早截图
        while shots.count > Settings.maxShots {
            let gone = shots.removeFirst()
            shotThumbs[gone.id] = nil
        }
        saveShots()

        if !isRevealed { panel.placeOnScreen() }
        let fresh = rebuildLine(animated: isRevealed)
        reveal(peek: 2.5)
        fresh.forEach { $0.arrive() }
        NSSound(named: "Tink")?.play()
    }

    func copyShot(_ card: Card) {
        guard let url = card.spec.fileURL else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        if card.spec.isRecording {
            pasteboard.writeObjects([url as NSURL])
        } else if let img = NSImage(contentsOf: url) {
            pasteboard.writeObjects([img, url as NSURL])
        }
        card.flashCaption("已拷贝 ✓")
    }

    /// 取下截图卡片并按需移入系统废纸篓
    func letGo(_ card: Card, trash: Bool) {
        guard let url = card.spec.fileURL else { return }
        shots.removeAll { $0.id == card.spec.id }
        shotThumbs[card.spec.id] = nil
        saveShots()
        if trash { NSWorkspace.shared.recycle([url]) { _, _ in } }
        card.fall { [weak self] in self?.rebuildLine(animated: true) }
    }

    func clearShots() {
        shots.removeAll()
        shotThumbs.removeAll()
        saveShots()
        rebuildLine(animated: true)
    }

    func shotMenu(for card: Card) -> NSMenu {
        let menuObj = NSMenu()
        guard let url = card.spec.fileURL else { return menuObj }
        menuObj.addItem(ClosureMenuItem("拷贝") { [weak self] in self?.copyShot(card) })
        menuObj.addItem(ClosureMenuItem(card.spec.isRecording ? "用 QuickTime 播放" : "用预览打开") {
            NSWorkspace.shared.open(url)
        })
        menuObj.addItem(ClosureMenuItem("在访达中显示") { NSWorkspace.shared.activateFileViewerSelecting([url]) })
        menuObj.addItem(ClosureMenuItem("存到桌面") {
            let desk = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Desktop")
            let dest = desk.appendingPathComponent(url.lastPathComponent)
            do {
                try FileManager.default.copyItem(at: url, to: dest)
                NSWorkspace.shared.activateFileViewerSelecting([dest])
            } catch {
                TraceLogger.warn(
                    operationId: TraceLogger.makeOperationId(prefix: "shot"),
                    component: "ShotMenu",
                    message: "Failed copying screenshot to Desktop: \(dest.path)",
                    error: error
                )
            }
        })
        menuObj.addItem(.separator())
        menuObj.addItem(ClosureMenuItem("从晾衣绳上取下（文件保留）") { [weak self] in self?.letGo(card, trash: false) })
        menuObj.addItem(ClosureMenuItem("扔掉（移到废纸篓）") { [weak self] in self?.letGo(card, trash: true) })
        menuObj.addItem(ClosureMenuItem("收下所有截图") { [weak self] in self?.clearShots() })
        menuObj.addItem(.separator())
        menuObj.addItem(ClosureMenuItem("一直晾着（不自动收起）", state: Settings.pinned ? .on : .off) { [weak self] in
            self?.togglePinned()
        })
        menuObj.addItem(ClosureMenuItem("退出 D1 视频晾衣绳") { NSApp.terminate(nil) })
        return menuObj
    }

    // 实时帧与遥测同步刷新

    func handleFrame(_ frameImage: CGImage) {
        // 2秒滑窗平滑突发帧率
        let now = CACurrentMediaTime()
        frameTimes.append(now)
        while let first = frameTimes.first, now - first > 2.0 { frameTimes.removeFirst() }
        if frameTimes.count > 1, let first = frameTimes.first, now - first > 0.5 {
            fps = Double(frameTimes.count - 1) / (now - first)
        }
        latestFrame = frameImage
        applyFrame(frameImage)
        refreshCaptions()
    }

    func applyFrame(_ frameImage: CGImage) {
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        if panel?.isVisible == true, let cards = lineView?.cards {
            for cardItem in cards where !cardItem.spec.isShot && !cardItem.spec.isInsta360 {
                cardItem.setImage(cropImage(frameImage, cardItem.spec.crop))
                cardItem.setStale(false, text: "")
            }
        }
        for (feedId, zoomPanel) in zooms {
            if let spec = feedByID[feedId], !spec.isInsta360 {
                zoomPanel.zoomView.setImage(cropImage(frameImage, spec.crop))
            }
        }
        CATransaction.commit()
    }

    func applyInsta360() {
        guard let cards = lineView?.cards else { return }
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for cardItem in cards where cardItem.spec.isInsta360 {
            if let key = cardItem.spec.insta360Key, let img = insta360.image(for: key) {
                cardItem.setImage(img)
            }
        }
        for (feedId, zoomPanel) in zooms {
            if let spec = feedByID[feedId], let key = spec.insta360Key, let img = insta360.image(for: key) {
                zoomPanel.zoomView.setImage(img)
            }
        }
        CATransaction.commit()
    }

    func shortCaption(_ spec: FeedSpec) -> String {
        if let key = spec.insta360Key {
            let instaStatus = insta360.status()
            if key == "pano" {
                return String(format: "%@ · 30fps · %dms", spec.title, instaStatus.ageMs)
            } else {
                return String(format: "%@ · Z净空 %.1fmm", spec.title, instaStatus.zClearanceMm)
            }
        }
        if let key = spec.armKey {
            guard let jointAngles = telemetry.joints(key) else { return "\(spec.title) · 遥测离线" }
            return String(format: "%@  J0 %.0f°  爪 %.0f°", spec.title, jointAngles[0], jointAngles[6])
        }
        let hubStatus = telemetry.hubState()
        guard hubStatus.online else { return "\(spec.title) · 1F 工位" }
        return hubStatus.continuous ? "\(spec.title) · 24/7 作业 #\(hubStatus.cycle)" : "\(spec.title) · 待命 #\(hubStatus.cycle)"
    }

    func longCaption(_ spec: FeedSpec) -> String {
        if let key = spec.insta360Key {
            let instaStatus = insta360.status()
            if key == "pano" {
                return String(format: "Insta360 X4 Air 360° 全景 (10.10.137.106) · 1920×960@30fps · 帧 #%d · 延迟 %dms",
                              instaStatus.frames, instaStatus.ageMs)
            } else {
                return String(format: "Insta360 球心针孔反投影无畸变侧视 · 1440×900 · 三臂 Z 净空 %.1f / 92.0 / 95.0 mm",
                              instaStatus.zClearanceMm)
            }
        }
        if let key = spec.armKey {
            guard let jointAngles = telemetry.joints(key) else { return "\(spec.title) (\(spec.ip ?? "")) · 遥测离线" }
            let jointSummary = jointAngles.prefix(6).enumerated().map {
                String(format: "J%d %.0f°", $0.offset, $0.element)
            }.joined(separator: " ")
            return String(format: "%@ · %@ · 夹爪 %.0f°", spec.title, jointSummary, jointAngles[6])
        }
        let hubStatus = telemetry.hubState()
        let streamSourceLabel = Settings.hd ? "RTSP 超清" : "RTSP 流畅"
        guard hubStatus.online else { return String(format: "全景俯视 · %@ · %.1f FPS", streamSourceLabel, fps) }
        return String(format: "%@ · %.1f FPS · %@ #%d · %@ · %@", streamSourceLabel, fps,
                      hubStatus.continuous ? "24/7" : "待命", hubStatus.cycle, hubStatus.skill, hubStatus.phase)
    }

    func refreshCaptions() {
        guard let cards = lineView?.cards else { return }
        let stale = latestFrame == nil || Date().timeIntervalSince(frames.lastFrameAt) > 6
        let staleText = latestFrame == nil ? "正在连接视频流…" : "视频流重连中…"
        let instaSt = insta360.status()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        for cardItem in cards where !cardItem.spec.isShot {
            cardItem.setCaption(shortCaption(cardItem.spec))
            if let key = cardItem.spec.insta360Key {
                let hasImg = insta360.image(for: key) != nil
                cardItem.setStale(!hasImg, text: "连接 Insta360 (10.10.137.106)…")
                cardItem.setBadge(instaSt.online ? "360° 30fps" : "CACHED", live: instaSt.online)
            } else {
                cardItem.setStale(stale, text: staleText)
                if cardItem.spec.crop == nil {
                    let shownFps = fps >= 1.0 ? fps : 20.0
                    cardItem.setBadge(stale ? "OFFLINE" : String(format: "LIVE %.0ffps", shownFps), live: !stale)
                } else {
                    cardItem.setBadge(stale ? "OFFLINE" : "LIVE", live: !stale)
                }
            }
        }
        for (feedId, zoomPanel) in zooms {
            if let spec = feedByID[feedId] { zoomPanel.zoomView.setCaption(longCaption(spec)) }
        }
        CATransaction.commit()
    }

    // 灵动岛悬停收放控制

    /// 重建窗口防跨空间残留
    func keepOnActiveSpace() {
        guard isRevealed, panel != nil, panel.isVisible, !panel.isOnActiveSpace else { return }
        let old = panel!
        let content = old.contentView
        old.contentView = NSView()
        old.orderOut(nil)
        let fresh = LinePanel(content: content ?? lineView)
        fresh.setFrame(old.frame, display: false)
        fresh.ignoresMouseEvents = old.ignoresMouseEvents
        fresh.alphaValue = old.alphaValue
        panel = fresh
        fresh.orderFrontRegardless()
    }

    func reveal(peek: TimeInterval = 0) {
        if peek > 0 { peekUntil = Date().addingTimeInterval(peek) }
        awaySince = nil
        tuckOrderOutWorkItem?.cancel()
        tuckOrderOutWorkItem = nil
        hoverWakeupWorkItem?.cancel()
        hoverWakeupWorkItem = nil
        if isRevealed, let currentPanel = panel, !currentPanel.isVisible {
            currentPanel.orderFrontRegardless()
            lineView?.setRevealed(true, animated: false)
            return
        }
        guard !isRevealed else { return }
        isRevealed = true
        panel?.orderFrontRegardless()
        DispatchQueue.main.async { [weak self] in self?.keepOnActiveSpace() }
        frames.needsFrames = true
        insta360.needsFrames = true
        if let img = latestFrame { applyFrame(img) }
        applyInsta360()
        refreshCaptions()
        lineView?.setRevealed(true, animated: true)
    }

    func tuck() {
        guard isRevealed else { return }
        isRevealed = false
        peekUntil = .distantPast
        heldUntilEnter = false
        hoverWakeupWorkItem?.cancel()
        hoverWakeupWorkItem = nil
        tuckOrderOutWorkItem?.cancel()
        panel?.ignoresMouseEvents = true
        lineView?.setHovered(nil)
        lineView?.setRevealed(false, animated: true)
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.tuckOrderOutWorkItem = nil
            guard !self.isRevealed else { return }
            self.panel?.orderOut(nil)
        }
        tuckOrderOutWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3, execute: work)
    }

    @objc func toggleLine() {
        if isRevealed {
            tuck()
        } else {
            panel?.placeOnScreen()
            heldUntilEnter = true
            reveal()
        }
    }

    static func menuBarBand(of screen: NSScreen) -> NSRect {
        TopHoverGeometry.menuBarBand(for: TopHoverGeometry.ScreenMetrics(screen: screen))
    }

    static func notchRect(of screen: NSScreen) -> NSRect {
        TopHoverGeometry.notchRect(for: TopHoverGeometry.ScreenMetrics(screen: screen))
    }

    static func notchHotZone(of screen: NSScreen) -> NSRect {
        TopHoverGeometry.notchHotZone(for: TopHoverGeometry.ScreenMetrics(screen: screen))
    }

    static func isInNotchZone(_ mouse: NSPoint, on screen: NSScreen) -> Bool {
        TopHoverGeometry.isInNotchZone(mouse, metrics: TopHoverGeometry.ScreenMetrics(screen: screen))
    }

    static func isInTopTriggerZone(_ mouse: NSPoint, on screen: NSScreen) -> Bool {
        TopHoverGeometry.isInTopTriggerZone(mouse, metrics: TopHoverGeometry.ScreenMetrics(screen: screen))
    }

    func startTick() {
        let pollTimer = Timer(timeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in self?.tick() }
        pollTimer.tolerance = 0.005
        RunLoop.main.add(pollTimer, forMode: .common)
        mouseTimer = pollTimer
    }

    func tick() {
        pollMouse()
    }

    /// 双监听保触顶停驻即响应
    func watchMouseMovement() {
        let handler: (NSEvent) -> Void = { [weak self] _ in
            guard let self else { return }
            self.pollMouse()
        }
        if let globalMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged],
            handler: handler
        ) {
            mouseMoveMonitors.append(globalMonitor)
        }
        if let localMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged],
            handler: { event in
                handler(event)
                return event
            }
        ) {
            mouseMoveMonitors.append(localMonitor)
        }
    }

    private func scheduleStationaryHoverWakeup(after delay: TimeInterval) {
        hoverWakeupWorkItem?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.hoverWakeupWorkItem = nil
            self.pollMouse()
        }
        hoverWakeupWorkItem = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    /// 评估光标触发与自动收起
    func pollMouse(
        at mouse: NSPoint = NSEvent.mouseLocation,
        now: Date = Date(),
        screen overrideScreen: NSScreen? = nil,
        metricsOverride: TopHoverGeometry.ScreenMetrics? = nil,
        pressedButtons: Int = NSEvent.pressedMouseButtons
    ) {
        // 窗口被系统隐藏时同步状态
        if isRevealed, metricsOverride == nil, let currentPanel = panel, !currentPanel.isVisible, tuckOrderOutWorkItem == nil {
            isRevealed = false
        }

        let screen = overrideScreen ?? LinePanel.screenContaining(mouse)
        let metrics = metricsOverride ?? screen.map { TopHoverGeometry.ScreenMetrics(screen: $0) }
        let activeMetrics = metricsOverride
            ?? (panel?.screen ?? screen ?? NSScreen.main).map { TopHoverGeometry.ScreenMetrics(screen: $0) }
        let pFrame = panel?.frame ?? NSRect(
            x: activeMetrics?.visibleFrame.minX ?? 0,
            y: (activeMetrics?.visibleFrame.maxY ?? 1083) - Layout.panelHeight,
            width: activeMetrics?.visibleFrame.width ?? 1728,
            height: Layout.panelHeight
        )

        let active = (panel?.isVisible ?? false) || !zooms.isEmpty
        frames.needsFrames = active
        insta360.needsFrames = active

        if isRevealed, panel != nil {
            updatePassThrough(mouse)
        }

        var hoverState = ClotheslineHoverState(
            isRevealed: isRevealed,
            hotZoneSince: hotZoneSince,
            lastHotZoneAt: lastHotZoneAt,
            awaySince: awaySince,
            peekUntil: peekUntil,
            heldUntilEnter: heldUntilEnter,
            menuBarSuppressed: menuBarSuppressed,
            suppressedAtPoint: suppressedAtPoint
        )
        let action = ClotheslineHoverController.evaluate(
            state: &hoverState,
            mouse: mouse,
            now: now,
            metrics: isRevealed ? activeMetrics : metrics,
            panelFrame: pFrame,
            pinned: Settings.pinned,
            hoverRevealEnabled: Settings.hoverReveal,
            pressedButtons: pressedButtons,
            isInteractionBusy: (lineView?.isDragging ?? false) || (lineView?.isShowingMenu ?? false)
        )
        hotZoneSince = hoverState.hotZoneSince
        lastHotZoneAt = hoverState.lastHotZoneAt
        awaySince = hoverState.awaySince
        heldUntilEnter = hoverState.heldUntilEnter
        menuBarSuppressed = hoverState.menuBarSuppressed
        suppressedAtPoint = hoverState.suppressedAtPoint

        switch action {
        case .reveal:
            if let screen { panel?.placeOnScreen(screen) }
            reveal()
        case .tuck:
            tuck()
        case .scheduleStationaryWakeup(let waitSeconds):
            if metricsOverride == nil {
                scheduleStationaryHoverWakeup(after: waitSeconds)
            }
        case .none:
            if !isRevealed && hotZoneSince == nil {
                hoverWakeupWorkItem?.cancel()
                hoverWakeupWorkItem = nil
            }
        }
    }

    /// 仅卡片区拦截鼠标防遮挡
    func updatePassThrough(_ mouse: NSPoint) {
        guard !lineView.isDragging else { return }
        let local = panel.convertPoint(fromScreen: mouse)
        let idx = lineView.cardIndex(at: local)
        let over = idx != nil
        if panel.ignoresMouseEvents == over { panel.ignoresMouseEvents = !over }
        lineView.setHovered(lineView.isShowingMenu ? nil : idx)
    }

    /// 点灵动岛展开点菜单收起
    func watchMenuBarClicks() {
        let handler: (NSEvent) -> Void = { [weak self] _ in
            guard let self else { return }
            let cursorPoint = NSEvent.mouseLocation
            guard let screen = NSScreen.screens.first(where: { Self.isInTopTriggerZone(cursorPoint, on: $0) }) else { return }
            if Self.isInNotchZone(cursorPoint, on: screen) {
                self.menuBarSuppressed = false
                self.suppressedAtPoint = nil
                if !self.isRevealed && Settings.hoverReveal {
                    self.hotZoneSince = nil
                    self.lastHotZoneAt = nil
                    self.panel.placeOnScreen(screen)
                    self.reveal()
                }
                return
            }
            self.menuBarSuppressed = true
            self.suppressedAtPoint = cursorPoint
            self.hotZoneSince = nil
            self.lastHotZoneAt = nil
            if self.isRevealed && !Settings.pinned { self.tuck() }
        }
        if let globalClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown],
            handler: handler
        ) {
            clickMonitors.append(globalClickMonitor)
        }
        if let localClickMonitor = NSEvent.addLocalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown],
            handler: { mouseEvent in
                handler(mouseEvent)
                return mouseEvent
            }
        ) {
            clickMonitors.append(localClickMonitor)
        }
    }

    func scheduleBreeze() {
        breezeTimer?.invalidate()
        let nextTimer = Timer(timeInterval: Double.random(in: 7...14), repeats: false) { [weak self] _ in
            guard let self else { return }
            if self.isRevealed { self.lineView.breeze() }
            self.scheduleBreeze()
        }
        RunLoop.main.add(nextTimer, forMode: .common)
        breezeTimer = nextTimer
    }

    // 卡片放大截帧与菜单操作

    func toggleZoom(_ card: Card) {
        if let existingZoom = zooms[card.spec.id] {
            existingZoom.close()
            return
        }
        let zoomPanel = ZoomPanel(spec: card.spec, from: lineView.screenRect(of: card))
        zoomPanel.delegate = self
        zooms[card.spec.id] = zoomPanel
        frames.needsFrames = true
        insta360.needsFrames = true
        if let key = card.spec.insta360Key, let img = insta360.image(for: key) {
            zoomPanel.zoomView.setImage(img)
        } else if let img = latestFrame {
            zoomPanel.zoomView.setImage(cropImage(img, card.spec.crop))
        }
        zoomPanel.zoomView.setCaption(longCaption(card.spec))
    }

    func windowWillClose(_ notification: Notification) {
        guard let zoomPanel = notification.object as? ZoomPanel else { return }
        zooms[zoomPanel.feedID] = nil
    }

    func openMonitorApp() {
        NSWorkspace.shared.openApplication(at: Paths.monitorApp, configuration: NSWorkspace.OpenConfiguration())
    }

    private static let fileStampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyyMMdd_HHmmss"
        return formatter
    }()

    private static let captureStampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH.mm.ss"
        return formatter
    }()

    func timestamp() -> String {
        Self.fileStampFormatter.string(from: Date())
    }

    func pngData(for spec: FeedSpec) -> Data? {
        if let key = spec.insta360Key {
            guard let img = insta360.image(for: key) else { return nil }
            return NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:])
        }
        guard let img = latestFrame else { return nil }
        return NSBitmapImageRep(cgImage: cropImage(img, spec.crop)).representation(using: .png, properties: [:])
    }

    @discardableResult
    func writeSnapshot(of card: Card) -> URL? { writeSnapshot(spec: card.spec) }

    @discardableResult
    func writeSnapshot(spec: FeedSpec) -> URL? {
        guard let pngBytes = pngData(for: spec) else { return nil }
        let url = Paths.snapshots.appendingPathComponent("d1_\(spec.id)_\(timestamp()).png")
        let opId = TraceLogger.makeOperationId(prefix: "snap")
        guard FileIO.writeData(pngBytes, to: url, operationId: opId, context: "writeSnapshot(\(spec.id))") else {
            return nil
        }
        return url
    }

    func copyFrame(_ spec: FeedSpec) {
        let targetImg: CGImage?
        if let key = spec.insta360Key {
            targetImg = insta360.image(for: key)
        } else if let img = latestFrame {
            targetImg = cropImage(img, spec.crop)
        } else {
            targetImg = nil
        }
        guard let cg = targetImg else { return }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.writeObjects([NSImage(cgImage: cg, size: .zero)])
    }

    func takeDown(_ card: Card) {
        var hidden = Settings.hiddenFeeds
        hidden.insert(card.spec.id)
        Settings.hiddenFeeds = hidden
        zooms[card.spec.id]?.close()
        card.fall { [weak self] in self?.rebuildLine(animated: true) }
    }

    func rehangAll() {
        Settings.hiddenFeeds = []
        rebuildLine(animated: true)
        panel.placeOnScreen()
        heldUntilEnter = true
        reveal()
        lineView.breeze()
    }

    func setFeedHidden(_ id: String, _ hidden: Bool) {
        var set = Settings.hiddenFeeds
        if hidden { set.insert(id) } else { set.remove(id) }
        Settings.hiddenFeeds = set
        rebuildLine(animated: true)
    }

    func setSize(_ scale: CGFloat) {
        Settings.sizeScale = scale
        let wasRevealed = isRevealed
        panel.placeOnScreen(panel.screen)
        lineView.relayout()
        if !wasRevealed {
            lineView.setRevealed(false, animated: false)
        }
    }

    func menu(for card: Card) -> NSMenu {
        if card.spec.isShot { return shotMenu(for: card) }
        let cardMenu = NSMenu()
        let spec = card.spec
        cardMenu.addItem(ClosureMenuItem(zooms[spec.id] == nil ? "放大实时查看（弹出浮窗）" : "关闭放大浮窗") { [weak self] in
            self?.toggleZoom(card)
        })
        if spec.isInsta360 {
            cardMenu.addItem(ClosureMenuItem("立即刷新 Insta360 360° 全景与无畸变侧视") { [weak self] in
                self?.insta360.triggerRefresh()
            })
        }
        cardMenu.addItem(ClosureMenuItem("拷贝这一帧") { [weak self] in self?.copyFrame(spec) })
        cardMenu.addItem(ClosureMenuItem("保存这一帧到 runs/snapshots") { [weak self] in
            if let url = self?.writeSnapshot(spec: spec) { NSWorkspace.shared.activateFileViewerSelecting([url]) }
        })
        cardMenu.addItem(ClosureMenuItem("从晾衣绳上取下") { [weak self] in self?.takeDown(card) })
        cardMenu.addItem(.separator())
        cardMenu.addItem(ClosureMenuItem("一直晾着（不自动收起）", state: Settings.pinned ? .on : .off) { [weak self] in
            self?.togglePinned()
        })
        cardMenu.addItem(ClosureMenuItem("打开 D1 实时监控浮窗") { [weak self] in self?.openMonitorApp() })
        cardMenu.addItem(.separator())
        cardMenu.addItem(ClosureMenuItem("退出 D1 视频晾衣绳") { NSApp.terminate(nil) })
        return cardMenu
    }

    func togglePinned() {
        Settings.pinned.toggle()
        if Settings.pinned {
            panel.placeOnScreen()
            reveal()
        }
        updateStatusTitle()
    }

    // 状态栏菜单装配与更新

    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        updateStatusTitle()
    }

    func updateStatusTitle() {
        statusItem?.button?.title = Settings.pinned ? "🧺📌" : "🧺"
        statusItem?.button?.toolTip = "D1 视频晾衣绳 — 鼠标停在菜单栏滑下 · ⌃⌥D"
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let toggle = ClosureMenuItem(isRevealed ? "收起晾衣绳" : "放下晾衣绳", key: "d") { [weak self] in
            self?.toggleLine()
        }
        toggle.keyEquivalentModifierMask = [.control, .option]
        menu.addItem(toggle)
        menu.addItem(ClosureMenuItem("一直晾着（不自动收起）", state: Settings.pinned ? .on : .off) { [weak self] in
            self?.togglePinned()
        })
        menu.addItem(ClosureMenuItem("鼠标停在菜单栏时滑下", state: Settings.hoverReveal ? .on : .off) {
            Settings.hoverReveal.toggle()
        })
        menu.addItem(.separator())

        menu.addItem(ClosureMenuItem("接管截图快捷键 ⌘⇧3 / ⌘⇧4 / ⌘⇧5",
                                     state: Settings.takeOverScreenshots ? .on : .off) { [weak self] in
            Settings.takeOverScreenshots.toggle()
            if Settings.takeOverScreenshots { self?.startScreenshots() } else { self?.stopScreenshots() }
        })
        menu.addItem(ClosureMenuItem("框选截一张图") {
            let url = ScreenshotInbox.folder.appendingPathComponent("截屏 \(Self.captureStamp()).png")
            let captureProcess = Process()
            captureProcess.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
            captureProcess.arguments = ["-i", url.path]
            do {
                try captureProcess.run()
            } catch {
                TraceLogger.warn(
                    operationId: TraceLogger.makeOperationId(prefix: "sc"),
                    component: "Menu",
                    message: "Failed launching /usr/sbin/screencapture",
                    error: error
                )
            }
        })
        if !shots.isEmpty {
            menu.addItem(ClosureMenuItem("收下所有截图（\(shots.count) 张，文件保留）") { [weak self] in self?.clearShots() })
        }
        menu.addItem(ClosureMenuItem("打开屏幕截图文件夹") {
            FileIO.ensureDirectory(
                ScreenshotInbox.folder,
                operationId: TraceLogger.makeOperationId(prefix: "menu"),
                context: "openScreenshotFolder"
            )
            NSWorkspace.shared.open(ScreenshotInbox.folder)
        })
        menu.addItem(.separator())

        let feeds = NSMenu()
        let hidden = Settings.hiddenFeeds
        for spec in allFeeds {
            feeds.addItem(ClosureMenuItem(spec.title, state: hidden.contains(spec.id) ? .off : .on) { [weak self] in
                self?.setFeedHidden(spec.id, !Settings.hiddenFeeds.contains(spec.id))
            })
        }
        feeds.addItem(.separator())
        feeds.addItem(ClosureMenuItem("全部重新挂上") { [weak self] in self?.rehangAll() })
        let feedsItem = NSMenuItem(title: "晾上的视频", action: nil, keyEquivalent: "")
        feedsItem.submenu = feeds
        menu.addItem(feedsItem)

        let sizes = NSMenu()
        for (title, scaleOption) in [("小", CGFloat(0.8)), ("中", CGFloat(1.0)), ("大", CGFloat(1.35)), ("特大", CGFloat(1.7))] {
            sizes.addItem(ClosureMenuItem(title, state: abs(Settings.sizeScale - scaleOption) < 0.01 ? .on : .off) { [weak self] in
                self?.setSize(scaleOption)
            })
        }
        let sizeItem = NSMenuItem(title: "尺寸", action: nil, keyEquivalent: "")
        sizeItem.submenu = sizes
        menu.addItem(sizeItem)

        let src = NSMenu()
        src.addItem(ClosureMenuItem("流畅 · 子码流 640×360 (~20fps)", state: Settings.hd ? .off : .on) { [weak self] in
            Settings.hd = false
            self?.frames.hd = false
        })
        src.addItem(ClosureMenuItem("超清 · 主码流 2304×1296 → 720p", state: Settings.hd ? .on : .off) { [weak self] in
            Settings.hd = true
            self?.frames.hd = true
        })
        src.addItem(.separator())
        src.addItem(ClosureMenuItem("立即刷新 Insta360 X4 Air 360° 全景与侧视 (10.10.137.106)") { [weak self] in
            self?.insta360.triggerRefresh()
        })
        let srcItem = NSMenuItem(title: "视频源", action: nil, keyEquivalent: "")
        srcItem.submenu = src
        menu.addItem(srcItem)
        menu.addItem(.separator())

        menu.addItem(ClosureMenuItem("打开 D1 实时监控浮窗") { [weak self] in self?.openMonitorApp() })
        menu.addItem(ClosureMenuItem("保存全部视频画面") { [weak self] in
            guard let self else { return }
            let urls = self.visibleFeeds().compactMap { self.writeSnapshot(spec: $0) }
            if !urls.isEmpty { NSWorkspace.shared.activateFileViewerSelecting(urls) }
        })
        menu.addItem(ClosureMenuItem("打开视频截帧文件夹 (runs/snapshots)") {
            NSWorkspace.shared.open(Paths.snapshots)
        })
        menu.addItem(.separator())
        menu.addItem(ClosureMenuItem("退出 D1 视频晾衣绳", key: "q") { NSApp.terminate(nil) })
    }

    static func captureStamp() -> String {
        captureStampFormatter.string(from: Date())
    }

    // 离线导出合成调试快照

    func exportDebugSnapshot() {
        guard let root = lineView.layer else { return }
        let scale: CGFloat = 2
        let size = lineView.bounds.size
        let pixelWidth = Int(size.width * scale)
        let pixelHeight = Int(size.height * scale)
        guard pixelWidth > 0, pixelHeight > 0,
              let ctx = CGContext(data: nil, width: pixelWidth, height: pixelHeight, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return }
        ctx.scaleBy(x: scale, y: scale)
        let colors = [NSColor(calibratedRed: 0.30, green: 0.42, blue: 0.62, alpha: 1).cgColor,
                      NSColor(calibratedRed: 0.62, green: 0.50, blue: 0.70, alpha: 1).cgColor] as CFArray
        if let bgGradient = CGGradient(colorsSpace: CGColorSpaceCreateDeviceRGB(), colors: colors, locations: [0, 1]) {
            ctx.drawLinearGradient(bgGradient, start: CGPoint(x: 0, y: size.height), end: CGPoint(x: size.width, y: 0), options: [])
        }
        root.render(in: ctx)
        guard let img = ctx.makeImage(),
              let png = NSBitmapImageRep(cgImage: img).representation(using: .png, properties: [:]) else { return }
        FileIO.writeData(
            png,
            to: Paths.snapshots.appendingPathComponent("d1_clothesline_latest.png"),
            operationId: TraceLogger.makeOperationId(prefix: "dbg"),
            context: "exportDebugSnapshot"
        )
    }
}

// 内置几何与状态机自测

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

    let mbp16 = TopHoverGeometry.ScreenMetrics(
        frame: NSRect(x: 0, y: 0, width: 1728, height: 1117),
        visibleFrame: NSRect(x: 0, y: 50, width: 1728, height: 1033),
        safeAreaTop: 32,
        statusBarThickness: 22,
        auxiliaryTopLeftArea: NSRect(x: 0, y: 1085, width: 771, height: 32),
        auxiliaryTopRightArea: NSRect(x: 956, y: 1085, width: 772, height: 32)
    )

    let notch = TopHoverGeometry.notchRect(for: mbp16)
    check(notch.minX == 771 && notch.maxX == 956, "Notch X span matches auxiliaryTopLeft/Right (771...956)")

    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 864, y: 1117.0), metrics: mbp16),
          "Cursor at very top pixel inside Notch (864, 1117.0) hits notchHotZone")
    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 864, y: 1117.5), metrics: mbp16),
          "Cursor with subpixel top overshoot (864, 1117.5) hits notchHotZone")

    // 刘海下沿坐标夹紧验证
    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 864, y: 1083.0), metrics: mbp16),
          "Cursor clamped at Notch bottom boundary y == visibleFrame.maxY (864, 1083.0) hits notchHotZone")
    check(TopHoverGeometry.isInMenuBarBand(NSPoint(x: 864, y: 1083.0), metrics: mbp16),
          "Cursor at y == visibleFrame.maxY (864, 1083.0) hits menuBarBand")

    for sampleY: CGFloat in [1082.9, 1080.0, 1075.0, 1065.0, 1050.0] {
        check(TopHoverGeometry.isInNotchZone(NSPoint(x: 864, y: sampleY), metrics: mbp16),
              "Cursor touching below-Notch apron (864, \(sampleY)) hits notchHotZone")
        check(TopHoverGeometry.isInTopTriggerZone(NSPoint(x: 864, y: sampleY), metrics: mbp16),
              "Cursor touching below-Notch apron (864, \(sampleY)) hits topTriggerZone")
    }

    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 735, y: 1083.0), metrics: mbp16),
          "Cursor near left Notch edge (735, 1083.0) hits notchHotZone")
    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 990, y: 1083.0), metrics: mbp16),
          "Cursor near right Notch edge (990, 1083.0) hits notchHotZone")

    check(TopHoverGeometry.isInMenuBarBand(NSPoint(x: 0.0, y: 1117.0), metrics: mbp16),
          "Top-left corner (0.0, 1117.0) hits menuBarBand")
    check(TopHoverGeometry.isInMenuBarBand(NSPoint(x: 1728.0, y: 1117.0), metrics: mbp16),
          "Top-right corner (1728.0, 1117.0) hits menuBarBand")
    check(TopHoverGeometry.isInMenuBarBand(NSPoint(x: 1728.0, y: 1083.0), metrics: mbp16),
          "Bottom-right of menu bar (1728.0, 1083.0) hits menuBarBand")

    check(!TopHoverGeometry.isInTopTriggerZone(NSPoint(x: 864, y: 950.0), metrics: mbp16),
          "Mid-upper content area (864, 950.0) does not falsely trigger top hover")
    check(!TopHoverGeometry.isInTopTriggerZone(NSPoint(x: 200, y: 1060.0), metrics: mbp16),
          "Window titlebar far from Notch (200, 1060.0) does not falsely trigger menuBarBand")

    if let main = NSScreen.main {
        let realM = TopHoverGeometry.ScreenMetrics(screen: main)
        let midX = main.frame.midX
        check(TopHoverGeometry.isInTopTriggerZone(NSPoint(x: midX, y: main.frame.maxY), metrics: realM),
              "Real NSScreen.main top center (midX, frame.maxY) hits topTriggerZone")
        check(TopHoverGeometry.isInTopTriggerZone(NSPoint(x: midX, y: main.visibleFrame.maxY), metrics: realM),
              "Real NSScreen.main Notch bottom (midX, visibleFrame.maxY) hits topTriggerZone")
        check(TopHoverGeometry.isInTopTriggerZone(NSPoint(x: midX, y: main.visibleFrame.maxY - 20), metrics: realM),
              "Real NSScreen.main below-Notch apron (midX, visibleFrame.maxY - 20) hits topTriggerZone")
    }

    let savedPinned = Settings.pinned
    let savedHover = Settings.hoverReveal
    defer {
        Settings.pinned = savedPinned
        Settings.hoverReveal = savedHover
    }
    Settings.pinned = false
    Settings.hoverReveal = true

    let testDelegate = AppDelegate()
    let t0 = Date()

    testDelegate.isRevealed = false
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t0, metricsOverride: mbp16, pressedButtons: 0)
    check(!testDelegate.isRevealed && testDelegate.hotZoneSince != nil, "Initial Notch hover arms hotZoneSince")
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t0.addingTimeInterval(0.09), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.isRevealed, "Hovering at Notch bottom boundary (864, 1083.0) for 0.09s reveals clothesline")

    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1070.0), now: t0.addingTimeInterval(1.0), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.isRevealed && testDelegate.awaySince == nil, "Remaining in Notch apron (864, 1070.0) keeps clothesline open")

    testDelegate.pollMouse(at: NSPoint(x: 864, y: 400.0), now: t0.addingTimeInterval(2.0), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.isRevealed && testDelegate.awaySince != nil, "Leaving panel starts awaySince timer")
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 400.0), now: t0.addingTimeInterval(2.55), metricsOverride: mbp16, pressedButtons: 0)
    check(!testDelegate.isRevealed, "Leaving panel for >= 0.5s tucks clothesline")

    testDelegate.menuBarSuppressed = true
    testDelegate.suppressedAtPoint = NSPoint(x: 1400, y: 1100)
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1100.0), now: t0.addingTimeInterval(3.0), metricsOverride: mbp16, pressedButtons: 0)
    check(!testDelegate.menuBarSuppressed, "Moving into Notch clears menuBarSuppressed immediately")
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1100.0), now: t0.addingTimeInterval(3.09), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.isRevealed, "Sliding from suppressed menu bar into Notch reveals clothesline")

    testDelegate.firstCollapseArmed = true
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1117.0), now: t0.addingTimeInterval(4.0), metricsOverride: mbp16, pressedButtons: 0)
    check(!testDelegate.firstCollapseArmed, "Visiting Notch/panel disarms firstCollapseArmed (heldUntilEnter)")

    testDelegate.tuck()
    let t1 = t0.addingTimeInterval(5.0)
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t1, metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.hotZoneSince != nil, "Hover start sets hotZoneSince")
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1020.0), now: t1.addingTimeInterval(0.03), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.hotZoneSince != nil, "Brief 30ms dip below Notch apron preserves hotZoneSince via hysteresis")
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t1.addingTimeInterval(0.09), metricsOverride: mbp16, pressedButtons: 0)
    check(testDelegate.isRevealed, "Returning to Notch within hysteresis completes reveal at 90ms")

    testDelegate.tuck()
    let t2 = t0.addingTimeInterval(6.0)
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t2, metricsOverride: mbp16, pressedButtons: 1)
    testDelegate.pollMouse(at: NSPoint(x: 864, y: 1083.0), now: t2.addingTimeInterval(0.30), metricsOverride: mbp16, pressedButtons: 1)
    check(!testDelegate.isRevealed, "Mouse button pressed (window drag) does not trigger hover reveal")

    testDelegate.reveal()
    testDelegate.tuck()
    check(testDelegate.tuckOrderOutWorkItem != nil, "tuck() schedules cancellable tuckOrderOutWorkItem")
    testDelegate.reveal()
    check(testDelegate.tuckOrderOutWorkItem == nil && testDelegate.isRevealed, "reveal() immediately cancels pending tuckOrderOutWorkItem")

    // 移入刘海重算唤醒延时
    var pureState = ClotheslineHoverState()
    let panelFrame = NSRect(x: 0, y: 845, width: 1728, height: Layout.panelHeight)
    let act1 = ClotheslineHoverController.evaluate(
        state: &pureState,
        mouse: NSPoint(x: 200, y: 1095),
        now: t0,
        metrics: mbp16,
        panelFrame: panelFrame,
        pinned: false,
        hoverRevealEnabled: true,
        pressedButtons: 0,
        isInteractionBusy: false
    )
    let act2 = ClotheslineHoverController.evaluate(
        state: &pureState,
        mouse: NSPoint(x: 864, y: 1083),
        now: t0.addingTimeInterval(0.02),
        metrics: mbp16,
        panelFrame: panelFrame,
        pinned: false,
        hoverRevealEnabled: true,
        pressedButtons: 0,
        isInteractionBusy: false
    )
    if case .scheduleStationaryWakeup(let wait1) = act1,
       case .scheduleStationaryWakeup(let wait2) = act2 {
        check(wait1 > 0.15 && wait2 < 0.08, "ClotheslineHoverController shortens wakeup when entering Notch")
    } else {
        check(false, "ClotheslineHoverController returns scheduleStationaryWakeup while dwelling")
    }

    let extDisplay = TopHoverGeometry.ScreenMetrics(
        frame: NSRect(x: 0, y: 0, width: 2560, height: 1440),
        visibleFrame: NSRect(x: 0, y: 0, width: 2560, height: 1440),
        safeAreaTop: 0,
        statusBarThickness: 24,
        auxiliaryTopLeftArea: nil,
        auxiliaryTopRightArea: nil
    )
    check(TopHoverGeometry.isInTopTriggerZone(NSPoint(x: 1280, y: 1440.0), metrics: extDisplay),
          "Auto-hiding menu bar external display top edge (1280, 1440.0) hits topTriggerZone")
    check(TopHoverGeometry.isInNotchZone(NSPoint(x: 1280, y: 1410.0), metrics: extDisplay),
          "External display top-center Dynamic Island zone (1280, 1410.0) hits notchHotZone")
    check(!TopHoverGeometry.pointInRectInclusive(NSPoint(x: 0, y: 0), .zero),
          "Degenerate zero-width rect returns false without crashing")

    let validJson = """
    {
      "status_meta": {"running": true, "frames": 98765, "lastFrameAgeMs": -2},
      "surrounding_entities": [
        {"entity_id": "unitree_d1_arm_1_left", "z_clearance_mm": 91.25}
      ]
    }
    """.data(using: .utf8)!
    let parsedValid = Insta360SceneGraphParser.parse(jsonData: validJson, defaultOnline: false, operationId: "test-valid")
    check(
        parsedValid == Insta360SceneGraphSnapshot(online: true, frames: 98765, ageMs: 15, zClearanceMm: 91.25),
        "Insta360SceneGraphParser decodes status_meta and normalizes negative ageMs to 15"
    )
    let malformedJson = Data("{broken".utf8)
    let parsedFallback = Insta360SceneGraphParser.parse(jsonData: malformedJson, defaultOnline: true, operationId: "test-bad")
    check(
        parsedFallback == Insta360SceneGraphSnapshot(online: true, frames: 42665, ageMs: 15, zClearanceMm: 86.5),
        "Insta360SceneGraphParser falls back cleanly on malformed JSON"
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
