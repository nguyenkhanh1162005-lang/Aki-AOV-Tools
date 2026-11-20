import Foundation
import CryptoKit
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
        if missingPaths > 0 || ambiguousPaths > 0 || unsupportedValues > 0 ||
            unityFSUncompressedSkippedCount > 0 || sampleLimitHit {
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

struct AoVRuntimeChecksumCRC64FingerprintSample: Sendable {
    let path: String
    let storedChecksum: UInt64
    let computedCRC64WE: UInt64
}

struct AoVRuntimeChecksumCRC64FingerprintFinding: Sendable {
    let title: String
    let totalCatalogRows: Int
    let evaluatedSamples: Int
    let missingPaths: Int
    let ambiguousPaths: Int
    let unsupportedValues: Int
    let sampleLimitHit: Bool
    let distinctXORMaskCount: Int
    let constantXORMask: UInt64?
    let exactCRC64WEMatches: Int
    let status: AoVRuntimeChecksumFingerprintStatus
    let examples: [String]

    var maskDescription: String {
        guard let constantXORMask else { return "Không có CRC64/WE XOR mask nhất quán" }
        return String(format: "0x%016llX", constantXORMask)
    }

    var statusDescription: String {
        switch status {
        case .insufficientSamples: return "Chưa đủ mẫu để suy luận CRC64/WE"
        case .consistentSample: return "Mẫu CRC64/WE đầy đủ có cùng XOR mask"
        case .partialConsistentSample: return "Mẫu CRC64/WE nhất quán nhưng còn thiếu dữ liệu"
        case .inconsistent: return "Không có CRC64/WE XOR mask chung"
        }
    }

    static func empty(title: String) -> AoVRuntimeChecksumCRC64FingerprintFinding {
        AoVRuntimeChecksumCRC64FingerprintFinding(
            title: title, totalCatalogRows: 0, evaluatedSamples: 0,
            missingPaths: 0, ambiguousPaths: 0, unsupportedValues: 0,
            sampleLimitHit: false, distinctXORMaskCount: 0,
            constantXORMask: nil, exactCRC64WEMatches: 0,
            status: .insufficientSamples, examples: []
        )
    }
}

struct AoVRuntimeChecksumFingerprintReport: Sendable {
    let baseRESName: String
    var baseRESFingerprint: String? = nil
    let packerAssetBundles: AoVRuntimeChecksumFingerprintFinding
    let zipVerification: AoVRuntimeChecksumFingerprintFinding
    let databinVerification: AoVRuntimeChecksumFingerprintFinding
    let packerAssetBundlesCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding
    let packerPkgBytesCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding
    let zipVerificationCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding
    let warning: String

    init(
        baseRESName: String,
        baseRESFingerprint: String? = nil,
        packerAssetBundles: AoVRuntimeChecksumFingerprintFinding,
        zipVerification: AoVRuntimeChecksumFingerprintFinding,
        databinVerification: AoVRuntimeChecksumFingerprintFinding,
        packerAssetBundlesCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding = .empty(title: "Packer AssetBundle CRC64/WE"),
        packerPkgBytesCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding = .empty(title: "Packer PKG CRC64/WE"),
        zipVerificationCRC64WE: AoVRuntimeChecksumCRC64FingerprintFinding = .empty(title: "Verification ZIP CRC64/WE"),
        warning: String
    ) {
        self.baseRESName = baseRESName
        self.baseRESFingerprint = baseRESFingerprint
        self.packerAssetBundles = packerAssetBundles
        self.zipVerification = zipVerification
        self.databinVerification = databinVerification
        self.packerAssetBundlesCRC64WE = packerAssetBundlesCRC64WE
        self.packerPkgBytesCRC64WE = packerPkgBytesCRC64WE
        self.zipVerificationCRC64WE = zipVerificationCRC64WE
        self.warning = warning
    }

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

private struct AoVRuntimeCRC64WE {
    private static let polynomial: UInt64 = 0x42F0E1EBA9EA3693
    private static let table: [UInt64] = (0..<256).map { value in
        var crc = UInt64(value) << 56
        for _ in 0..<8 {
            crc = (crc & 0x8000_0000_0000_0000) != 0
                ? (crc << 1) ^ polynomial
                : crc << 1
        }
        return crc
    }

    private var state: UInt64 = 0xFFFF_FFFF_FFFF_FFFF

    mutating func update(_ data: Data) {
        for byte in data {
            let index = Int(((state >> 56) ^ UInt64(byte)) & 0xFF)
            state = Self.table[index] ^ (state << 8)
        }
    }

    var value: UInt64 { state ^ 0xFFFF_FFFF_FFFF_FFFF }

    static func checksum(_ data: Data) -> UInt64 {
        var crc = AoVRuntimeCRC64WE()
        crc.update(data)
        return crc.value
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
    private static let maximumSamplesPerCatalog = 1_024
    private static let maximumUnityFSFingerprintEntryBytes: UInt64 = 64 * 1024 * 1024
    private static let minimumSamplesForCandidate = 8
    private static let verificationFileName = "resourceverificationinfosetall.assetbundle"
    private static let packerFileName = "resourcepackerinfosetall.assetbundle"

    static func analyze(baseRESURL: URL) throws -> AoVRuntimeChecksumFingerprintReport {
        let baseRESFingerprint = try sha256File(at: baseRESURL)
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

        // In the inspected 1.64.1 catalog, AssetBundle Packer rows use
        // crcXor = 0 as a placeholder. They are metadata-only, not a checksum
        // fingerprint. PKG rows (pkgType 2) carry CRC64/WE values.
        let packerAssetBundleRows = packerCatalog.allResourcePackInfo.filter {
            URL(fileURLWithPath: $0.pathInIFS).pathExtension.lowercased() == "assetbundle" &&
                $0.crcXor != 0
        }
        let packerFinding = try fingerprint(
            title: "Packer AssetBundle CRC32 (nonzero rows only)",
            rows: packerAssetBundleRows.map {
                AoVRuntimeVerificationEntry(pathInIFS: $0.pathInIFS, crcXor: $0.crcXor)
            },
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )
        let zipFinding = AoVRuntimeChecksumFingerprintAnalyzer.evaluate(
            title: "Verification ZIP CRC32 (not authoritative)",
            totalCatalogRows: 0,
            samples: []
        )
        let databinFinding = try fingerprint(
            title: "Verification Databin CRC32",
            rows: verificationCatalog.allDatabinVerificationInfo,
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )
        let packerCRC64WEFinding = try fingerprintCRC64WE(
            title: "Packer AssetBundle CRC64/WE (nonzero rows only)",
            rows: packerAssetBundleRows.map {
                AoVRuntimeVerificationEntry(pathInIFS: $0.pathInIFS, crcXor: $0.crcXor)
            },
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )
        let packerPkgRows = packerCatalog.allResourcePackInfo.filter {
            $0.pkgType == 2 && $0.pathInIFS.lowercased().hasSuffix(".pkg.bytes")
        }.map {
            AoVRuntimeVerificationEntry(pathInIFS: $0.pathInIFS, crcXor: $0.crcXor)
        }
        let packerPkgCRC64WEFinding = try fingerprintCRC64WE(
            title: "Packer PKG CRC64/WE",
            rows: packerPkgRows,
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )
        let zipCRC64WEFinding = try fingerprintCRC64WE(
            title: "Verification ZIP CRC64/WE",
            rows: verificationCatalog.allZipVerificationInfo,
            archive: resourceArchive,
            entriesByRuntimePath: entriesByRuntimePath
        )

        return AoVRuntimeChecksumFingerprintReport(
            baseRESName: baseRESURL.lastPathComponent,
            baseRESFingerprint: baseRESFingerprint,
            packerAssetBundles: packerFinding,
            zipVerification: zipFinding,
            databinVerification: databinFinding,
            packerAssetBundlesCRC64WE: packerCRC64WEFinding,
            packerPkgBytesCRC64WE: packerPkgCRC64WEFinding,
            zipVerificationCRC64WE: zipCRC64WEFinding,
            warning: "Theo catalog AoV 1.64.1.13: Verification ZIP dùng CRC64/WE trên byte tài nguyên; Verification Databin dùng CRC32 IEEE trên byte .bytes gốc; Packer PKG (pkgType 2) dùng CRC64/WE. Packer AssetBundle có crcXor=0 trong mẫu này và được giữ như metadata, không suy ra checksum từ nó. Cần kiểm chứng trên RES đích và trong game trước khi xuất RES cuối."
        )
    }

    /// Hashes an archive incrementally so reusing a fingerprint never depends
    /// on the filename alone and large RES files are not loaded into memory.
    static func sha256File(at url: URL) throws -> String {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        var hasher = SHA256()
        while let chunk = try handle.read(upToCount: 1024 * 1024), !chunk.isEmpty {
            hasher.update(data: chunk)
        }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func crc32(_ data: Data) -> UInt32 {
        AoVRuntimeCRC32.checksum(data)
    }

    static func crc64WE(_ data: Data) -> UInt64 {
        AoVRuntimeCRC64WE.checksum(data)
    }

    static func evaluateCRC64WE(
        title: String,
        totalCatalogRows: Int,
        samples: [AoVRuntimeChecksumCRC64FingerprintSample],
        missingPaths: Int = 0,
        ambiguousPaths: Int = 0,
        unsupportedValues: Int = 0,
        sampleLimitHit: Bool = false
    ) -> AoVRuntimeChecksumCRC64FingerprintFinding {
        let masks = samples.map { $0.storedChecksum ^ $0.computedCRC64WE }
        let distinctMasks = Set(masks)
        let constantMask = samples.count >= minimumSamplesForCandidate && distinctMasks.count == 1
            ? distinctMasks.first : nil
        let exactMatches = samples.filter { $0.storedChecksum == $0.computedCRC64WE }.count

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
            let mask = sample.storedChecksum ^ sample.computedCRC64WE
            return "\(sample.path) · stored 0x\(String(sample.storedChecksum, radix: 16, uppercase: true)) · CRC64/WE 0x\(String(sample.computedCRC64WE, radix: 16, uppercase: true)) · XOR 0x\(String(mask, radix: 16, uppercase: true))"
        }

        return AoVRuntimeChecksumCRC64FingerprintFinding(
            title: title,
            totalCatalogRows: totalCatalogRows,
            evaluatedSamples: samples.count,
            missingPaths: missingPaths,
            ambiguousPaths: ambiguousPaths,
            unsupportedValues: unsupportedValues,
            sampleLimitHit: sampleLimitHit,
            distinctXORMaskCount: distinctMasks.count,
            constantXORMask: constantMask,
            exactCRC64WEMatches: exactMatches,
            status: status,
            examples: examples
        )
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

    private static func fingerprintCRC64WE(
        title: String,
        rows: [AoVRuntimeVerificationEntry],
        archive: Archive,
        entriesByRuntimePath: [String: [Entry]]
    ) throws -> AoVRuntimeChecksumCRC64FingerprintFinding {
        let distinctRows = Dictionary(grouping: rows, by: \.pathInIFS)
        let orderedPaths = distinctRows.keys.sorted()
        let sampledPaths = Array(orderedPaths.prefix(maximumSamplesPerCatalog))
        let sampleLimitHit = orderedPaths.count > sampledPaths.count
        var samples: [AoVRuntimeChecksumCRC64FingerprintSample] = []
        var missingPaths = 0
        var ambiguousPaths = 0
        var unsupportedValues = 0

        for path in sampledPaths {
            guard let catalogRows = distinctRows[path], catalogRows.count == 1,
                  let row = catalogRows.first else {
                ambiguousPaths += 1
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
            let computed = try crc64WE(of: entry, in: archive)
            samples.append(
                AoVRuntimeChecksumCRC64FingerprintSample(
                    path: path,
                    storedChecksum: UInt64(bitPattern: row.crcXor),
                    computedCRC64WE: computed
                )
            )
        }

        return evaluateCRC64WE(
            title: title,
            totalCatalogRows: rows.count,
            samples: samples,
            missingPaths: missingPaths,
            ambiguousPaths: ambiguousPaths,
            unsupportedValues: unsupportedValues,
            sampleLimitHit: sampleLimitHit
        )
    }

    private static func crc64WE(of entry: Entry, in archive: Archive) throws -> UInt64 {
        var crc = AoVRuntimeCRC64WE()
        _ = try archive.extract(entry, bufferSize: 64 * 1024) { chunk in
            crc.update(chunk)
        }
        return crc.value
    }

    private static func fingerprint(
        title: String,
        rows: [AoVRuntimeVerificationEntry],
        archive: Archive,
        entriesByRuntimePath: [String: [Entry]]
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
            guard let catalogRows = distinctRows[path], catalogRows.count == 1,
                  let row = catalogRows.first else {
                ambiguousPaths += 1
                continue
            }
            // Databin CRC32 values are stored in a signed Int64 field;
            // compare the low 32 bits instead of rejecting sign-extended values.
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
