import AppKit
import SwiftUI

/// Existing word views keep their positions as the final line grows.
struct CaptionWordsView: View {
    let text: String
    let label: String
    let typeface: String
    let fontSize: Double
    private struct Word: Identifiable {
        let id: Int
        let text: String
        let width: CGFloat
    }
    private var font: NSFont {
        typeface == "System" ? .systemFont(ofSize: fontSize, weight: .medium) : NSFont(name: typeface, size: fontSize) ?? .systemFont(ofSize: fontSize)
    }
    var body: some View {
        GeometryReader { geometry in
            let lineHeight = ceil(font.ascender - font.descender + font.leading) + 5
            let capacity = max(1, min(3, Int(geometry.size.height / lineHeight)))
            let lines = page(width: geometry.size.width, lines: capacity)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(lines.enumerated()), id: \.offset) { _, words in
                    HStack(alignment: .firstTextBaseline, spacing: 0) {
                        ForEach(words) { word in
                            Text(word.text).font(Font(font)).fixedSize().frame(width: word.width, alignment: .leading)
                        }
                    }
                    .frame(height: lineHeight, alignment: .topLeading)
                }
                Spacer(minLength: 0)
            }
            .frame(width: geometry.size.width, height: CGFloat(capacity) * lineHeight, alignment: .topLeading)
            .frame(height: geometry.size.height, alignment: .center)
            .clipped()
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(label): \(lines.flatMap { $0 }.map(\.text).joined())")
        }
        .transaction { $0.animation = nil }
    }
    private func page(width: CGFloat, lines capacity: Int) -> [[Word]] {
        guard width > 0 else { return [] }
        var rows: [[Word]] = [[]]
        var used: CGFloat = 0
        var index = 0
        func append(_ text: String) {
            let measured = ceil((text as NSString).size(withAttributes: [.font: font]).width)
            let inkWidth = ceil((text.trimmingCharacters(in: .whitespacesAndNewlines) as NSString).size(withAttributes: [.font: font]).width)
            if used + inkWidth > width && !rows[rows.count - 1].isEmpty {
                if rows.count >= capacity { rows = [[]] } else { rows.append([]) }
                used = 0
            }
            rows[rows.count - 1].append(Word(id: index, text: text, width: measured))
            index += 1; used += measured
        }
        for token in ProgressiveCaption.words(text) {
            if (token as NSString).size(withAttributes: [.font: font]).width > width {
                // Long unbroken words still fit narrow windows and very large type.
                var part = ""
                for character in token {
                    let candidate = part + String(character)
                    if !part.isEmpty && (candidate as NSString).size(withAttributes: [.font: font]).width > width {
                        append(part); part = ""
                    }
                    part.append(character)
                }
                if !part.isEmpty { append(part) }
            } else { append(token) }
        }
        return rows
    }
}
