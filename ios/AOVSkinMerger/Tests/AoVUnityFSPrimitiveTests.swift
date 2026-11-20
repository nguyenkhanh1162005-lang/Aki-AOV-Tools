import XCTest
import Foundation
@testable import AOVSkinMerger

final class AoVUnityFSPrimitiveTests: XCTestCase {
    private func data(hex:String)->Data {
        let chars=Array(hex)
        return Data(stride(from:0,to:chars.count,by:2).map {
            UInt8(String(chars[$0..<($0+2)]),radix:16)!
        })
    }

    func testSM4StandardBlockVectorAndCBCDecrypt() throws {
        let key=data(hex:"0123456789abcdeffedcba9876543210")
        let expected=data(hex:"681edf34d206965e86b3e94f536e4246")
        let encrypted=try AoVSM4.cryptCBC(key,key:key,iv:Data(repeating:0,count:16),decrypt:false)
        XCTAssertEqual(encrypted,expected)
        XCTAssertEqual(try AoVSM4.cryptCBC(encrypted,key:key,iv:Data(repeating:0,count:16),decrypt:true),key)
    }

    func testSM4CBCLeavesAoVPartialSuffixUnencrypted() throws {
        let key=data(hex:"0123456789abcdeffedcba9876543210")
        let iv=data(hex:"00112233445566778899aabbccddeeff")
        let source=data(hex:"00112233445566778899aabbccddeeffa1b2c3")
        let encrypted=try AoVSM4.cryptCBC(source,key:key,iv:iv,decrypt:false)
        XCTAssertEqual(encrypted.suffix(3),source.suffix(3))
        XCTAssertEqual(try AoVSM4.cryptCBC(encrypted,key:key,iv:iv,decrypt:true),source)
    }

    func testLZ4LiteralOnlyBlock() throws {
        let decoded=try AoVLZ4Block.decompress(Data([0x50,0x68,0x65,0x6C,0x6C,0x6F]),expectedSize:5)
        XCTAssertEqual(String(data:decoded,encoding:.utf8),"hello")
    }

    func testLZ4OverlappingMatchBlock() throws {
        let decoded=try AoVLZ4Block.decompress(Data([0x40,0x57,0x58,0x59,0x5A,0x04,0x00]),expectedSize:8)
        XCTAssertEqual(String(data:decoded,encoding:.utf8),"WXYZWXYZ")
    }

    func testLZ4RejectsInvalidBackReference() {
        XCTAssertThrowsError(try AoVLZ4Block.decompress(Data([0x00,0x01,0x00]),expectedSize:4))
    }

    func testLZ4RejectsOutputSizeMismatch() {
        XCTAssertThrowsError(try AoVLZ4Block.decompress(Data([0x50,0x68,0x65,0x6C,0x6C,0x6F]),expectedSize:6))
    }
}
