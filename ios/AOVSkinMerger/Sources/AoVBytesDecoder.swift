import Foundation
import CommonCrypto

enum AoVBytesDecodeError: LocalizedError {
    case notAES
    case aesZstdUnsupported
    case unsupportedAESFlag(UInt8)
    case invalidHeader
    case resourceTooLarge
    case invalidCiphertextLength
    case decryptFailed
    case sizeMismatch(expected: UInt32, actual: Int)

    var errorDescription: String? {
        switch self {
        case .notAES:
            return "Tài nguyên không có header AES AoV 22 4A 67."
        case .aesZstdUnsupported:
            return "Tài nguyên dùng AES+Zstandard; bản iOS này chưa có Zstandard dictionary decoder."
        case .unsupportedAESFlag(let flag):
            return String(format: "Flag AES AoV chưa hỗ trợ: 0x%02X.", flag)
        case .invalidHeader:
            return "Header AES AoV bị thiếu hoặc không hợp lệ."
        case .resourceTooLarge:
            return "Payload sau giải mã vượt giới hạn 32 MiB của bộ giải mã iOS thử nghiệm."
        case .invalidCiphertextLength:
            return "Độ dài ciphertext AES không hợp lệ."
        case .decryptFailed:
            return "AES-CBC giải mã thất bại; tên tài nguyên hoặc dữ liệu có thể sai."
        case .sizeMismatch(let expected, let actual):
            return "Kích thước giải mã không khớp header: \(actual) != \(expected)."
        }
    }
}

enum AoVBytesDecoder {
    private static let aesHeader: [UInt8] = [0x22, 0x4A, 0x67]
    private static let aesBaseKey: [UInt8] = [
        0x99, 0x64, 0xB1, 0xB0, 0x6B, 0x03, 0x8D, 0x7F,
        0xB7, 0x7D, 0xB6, 0xA7, 0x54, 0x90, 0x8B, 0x73
    ]
    private static let maximumDecodedBytes: UInt32 = 32 * 1024 * 1024

    static func isAESResource(_ data: Data) -> Bool {
        data.count >= 4 && Array(data.prefix(3)) == aesHeader
    }

    static func decodeAESOnly(_ data: Data, resourceName: String) throws -> Data {
        guard isAESResource(data) else { throw AoVBytesDecodeError.notAES }
        guard data.count >= 24 else { throw AoVBytesDecodeError.invalidHeader }

        let flag = data[3]
        if flag == 0xEF { throw AoVBytesDecodeError.aesZstdUnsupported }
        guard flag == 0x00 else { throw AoVBytesDecodeError.unsupportedAESFlag(flag) }

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
        var output = Data(count: ciphertext.count + kCCBlockSizeAES128)
        var outputLength = 0

        let status: CCCryptorStatus = key.withUnsafeBytes { keyBytes in
            iv.withUnsafeBytes { ivBytes in
                ciphertext.withUnsafeBytes { inputBytes in
                    output.withUnsafeMutableBytes { outputBytes in
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
                            &outputLength
                        )
                    }
                }
            }
        }

        guard status == kCCSuccess else { throw AoVBytesDecodeError.decryptFailed }
        output.removeSubrange(outputLength..<output.count)
        guard output.count == Int(expectedSize) else {
            throw AoVBytesDecodeError.sizeMismatch(expected: expectedSize, actual: output.count)
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
