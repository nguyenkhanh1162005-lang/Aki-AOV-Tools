import Foundation
import CommonCrypto
import libzstd

enum AoVBytesDecodeError: LocalizedError, Equatable {
    case notAoVWrapped
    case unsupportedAESFlag(UInt8)
    case invalidHeader
    case resourceTooLarge
    case invalidCiphertextLength
    case decryptFailed
    case sizeMismatch(expected: UInt32, actual: Int)
    case dictionaryMissing
    case dictionaryInvalid
    case invalidZstdFrame
    case zstdDecodeFailed(String)

    var errorDescription: String? {
        switch self {
        case .notAoVWrapped:
            return "Tài nguyên không có wrapper AoV 22 4A đã nhận diện."
        case .unsupportedAESFlag(let flag):
            return String(format: "Flag AES AoV chưa hỗ trợ: 0x%02X.", flag)
        case .invalidHeader:
            return "Header AES AoV bị thiếu hoặc không hợp lệ."
        case .resourceTooLarge:
            return "Payload sau giải mã vượt giới hạn 32 MiB của bộ giải mã iOS."
        case .invalidCiphertextLength:
            return "Độ dài ciphertext AES không hợp lệ."
        case .decryptFailed:
            return "AES-CBC giải mã thất bại; tên tài nguyên hoặc dữ liệu có thể sai."
        case .sizeMismatch(let expected, let actual):
            return "Kích thước giải mã không khớp header: \(actual) != \(expected)."
        case .dictionaryMissing:
            return "Không tìm thấy AoV Zstandard dictionary trong app bundle."
        case .dictionaryInvalid:
            return "AoV Zstandard dictionary không hợp lệ."
        case .invalidZstdFrame:
            return "Payload AES không chứa frame Zstandard hợp lệ."
        case .zstdDecodeFailed(let reason):
            return "Giải nén AoV Zstandard thất bại: \(reason)"
        }
    }
}

struct AoVDecodedResource: Sendable {
    let data: Data
    let wrapperMode: String
}

private final class AoVBytesDecoderBundleToken {}

enum AoVBytesDecoder {
    private static let aesHeader: [UInt8] = [0x22, 0x4A, 0x67]
    private static let zstdHeader: [UInt8] = [0x22, 0x4A, 0x00, 0xEF]
    private static let zstdMagic: [UInt8] = [0x28, 0xB5, 0x2F, 0xFD]
    private static let aesBaseKey: [UInt8] = [
        0x99, 0x64, 0xB1, 0xB0, 0x6B, 0x03, 0x8D, 0x7F,
        0xB7, 0x7D, 0xB6, 0xA7, 0x54, 0x90, 0x8B, 0x73
    ]
    private static let maximumDecodedBytes: UInt32 = 32 * 1024 * 1024

    static func isAESResource(_ data: Data) -> Bool {
        data.count >= 4 && Array(data.prefix(3)) == aesHeader
    }

    static func isAoVWrappedResource(_ data: Data) -> Bool {
        isAESResource(data) || (data.count >= 4 && Array(data.prefix(4)) == zstdHeader)
    }

    static func decodeResource(_ data: Data, resourceName: String) throws -> AoVDecodedResource {
        if data.count >= 4 && Array(data.prefix(4)) == zstdHeader {
            guard data.count >= 12, Array(data[8..<12]) == zstdMagic else {
                throw AoVBytesDecodeError.invalidZstdFrame
            }
            let expected = UInt32(data[4]) |
                (UInt32(data[5]) << 8) |
                (UInt32(data[6]) << 16) |
                (UInt32(data[7]) << 24)
            guard expected <= maximumDecodedBytes else {
                throw AoVBytesDecodeError.resourceTooLarge
            }
            let frame = Data(data.dropFirst(8))
            let decoded = try decompressZstdWithAoVDictionary(frame, expectedSize: expected)
            return AoVDecodedResource(data: decoded, wrapperMode: "zstd")
        }

        guard isAESResource(data) else { throw AoVBytesDecodeError.notAoVWrapped }
        guard data.count >= 24 else { throw AoVBytesDecodeError.invalidHeader }

        let flag = data[3]
        guard flag == 0x00 || flag == 0xEF else {
            throw AoVBytesDecodeError.unsupportedAESFlag(flag)
        }

        let expectedSize = UInt32(data[4]) |
            (UInt32(data[5]) << 8) |
            (UInt32(data[6]) << 16) |
            (UInt32(data[7]) << 24)
        guard expectedSize <= maximumDecodedBytes else {
            throw AoVBytesDecodeError.resourceTooLarge
        }

        let ciphertext = Data(data.dropFirst(8))
        guard !ciphertext.isEmpty, ciphertext.count % kCCBlockSizeAES128 == 0 else {
            throw AoVBytesDecodeError.invalidCiphertextLength
        }

        let key = derivedKey(resourceName: resourceName)
        let iv = Data(repeating: 0, count: kCCBlockSizeAES128)
        var decrypted = Data(count: ciphertext.count + kCCBlockSizeAES128)
        var decryptedLength = 0

        let status: CCCryptorStatus = key.withUnsafeBytes { keyBytes in
            iv.withUnsafeBytes { ivBytes in
                ciphertext.withUnsafeBytes { inputBytes in
                    decrypted.withUnsafeMutableBytes { outputBytes in
                        CCCrypt(
                            CCOperation(kCCDecrypt),
                            CCAlgorithm(kCCAlgorithmAES),
                            CCOptions(kCCOptionPKCS7Padding),
                            keyBytes.baseAddress,
                            key.count,
                            ivBytes.baseAddress,
                            inputBytes.baseAddress,
                            ciphertext.count,
                            outputBytes.baseAddress,
                            outputBytes.count,
                            &decryptedLength
                        )
                    }
                }
            }
        }

        guard status == kCCSuccess else { throw AoVBytesDecodeError.decryptFailed }
        decrypted.removeSubrange(decryptedLength..<decrypted.count)

        if flag == 0x00 {
            guard decrypted.count == Int(expectedSize) else {
                throw AoVBytesDecodeError.sizeMismatch(expected: expectedSize, actual: decrypted.count)
            }
            return AoVDecodedResource(data: decrypted, wrapperMode: "aes")
        }

        guard decrypted.count >= zstdMagic.count,
              Array(decrypted.prefix(zstdMagic.count)) == zstdMagic else {
            throw AoVBytesDecodeError.invalidZstdFrame
        }
        let decoded = try decompressZstdWithAoVDictionary(decrypted, expectedSize: expectedSize)
        return AoVDecodedResource(data: decoded, wrapperMode: "aes+zstd")
    }

    private static func decompressZstdWithAoVDictionary(
        _ frame: Data,
        expectedSize: UInt32
    ) throws -> Data {
        guard expectedSize <= maximumDecodedBytes else {
            throw AoVBytesDecodeError.resourceTooLarge
        }
        let bundleURL = Bundle.main.url(forResource: "AoVZstdDictionary", withExtension: "base64")
            ?? Bundle(for: AoVBytesDecoderBundleToken.self).url(forResource: "AoVZstdDictionary", withExtension: "base64")
        guard let bundleURL else { throw AoVBytesDecodeError.dictionaryMissing }

        let base64 = try String(contentsOf: bundleURL, encoding: .utf8)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard let dictionary = Data(base64Encoded: base64),
              dictionary.count >= 8 else {
            throw AoVBytesDecodeError.dictionaryInvalid
        }

        let dictionaryHandle: OpaquePointer? = dictionary.withUnsafeBytes { dictionaryBytes in
            guard let baseAddress = dictionaryBytes.baseAddress else { return nil }
            return ZSTD_createDDict(baseAddress, dictionary.count)
        }
        guard let dictionaryHandle else { throw AoVBytesDecodeError.dictionaryInvalid }
        defer { _ = ZSTD_freeDDict(dictionaryHandle) }

        guard let context = ZSTD_createDCtx() else {
            throw AoVBytesDecodeError.zstdDecodeFailed("Không tạo được decompression context.")
        }
        defer { _ = ZSTD_freeDCtx(context) }

        var output = Data(count: Int(expectedSize))
        let decodedSize: Int = frame.withUnsafeBytes { inputBytes in
            output.withUnsafeMutableBytes { outputBytes in
                guard let inputBase = inputBytes.baseAddress,
                      let outputBase = outputBytes.baseAddress else {
                    return -1
                }
                return ZSTD_decompress_usingDDict(
                    context,
                    outputBase,
                    outputBytes.count,
                    inputBase,
                    frame.count,
                    dictionaryHandle
                )
            }
        }

        if ZSTD_isError(decodedSize) != 0 {
            let errorName = ZSTD_getErrorName(decodedSize).map {
                String(cString: $0)
            } ?? "Zstandard error"
            throw AoVBytesDecodeError.zstdDecodeFailed(errorName)
        }
        guard decodedSize == Int(expectedSize) else {
            throw AoVBytesDecodeError.sizeMismatch(expected: expectedSize, actual: decodedSize)
        }
        return output
    }

    private static func derivedKey(resourceName: String) -> Data {
        let normalized = resourceName.replacingOccurrences(of: "\\", with: "/")
        let filename = (normalized as NSString).lastPathComponent
        let stem = (filename as NSString).deletingPathExtension.uppercased()

        var hash: UInt32 = 0
        for scalar in stem.unicodeScalars {
            hash = hash &* 31 &+ UInt32(scalar.value)
        }
        let hashBytes: [UInt8] = [
            UInt8(hash & 0xFF),
            UInt8((hash >> 8) & 0xFF),
            UInt8((hash >> 16) & 0xFF),
            UInt8((hash >> 24) & 0xFF)
        ]
        let key = aesBaseKey.enumerated().map { index, byte in
            byte ^ hashBytes[index & 3]
        }
        return Data(key)
    }
}
