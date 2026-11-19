import Foundation
import XCTest
@testable import AOVSkinMerger

final class AKIOperationHistoryStoreTests: XCTestCase {
    private func makeStore() throws -> (AKIOperationHistoryStore, URL) {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKIOperationHistoryTests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return (
            AKIOperationHistoryStore(fileURL: directory.appendingPathComponent("history.json")),
            directory
        )
    }

    func testMissingHistoryStartsEmpty() throws {
        let (store, directory) = try makeStore()
        defer { try? FileManager.default.removeItem(at: directory) }

        XCTAssertTrue(try store.load().isEmpty)
    }

    func testAppendPersistsEntryAcrossStoreInstances() throws {
        let (store, directory) = try makeStore()
        defer { try? FileManager.default.removeItem(at: directory) }
        let entry = AKIOperationHistoryEntry(
            date: Date(timeIntervalSince1970: 1_800_000_000),
            operation: "Ghép MOD",
            summary: "output.zip",
            outcome: .success
        )

        try store.append(entry)
        let reopenedStore = AKIOperationHistoryStore(fileURL: store.fileURL)

        XCTAssertEqual(try reopenedStore.load(), [entry])
    }

    func testHistoryIsBoundedToNewestOneHundredEntries() throws {
        let (store, directory) = try makeStore()
        defer { try? FileManager.default.removeItem(at: directory) }

        for index in 0..<120 {
            try store.append(AKIOperationHistoryEntry(
                date: Date(timeIntervalSince1970: TimeInterval(index)),
                operation: "Test",
                summary: "\(index)",
                outcome: .success
            ))
        }

        let entries = try store.load()
        XCTAssertEqual(entries.count, AKIOperationHistoryStore.maximumEntries)
        XCTAssertEqual(entries.first?.summary, "119")
        XCTAssertEqual(entries.last?.summary, "20")
    }

    func testRemoveAllClearsPersistedHistory() throws {
        let (store, directory) = try makeStore()
        defer { try? FileManager.default.removeItem(at: directory) }

        try store.append(AKIOperationHistoryEntry(
            operation: "Kiểm tra ZIP",
            summary: "1 gói",
            outcome: .success
        ))
        try store.removeAll()

        XCTAssertTrue(try store.load().isEmpty)
    }
}
