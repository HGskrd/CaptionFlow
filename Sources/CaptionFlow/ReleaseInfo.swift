import Foundation

struct ReleaseInfo: Decodable {
    let version: String
    let sequence: Int
    static let current: ReleaseInfo = {
        do {
            let data = try Data(contentsOf: WorkerBridge.resources.appendingPathComponent("release.json"))
            return try JSONDecoder().decode(ReleaseInfo.self, from: data)
        } catch { fatalError("Missing or invalid bundled release metadata: \(error)") }
    }()
}
