import Foundation
import ZIPFoundation

enum AoVRuntimeChecksumFingerprintStatus: String, Sendable {
    case insufficientSamples
    case consistentSample
    case partialConsistentSample
    case inconsistent
}

struct AoVRuntimeChecksumFingerprintSample: Sendable {
    let path: String
    let storedChecksum: Int64
    let computedCRC32: UInt32
    let computedUnityFSUncompressedCRC32: UInt32?

    init(
        path: String,
        storedChecksum: Int64,
        computedCRC32: UInt32,
        computedUnityFSUncompressedCRC32: UInt32? = nil
    ) {
        self.path = path
        self.storedChecksum = storedChecksum
        self.computedCRC32 = computedCRC32
        self.computedUnityFSUncompressedCRC32 = computedUnityFSUncompressedCRC32
    }
}

struct AoVRuntimeChecksumFingerprintFinding: Sendable {
    let title: String
    let totalCatalogRows: Int
    let evaluatedSamples: Int
    let missingPaths: Int
    let ambiguousPaths: Int
    let unsupportedValues: Int
    let sampleLimitHit: Bool
    let distinctXORMaskCount: Int
    let constantXORMask: UInt32?
    let exactCRC32Matches: Int
    let unityFSUncompressedEvaluatedSamples: Int
    let distinctUnityFSUncompressedXORMaskCount: Int
    let constantUnityFSUncompressedXORMask: UInt32?
    let exactUnityFSUncompressedCRC32Matches: Int
    let unityFSUncompressedSkippedCount: Int
    let status: AoVRuntimeChecksumFingerprintStatus
    let examples: [String]

    var maskDescription: String {
        guard let constantXORMask else { return "Không có mask nhất quán" }
        return String(format: "0x%08X", constantXORMask)
    }

    var unityFSUncompressedMaskDescription: String {
        guard let constantUnityFSUncompressedXORMask else {
            return "Chưa có mask UnityFS giải nén nhất quán"
        }
        return String(format: "0x%08X", constantUnityFSUncompressedXORMask)
    }

    var unityFSUncompressedStatus: AoVRuntimeChecksumFingerprintStatus {
        if unityFSUncompressedEvaluatedSamples < 8 {
            return .insufficientSamples
        }
        if distinctUnityFSUncompressedXORMaskCount > 1 {
            return .inconsistent
        }
        if unityFSUncompressedSkippedCount > 0 || sampleLimitHit {
            return .partialConsistentSample
        }
        return .consistentSample
    }

    var statusDescription: String {
        switch status {
        case .insufficientSamples:
            return "Chưa đủ mẫu để suy luận"
        case .consistentSample:
            return "Mẫu đầy đủ có cùng XOR mask"
        case .partialConsistentSample:
            return "Mẫu hiện có nhất quán nhưng còn thiếu dữ liệu"
        case .inconsistent:
            return "Không có XOR mask chung"
        }
    }
}

struct AoVRuntimeChecksumFingerprintReport: Sendable {
    let baseRESName: String
    let packerAssetBundles: AoVRuntimeChecksumFingerprintFinding
    let zipVerification: AoVRuntimeChecksumFingerprintFinding
    let databinVerification: AoVRuntimeChecksumFingerprintFinding
    let warning: String

    var requiresExternalValidation: Bool { true }
}

enum AoVRuntimeChecksumFingerprintError: LocalizedError {
    case invalidArchive(String)
    case ambiguousResourcesArchive
    case missingVerificationCatalog
    case ambiguousVerificationCatalog
    case invalidVerificationCatalog(String)
    case missingPackerCatalog
    case ambiguousPackerCatalog
    case invalidPackerCatalog(String)
    case unsafePath(String)
    case duplicatePath(String)
    case tooManyEntries

    var errorDescription: String? {
        switch self {
        case .invalidArchive(let name):
            return "Không mở được RES ZIP: \(name)."
        case .ambiguousResourcesArchive:
            return "RES có nhiều Resources.zip; không thể tự chọn gói."
        case .missingVerificationCatalog:
            return "Không tìm thấy ResourceVerificationInfoSetAll.assetbundle."
        case .ambiguousVerificationCatalog:
            return "RES có nhiều Verification catalog cùng tên."
        case .invalidVerificationCatalog(let reason):
            return "Không đọc được Verification catalog: \(reason)"
        case .missingPackerCatalog:
            return "Không tìm thấy ResourcePackerInfoSetAll.assetbundle."
        case .ambiguousPackerCatalog:
            return "RES có nhiều Packer catalog cùng tên."
        case .invalidPackerCatalog(let reason):
            return "Không đọc được Packer catalog: \(reason)"
        case .unsafePath(let path):
            return "RES có đường dẫn không an toàn: \(path)."
        case .duplicatePath(let path):
            return "RES có đường dẫn trùng sau chuẩn hóa: \(path)."
        case .tooManyEntries:
            return "RES có quá nhiều file để khảo sát checksum an toàn."
        }
    }
}

private struct AoVRuntimeCRC32 {
    private static let table: [UInt32] = (0..<256).map { value in
        var crc = UInt32(value)
        for _ in 0..<8 {
            crc = (crc & 1) == 1 ? (crc >> 1) ^ 0xEDB88320 : crc >> 1
        }
        return crc
    }

    private var state: UInt32 = 0xFFFFFFFF

    mutating func update(_ data: Data) {
        for byte in data {
            let index = Int((state ^ UInt32(byte)) & 0xFF)
            state = Self.table[index] ^ (state >> 8)
        }
    }

    var value: UInt32 { state ^ 0xFFFFFFFF }

    static func checksum(_ data: Data) -> UInt32 {
        var crc = AoVRuntimeCRC32()
        crc.update(data)
        return crc.value
    }
}

enum AoVRuntimeChecksumFingerprintAnalyzer {
    private static let maximumEntries = 20_000
    private static let maximumSingleEntryBytes: UInt64 = 512 * 1024 * 1024
    private static let maximumSamplesPerCatalog = 256
    private static let maximumUnityFSFingerprintEntryBytes: UInt64 = 64 * 1024 * 1024
    private static let minimumSamplesForCandidate = 8
    private static let verificationFileName = "resourceverificationinfosetall.assetbundle"
    private static let packerFileName = "resourcepackerinfosetall.assetbundle"

    static func analyze(baseRESURL: URL) throws -> AoVRuntimeChecksumFingerprintReport {
        let tempRoot = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-AOV-CHECKSUM-FINGERPRINT-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tempRoot, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: tempRoot) }

        let outerArchive: Archive
        do {
            outerArchive = try Archive(url: baseRESURL, accessMode: .read)
        } catch {
            throw AoVRuntimeChecksumFingerprintError.invalidArchive(baseRESURL.lastPathComponent)
        }
        let nestedResources = outerArchive.filter {
            $0.type == .file &&
                ($0.path as NSString).lastPathComponent.caseInsensitiveCompare("Resources.zip") == .orderedSame
        }
        guard nestedResources.count <= 1 else {
            throw AoVRuntimeChecksumFingerprintError.ambiguousResourcesArchive
        }

        let resourceArchive: Archive
        if let nested = nestedResources.first {
            guard nested.uncompressedSize <= maximumSingleEntryBytes else {
                throw AoVRuntimeChecksumFingerprintError.invalidArchive("Resources.zip vượt 512 MiB")
            }
            let nestedURL = tempRoot.appendingPathComponent("RES-inner.zip")
            do {
                _ = try outerArchive.extract(nested, to: nestedURL, bufferSize: 64 * 1024)
                resourceArchive = try Archive(url: nestedURL, accessMode: .read)
            } catch {
                throw AoVRuntimeChecksumFingerprintError.invalidArchive("Resources.zip")
            }
        } else {
            resourceArchive = outerArchive
        }

        let entries = try validatedEntries(resourceArchive)
        let catalogMatches = entries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(verificationFileName) == .orderedSame
        }
        guard !catalogMatches.isEmpty else {
            throw AoVRuntimeChecksumFingerprintError.missingVerificationCatalog
        }
        guard catalogMatches.count == 1, let catalogEntry = catalogMatches.first else {
            throw AoVRuntimeChecksumFingerprintError.ambiguousVerificationCatalog
        }
        let verificationData = try readEntry(resourceArchive, catalogEntry)
        let verificationTarget: AoVUnityFSRuntimeCatalogTarget
        do {
            verificationTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locateVerificationCatalog(in: verificationData)
        } catch {
            throw AoVRuntimeChecksumFingerprintError.invalidVerificationCatalog(error.localizedDescription)
        }
        guard case .verification(let verificationCatalog) = verificationTarget.catalog else {
            throw AoVRuntimeChecksumFingerprintError.invalidVerificationCatalog(
                "File được nhận diện không phải Verification catalog."
            )
        }

        let packerMatches = entries.filter {
            ($0.path as NSString).lastPathComponent.caseInsensitiveCompare(packerFileName) == .orderedSame
        }
        guard !packerMatches.isEmpty else {
            throw AoVRuntimeChecksumFingerprintError.missingPackerCatalog
        }
        guard packerMatches.count == 1, let packerEntry = packerMatches.first else {
            throw AoVRuntimeChecksumFingerprintError.ambiguousPackerCatalog
        }
        let packerData = try readEntry(resourceArchive, packerEntry)
        let packerTarget: AoVUnityFSRuntimeCatalogTarget
        do {
            packerTarget = try AoVUnityFSRuntimeCatalogMergeExecutor.locatePackerCatalog(in: packerData)
        } catch {
            throw AoVRuntimeChecksumFingerprintError.invalidPackerCatalog(error.localizedDescription)
        }
        guard case .packer(let packerCatalog) = packerTarget.catalog else {
            throw AoVRuntimeChecksumFingerprintError.invalidPackerCatalog(
                "File được nhận diện không phải Packer catalog."
            )
        }

        var entriesByRuntimePath: [String: [Entry]] = [:]
        for entry in entries {
            let runtimePath = runtimePath(forArchivePath: entry.path)
            guard !runtimePath.isEmpty else { continue }
            entriesByRuntimePath[runtimePath, default: []].append(entry)
        }

        let packerFinding = try fingerprint(
            title: "Packer AssetBundle",
            rows: packerCatalog.allResourcePackInfo.map {
                AoVRuntimeVerificationEntry(pathInIFS: $0.pathInIFS, crcXor: $0.crcXor)
            },
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath,
            assetBundleRowsOnly: true
        )
        let zipFinding = try fingerprint(
            title: "Verification ZIP",
            rows: verificationCatalog.allZipVerificationInfo,
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )
        let databinFinding = try fingerprint(
            title: "Verification Databin",
            rows: verificationCatalog.allDatabinVerificationInfo,
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )

        return AoVRuntimeChecksumFingerprintReport(
            baseRESName: baseRESURL.lastPathComponent,
            packerAssetBundles: packerFinding,
            zipVerification: zipFinding,
            databinVerification: databinFinding,
            warning: "Có hai phép đo tách biệt: CRC32 IEEE trên byte ZIP thô và, với AssetBundle UnityFS nhỏ hơn 64 MiB, CRC32 trên luồng block đã giải nén. XOR mask chỉ là giả thuyết từ mẫu RES; không tự động dùng cho payload đã sửa và chưa chứng minh tương thích trong game."
        )
    }

    static func crc32(_ data: Data) -> UInt32 {
        AoVRuntimeCRC32.checksum(data)
    }

    static func unityFSUncompressedCRC32(_ data: Data) throws -> UInt32 {
        var crc = AoVRuntimeCRC32()
        try AoVUnityFSParser.forEachUncompressedBlock(
            data,
            maximumExpandedBytes: 256 * 1024 * 1024
        ) { block in
            crc.update(block)
        }
        return crc.value
    }

    static func evaluate(
        title: String,
        totalCatalogRows: Int,
        samples: [AoVRuntimeChecksumFingerprintSample],
        missingPaths: Int = 0,
        ambiguousPaths: Int = 0,
        unsupportedValues: Int = 0,
        sampleLimitHit: Bool = false,
        unityFSUncompressedSkippedCount: Int = 0
    ) -> AoVRuntimeChecksumFingerprintFinding {
        let masks = samples.map {
            UInt32(truncatingIfNeeded: $0.storedChecksum) ^ $0.computedCRC32
        }
        let distinctMasks = Set(masks)
        let constantMask = samples.count >= minimumSamplesForCandidate && distinctMasks.count == 1
            ? distinctMasks.first : nil
        let exactMatches = samples.filter {
            UInt32(truncatingIfNeeded: $0.storedChecksum) == $0.computedCRC32
        }.count

        let unityFSSamples = samples.compactMap { sample -> (stored: UInt32, computed: UInt32)? in
            guard let computed = sample.computedUnityFSUncompressedCRC32 else { return nil }
            return (UInt32(truncatingIfNeeded: sample.storedChecksum), computed)
        }
        let unityFSMasks = unityFSSamples.map { $0.stored ^ $0.computed }
        let distinctUnityFSMasks = Set(unityFSMasks)
        let constantUnityFSMask = unityFSSamples.count >= minimumSamplesForCandidate &&
            distinctUnityFSMasks.count == 1 ? distinctUnityFSMasks.first : nil
        let exactUnityFSMatches = unityFSSamples.filter { $0.stored == $0.computed }.count

        let status: AoVRuntimeChecksumFingerprintStatus
        if samples.count < minimumSamplesForCandidate {
            status = .insufficientSamples
        } else if distinctMasks.count > 1 {
            status = .inconsistent
        } else if missingPaths > 0 || ambiguousPaths > 0 || unsupportedValues > 0 || sampleLimitHit {
            status = .partialConsistentSample
        } else {
            status = .consistentSample
        }

        let examples = samples.prefix(6).map { sample in
            let stored = UInt32(truncatingIfNeeded: sample.storedChecksum)
            let mask = stored ^ sample.computedCRC32
            return "\(sample.path) · stored 0x\(String(stored, radix: 16, uppercase: true)) · CRC32 0x\(String(sample.computedCRC32, radix: 16, uppercase: true)) · XOR 0x\(String(mask, radix: 16, uppercase: true))"
        }

        return AoVRuntimeChecksumFingerprintFinding(
            title: title,
            totalCatalogRows: totalCatalogRows,
            evaluatedSamples: samples.count,
            missingPaths: missingPaths,
            ambiguousPaths: ambiguousPaths,
            unsupportedValues: unsupportedValues,
            sampleLimitHit: sampleLimitHit,
            distinctXORMaskCount: distinctMasks.count,
            constantXORMask: constantMask,
            exactCRC32Matches: exactMatches,
            unityFSUncompressedEvaluatedSamples: unityFSSamples.count,
            distinctUnityFSUncompressedXORMaskCount: distinctUnityFSMasks.count,
            constantUnityFSUncompressedXORMask: constantUnityFSMask,
            exactUnityFSUncompressedCRC32Matches: exactUnityFSMatches,
            unityFSUncompressedSkippedCount: unityFSUncompressedSkippedCount,
            status: status,
            examples: examples
        )
    }

    private static func fingerprint(
        title: String,
        rows: [AoVRuntimeVerificationEntry],
        archive: Archive,
        entriesByRuntimePath: [String: [Entry]],
        assetBundleRowsOnly: Bool = false
    ) throws -> AoVRuntimeChecksumFingerprintFinding {
        let distinctRows = Dictionary(grouping: rows, by: \.pathInIFS)
        let orderedPaths = distinctRows.keys.sorted()
        let sampledPaths = Array(orderedPaths.prefix(maximumSamplesPerCatalog))
        let sampleLimitHit = orderedPaths.count > sampledPaths.count
        var samples: [AoVRuntimeChecksumFingerprintSample] = []
        var missingPaths = 0
        var ambiguousPaths = 0
        var unsupportedValues = 0
        var unityFSUncompressedSkipped = 0

        for path in sampledPaths {
            if assetBundleRowsOnly &&
                URL(fileURLWithPath: path).pathExtension.lowercased() != "assetbundle" {
                unsupportedValues += 1
                continue
            }
            guard let catalogRows = distinctRows[path], catalogRows.count == 1,
                  let row = catalogRows.first else {
                ambiguousPaths += 1
                continue
            }
            guard row.crcXor >= 0, row.crcXor <= Int64(UInt32.max) else {
                unsupportedValues += 1
                continue
            }
            let normalizedIFSPath = normalizedPath(path)
            guard let matches = entriesByRuntimePath[normalizedIFSPath], !matches.isEmpty else {
                missingPaths += 1
                continue
            }
            guard matches.count == 1, let entry = matches.first else {
                ambiguousPaths += 1
                continue
            }
            guard entry.uncompressedSize <= maximumSingleEntryBytes else {
                unsupportedValues += 1
                continue
            }
            let computed = try crc32(of: entry, in: archive)
            var unityFSUncompressedCRC: UInt32?
            if URL(fileURLWithPath: entry.path).pathExtension.lowercased() == "assetbundle" {
                if entry.uncompressedSize <= maximumUnityFSFingerprintEntryBytes {
                    do {
                        let bundleData = try readEntry(archive, entry)
                        unityFSUncompressedCRC = try unityFSUncompressedCRC32(bundleData)
                    } catch {
                        unityFSUncompressedSkipped += 1
                    }
                } else {
                    unityFSUncompressedSkipped += 1
                }
            }
            samples.append(
                AoVRuntimeChecksumFingerprintSample(
                    path: path,
                    storedChecksum: row.crcXor,
                    computedCRC32: computed,
                    computedUnityFSUncompressedCRC32: unityFSUncompressedCRC
                )
            )
        }

        return evaluate(
            title: title,
            totalCatalogRows: rows.count,
            samples: samples,
            missingPaths: missingPaths,
            ambiguousPaths: ambiguousPaths,
            unsupportedValues: unsupportedValues,
            sampleLimitHit: sampleLimitHit,
            unityFSUncompressedSkippedCount: unityFSUncompressedSkipped
        )
    }

    private static func crc32(of entry: Entry, in archive: Archive) throws -> UInt32 {
        var crc = AoVRuntimeCRC32()
        _ = try archive.extract(entry, bufferSize: 64 * 1024) { chunk in
            crc.update(chunk)
        }
        return crc.value
    }

    private static func readEntry(_ archive: Archive, _ entry: Entry) throws -> Data {
        guard entry.uncompressedSize <= maximumSingleEntryBytes else {
            throw AoVRuntimeChecksumFingerprintError.invalidArchive(entry.path)
        }
        var data = Data()
        data.reserveCapacity(Int(entry.uncompressedSize))
        _ = try archive.extract(entry, bufferSize: 64 * 1024) { chunk in
            data.append(chunk)
        }
        guard UInt64(data.count) == entry.uncompressedSize else {
            throw AoVRuntimeChecksumFingerprintError.invalidArchive(
                "Kích thước entry \(entry.path) không khớp."
            )
        }
        return data
    }

    private static func validatedEntries(_ archive: Archive) throws -> [Entry] {
        var entries: [Entry] = []
        var seen = Set<String>()
        for entry in archive {
            if entry.type == .directory { continue }
            guard entry.type == .file else {
                throw AoVRuntimeChecksumFingerprintError.unsafePath(entry.path)
            }
            guard entries.count < maximumEntries else {
                throw AoVRuntimeChecksumFingerprintError.tooManyEntries
            }
            let path = normalizedPath(entry.path)
            let components = path.split(separator: "/", omittingEmptySubsequences: false)
            guard !path.isEmpty, !path.hasPrefix("/"), !path.hasPrefix("~"),
                  !components.contains(""), !components.contains("."), !components.contains(".."),
                  !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw AoVRuntimeChecksumFingerprintError.unsafePath(entry.path)
            }
            guard seen.insert(path).inserted else {
                throw AoVRuntimeChecksumFingerprintError.duplicatePath(path)
            }
            entries.append(entry)
        }
        return entries
    }

    private static func runtimePath(forArchivePath raw: String) -> String {
        var components = normalizedPath(raw).split(separator: "/").map(String.init)
        if components.first?.caseInsensitiveCompare("Resources") == .orderedSame {
            components.removeFirst()
        }
        if let first = components.first,
           first.range(of: #"^\d+(?:\.\d+){1,3}$"#, options: .regularExpression) != nil {
            components.removeFirst()
        }
        return normalizedPath(components.joined(separator: "/"))
    }

    private static func normalizedPath(_ value: String) -> String {
        value.replacingOccurrences(of: "\\", with: "/")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
