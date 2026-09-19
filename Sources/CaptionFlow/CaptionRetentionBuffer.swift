import Foundation

struct CaptionRetentionBuffer {
    private struct Entry {
        let text: String
        let appearedAt: Date
    }

    private var entries: [Entry] = []
    var isEmpty: Bool { entries.isEmpty }

    mutating func append(_ text: String, at date: Date = Date()) {
        guard !text.isEmpty else { return }
        entries.append(Entry(text: text, appearedAt: date))
        if entries.count > 500 { entries.removeFirst(entries.count - 500) }
    }

    mutating func text(mode: CaptionFlowMode, wordLimit: Int, timeLimit: Double, now: Date = Date()) -> String {
        switch mode {
        case .sentence:
            return rendered
        case .words:
            if entries.count > wordLimit { entries.removeFirst(entries.count - wordLimit) }
        case .time:
            let cutoff = now.addingTimeInterval(-timeLimit)
            entries.removeAll { $0.appearedAt <= cutoff }
        }
        return rendered
    }

    private var rendered: String {
        entries.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
