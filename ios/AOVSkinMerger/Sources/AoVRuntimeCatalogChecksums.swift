import Foundation

enum AoVRuntimeCatalogChecksum {
    /// Verification Databin entries in the inspected AoV 1.64.1.11 and
    /// 1.64.1.12 catalogs match the standard ZIP CRC-32 of the stored bytes.
    /// Keep the result as a sign-extended Int32 because the catalog field is Int64.
    static func verificationDatabinCRC32(_ data: Data) -> Int64 {
        var crc: UInt32 = 0xFFFF_FFFF
        for byte in data {
            crc ^= UInt32(byte)
            for _ in 0..<8 {
                crc = (crc & 1) == 1
                    ? (crc >> 1) ^ 0xEDB8_8320
                    : crc >> 1
            }
        }
        let finalized = crc ^ 0xFFFF_FFFF
        return Int64(Int32(bitPattern: finalized))
    }

    static func verifiesDatabin(_ data: Data, catalogValue: Int64) -> Bool {
        verificationDatabinCRC32(data) == catalogValue
    }
}
