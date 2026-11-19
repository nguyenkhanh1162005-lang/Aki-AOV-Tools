import Foundation

enum AKIOperationHistoryOutcome: String, Codable {
    case success
    case partial
    case failed

    var title: String {
        switch self {
        case .success: return "Thành công"
        case .partial: return "Một phần"
        case .failed: return "Thất bại"
        }
    }

    var systemImage: String {
        switch self {
        case .success: return "checkmark.circle.fill"
        case .partial: return "exclamationmark.circle.fill"
        case .failed: return "xmark.octagon.fill"
        }
    }
}

struct AKIOperationHistoryEntry: Identifiable, Codable, Equatable {
    let id: UUID
    let date: Date
    let operation: String
    let summary: String
    let outcome: AKIOperationHistoryOutcome

    init(
        id: UUID = UUID(),
        date: Date = Date(),
        operation: String,
        summary: String,
        outcome: AKIOperationHistoryOutcome
    ) {
        self.id = id
        self.date = date
        self.operation = operation
        self.summary = summary
        self.outcome = outcome
    }
}

struct AKIOperationHistoryStore {
    static let maximumEntries = 100

    let fileURL: URL

    func load() throws -> [AKIOperationHistoryEntry] {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return []
        }
        let data = try Data(contentsOf: fileURL)
        let entries = try JSONDecoder().decode([AKIOperationHistoryEntry].self, from: data)
        return Array(entries.prefix(Self.maximumEntries))
    }

    func append(_ entry: AKIOperationHistoryEntry) throws {
        var entries = try load()
        entries.insert(entry, at: 0)
        let boundedEntries = Array(entries.prefix(Self.maximumEntries))

        try FileManager.default.createDirectory(
            at: fileURL.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let data = try JSONEncoder().encode(boundedEntries)
        try data.write(to: fileURL, options: .atomic)
    }

    func removeAll() throws {
        guard FileManager.default.fileExists(atPath: fileURL.path) else {
            return
        }
        try FileManager.default.removeItem(at: fileURL)
    }
}
