import Foundation
import SwiftUI
import CryptoKit
import AppKit

/// All optional external traffic is gated here; inference remains local.
@MainActor
final class CommunitySupport: ObservableObject {
    static let base = URL(string: "https://h.gskrd.me/apps/captionflow")!
    static let version = ReleaseInfo.current.version
    static let releaseSequence = ReleaseInfo.current.sequence
    static let publicKey = "yTp27s/gWvh5/qBk/I6/IcszfL0oPFANbj1ffM+QDiQ="
    @Published var enabled = UserDefaults.standard.bool(forKey: "communityConsent")
    @Published var update: [String: Any]?
    @Published var status = ""
    private var session = URLSession(configuration: .ephemeral)
    var hasChoice: Bool { UserDefaults.standard.object(forKey: "communityConsent") != nil }
    private var token: String? {
        get { UserDefaults.standard.string(forKey: "communityToken") }
        set { UserDefaults.standard.set(newValue, forKey: "communityToken") }
    }
    func choose(_ value: Bool) {
        status = ""
        enabled = value; UserDefaults.standard.set(value, forKey: "communityConsent")
        if value { Task { await sync() } }
        else { update = nil; session.invalidateAndCancel(); session = URLSession(configuration: .ephemeral) }
    }
    func request(_ path: String, method: String = "GET", body: [String: Any]? = nil, retryAuthentication: Bool = true) async throws -> Data {
        guard enabled else { throw URLError(.notConnectedToInternet) }
        var request = URLRequest(url: Self.base.appendingPathComponent(path))
        request.httpMethod = method; request.timeoutInterval = 15
        request.setValue("CaptionFlow/\(Self.version)", forHTTPHeaderField: "User-Agent")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body { request.httpBody = try JSONSerialization.data(withJSONObject: body) }
        let (data, response) = try await session.data(for: request)
        if (response as? HTTPURLResponse)?.statusCode == 401, retryAuthentication,
           ["v1/reports", "v1/diagnostics"].contains(path), enabled {
            token = nil
            try await enroll()
            return try await self.request(path, method: method, body: body, retryAuthentication: false)
        }
        guard let response = response as? HTTPURLResponse, (200..<300).contains(response.statusCode) else { throw URLError(.badServerResponse) }
        return data
    }
    func enroll() async throws {
        if token != nil { return }
        let data = try await request("v1/enroll", method: "POST", body: ["consent_version": 1])
        if enabled, let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] { token = result["token"] as? String }
    }
    func diagnostic(_ phase: String, error: String? = nil) async {
        guard enabled else { return }
        do {
            try await enroll()
            var body: [String: Any] = ["schema": 1, "app_version": Self.version, "phase": phase,
                "hardware": ["os": "Darwin", "os_release": ProcessInfo.processInfo.operatingSystemVersionString,
                             "logical_cores": ProcessInfo.processInfo.processorCount,
                             "ram_bytes": ProcessInfo.processInfo.physicalMemory]]
            if let error { body["error_fingerprint"] = SHA256.hash(data: Data(error.utf8)).map { String(format: "%02x", $0) }.joined() }
            _ = try await request("v1/diagnostics", method: "POST", body: body)
        } catch { if enabled { status = "Feedback service unavailable; local use continues." } }
    }
    func sync() async {
        await diagnostic("startup")
        guard enabled, Bundle.main.bundleIdentifier != "dev.captionflow.gpui" else { return }
        do {
            let data = try await request("v1/releases/macos-aarch64")
            guard let envelope = try JSONSerialization.jsonObject(with: data, options: .fragmentsAllowed) as? [String: String],
                  let payload = Data(base64Encoded: envelope["payload"] ?? ""),
                  let signature = Data(base64Encoded: envelope["signature"] ?? ""),
                  let keyData = Data(base64Encoded: Self.publicKey),
                  let release = try JSONSerialization.jsonObject(with: payload) as? [String: Any] else { return }
            let key = try Curve25519.Signing.PublicKey(rawRepresentation: keyData)
            guard key.isValidSignature(signature, for: payload), release["platform"] as? String == "macos-aarch64",
                  let sequence = release["sequence"] as? Int, sequence > Self.releaseSequence,
                  sequence >= UserDefaults.standard.integer(forKey: "highestRelease"),
                  let expires = release["expires"] as? Double, expires > Date().timeIntervalSince1970,
                  let urlString = release["url"] as? String, let url = URL(string: urlString),
                  url.scheme == "https", url.host == Self.base.host, url.path.hasPrefix(Self.base.path + "/releases/") else { return }
            if enabled { update = release }
        } catch { if enabled { status = "Update check unavailable; local use continues." } }
    }
    func revoke() async {
        let oldToken = enabled ? token : nil
        token = nil; choose(false)
        status = "Local-only mode enabled."
        guard let oldToken else { return }
        // Disable and cancel ordinary traffic immediately. This final request
        // only revokes the token captured before consent was withdrawn.
        let deletion = URLSession(configuration: .ephemeral)
        defer { deletion.finishTasksAndInvalidate() }
        do {
            var request = URLRequest(url: Self.base.appendingPathComponent("v1/self"))
            request.httpMethod = "DELETE"; request.timeoutInterval = 15
            request.setValue("Bearer \(oldToken)", forHTTPHeaderField: "Authorization")
            let (_, response) = try await deletion.data(for: request)
            guard let code = (response as? HTTPURLResponse)?.statusCode, (200..<300).contains(code) || code == 401 else { throw URLError(.badServerResponse) }
        }
        catch { if !enabled { status = "Offline now. Server deletion could not be confirmed; reports expire after 30 days." } }
    }
    func downloadUpdate() async {
        guard enabled, let release = update, let urlString = release["url"] as? String,
              let url = URL(string: urlString), let hash = release["sha256"] as? String else { return }
        do {
            let (temporary, response) = try await session.download(from: url)
            guard enabled, (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.badServerResponse) }
            let handle = try FileHandle(forReadingFrom: temporary); defer { try? handle.close() }
            var digest = SHA256()
            while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty { digest.update(data: chunk) }
            guard digest.finalize().map({ String(format: "%02x", $0) }).joined() == hash else { throw URLError(.cannotDecodeContentData) }
            let directory = WorkerBridge.support.appendingPathComponent("updates")
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let destination = directory.appendingPathComponent(UUID().uuidString + ".zip")
            try FileManager.default.moveItem(at: temporary, to: destination)
            UserDefaults.standard.set(release["sequence"], forKey: "highestRelease")
            update = nil; NSWorkspace.shared.activateFileViewerSelecting([destination])
            status = "Verified update downloaded. Extract and replace the app after quitting."
        } catch { if enabled { status = "Update could not be verified or downloaded." } }
    }
}
