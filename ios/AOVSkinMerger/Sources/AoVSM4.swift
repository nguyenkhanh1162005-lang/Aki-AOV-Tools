import Foundation

enum AoVSM4Error: LocalizedError {
    case invalidKey, invalidIV
    var errorDescription: String? {
        switch self {
        case .invalidKey: return "Khóa SM4 phải có đúng 16 byte."
        case .invalidIV: return "IV SM4 phải có đúng 16 byte."
        }
    }
}

enum AoVSM4 {
    private static let sboxHex = "d690e9feccc13db716b614c228fb2c052b679a762abe04c3aa441326498606999c4250f491ef987a33540b43edcfac62e4b31ca9c908e89580df94fa758f3fa64707a7fcf37317ba83593c19e6854fa8686b81b27164da8bf8eb0f4b70569d351e240e5e6358d1a225227c3b01217887d40046579fd327524c3602e7a0c4c89eeabf8ad240c738b5a3f7f2cef96115a1e0ae5da49b341a55ad933230f58cb1e31df6e22e8266ca60c02923ab0d534e6fd5db3745defd8e2f03ff6a726d6c5b518d1baf92bbddbc7f11d95c411f105ad80ac13188a5cd7bbd2d74d012b8e5b4b08969974a0c96777e65b9f109c56ec68418f07dec3adc4d2079ee5f3ed7cb3948"
    private static let sbox: [UInt8] = {
        let chars = Array(sboxHex)
        return stride(from: 0, to: chars.count, by: 2).map {
            UInt8(String(chars[$0..<($0 + 2)]), radix: 16)!
        }
    }()
    private static let fk: [UInt32] = [0xA3B1BAC6,0x56AA3350,0x677D9197,0xB27022DC]
    private static let ck: [UInt32] = [
        0x00070E15,0x1C232A31,0x383F464D,0x545B6269,0x70777E85,0x8C939AA1,0xA8AFB6BD,0xC4CBD2D9,
        0xE0E7EEF5,0xFC030A11,0x181F262D,0x343B4249,0x50575E65,0x6C737A81,0x888F969D,0xA4ABB2B9,
        0xC0C7CED5,0xDCE3EAF1,0xF8FF060D,0x141B2229,0x30373E45,0x4C535A61,0x686F767D,0x848B9299,
        0xA0A7AEB5,0xBCC3CAD1,0xD8DFE6ED,0xF4FB0209,0x10171E25,0x2C333A41,0x484F565D,0x646B7279
    ]

    @inline(__always) private static func rotl(_ v: UInt32, _ n: UInt32) -> UInt32 {
        (v << n) | (v >> (32 - n))
    }
    @inline(__always) private static func tau(_ v: UInt32) -> UInt32 {
        let a=UInt32(sbox[Int((v>>24)&255)]), b=UInt32(sbox[Int((v>>16)&255)])
        let c=UInt32(sbox[Int((v>>8)&255)]), d=UInt32(sbox[Int(v&255)])
        return (a<<24)|(b<<16)|(c<<8)|d
    }
    private static func word(_ b:[UInt8],_ i:Int)->UInt32 {
        (UInt32(b[i])<<24)|(UInt32(b[i+1])<<16)|(UInt32(b[i+2])<<8)|UInt32(b[i+3])
    }
    private static func put(_ v:UInt32,_ b:inout [UInt8],_ i:Int) {
        b[i]=UInt8((v>>24)&255); b[i+1]=UInt8((v>>16)&255); b[i+2]=UInt8((v>>8)&255); b[i+3]=UInt8(v&255)
    }
    private static func keys(_ key:[UInt8])->[UInt32] {
        var k=[UInt32](repeating:0,count:36), r=[UInt32](repeating:0,count:32)
        for i in 0..<4 { k[i]=word(key,i*4)^fk[i] }
        for i in 0..<32 {
            let b=tau(k[i+1]^k[i+2]^k[i+3]^ck[i])
            k[i+4]=k[i]^b^rotl(b,13)^rotl(b,23); r[i]=k[i+4]
        }
        return r
    }
    private static func cryptBlock(_ block:[UInt8],_ rk:[UInt32],_ decrypt:Bool)->[UInt8] {
        var x=[UInt32](repeating:0,count:36)
        for i in 0..<4 { x[i]=word(block,i*4) }
        for i in 0..<32 {
            let b=tau(x[i+1]^x[i+2]^x[i+3]^rk[decrypt ? 31-i : i])
            x[i+4]=x[i]^b^rotl(b,2)^rotl(b,10)^rotl(b,18)^rotl(b,24)
        }
        var o=[UInt8](repeating:0,count:16)
        put(x[35],&o,0); put(x[34],&o,4); put(x[33],&o,8); put(x[32],&o,12)
        return o
    }

    /// AoV storage uses SM4-CBC on complete 16-byte blocks and keeps a short suffix unchanged.
    static func cryptCBC(_ data:Data,key:Data,iv:Data,decrypt:Bool)throws->Data {
        guard key.count==16 else { throw AoVSM4Error.invalidKey }
        guard iv.count==16 else { throw AoVSM4Error.invalidIV }
        let input=[UInt8](data), length=(input.count/16)*16, rk=keys([UInt8](key))
        var previous=[UInt8](iv), output=[UInt8](); output.reserveCapacity(input.count)
        if length>0 {
            for offset in stride(from:0,to:length,by:16) {
                let block=Array(input[offset..<(offset+16)])
                if decrypt {
                    let clear=cryptBlock(block,rk,true)
                    for i in 0..<16 { output.append(clear[i]^previous[i]) }
                    previous=block
                } else {
                    var mixed=[UInt8](repeating:0,count:16)
                    for i in 0..<16 { mixed[i]=block[i]^previous[i] }
                    let cipher=cryptBlock(mixed,rk,false); output.append(contentsOf:cipher); previous=cipher
                }
            }
        }
        if length<input.count { output.append(contentsOf:input[length...]) }
        return Data(output)
    }
}

enum AoVLZ4Error: LocalizedError {
    case truncated, invalidBackReference, outputTooLarge, sizeMismatch(Int,Int)
    var errorDescription: String? {
        switch self {
        case .truncated: return "Block LZ4 bị cắt cụt."
        case .invalidBackReference: return "Back-reference LZ4 không hợp lệ."
        case .outputTooLarge: return "Block LZ4 vượt giới hạn giải nén."
        case .sizeMismatch(let expected,let actual): return "LZ4 giải ra \(actual) byte thay vì \(expected)."
        }
    }
}

enum AoVLZ4Block {
    static func decompress(_ data:Data,expectedSize:Int,maximumSize:Int=512*1024*1024)throws->Data {
        guard expectedSize>=0,expectedSize<=maximumSize else { throw AoVLZ4Error.outputTooLarge }
        let input=[UInt8](data); var cursor=0, output=[UInt8](); output.reserveCapacity(expectedSize)
        func length(_ initial:Int,_ cursor:inout Int)throws->Int {
            var value=initial
            if initial==15 {
                while true {
                    guard cursor<input.count else { throw AoVLZ4Error.truncated }
                    let extra=Int(input[cursor]); cursor+=1
                    let (next,overflow)=value.addingReportingOverflow(extra)
                    guard !overflow,next<=maximumSize else { throw AoVLZ4Error.outputTooLarge }
                    value=next
                    if extra != 255 { break }
                }
            }
            return value
        }
        while cursor<input.count {
            let token=Int(input[cursor]); cursor+=1
            let literals=try length(token>>4,&cursor)
            guard literals<=input.count-cursor else { throw AoVLZ4Error.truncated }
            guard literals<=expectedSize-output.count else { throw AoVLZ4Error.outputTooLarge }
            if literals>0 { output.append(contentsOf:input[cursor..<(cursor+literals)]); cursor+=literals }
            if cursor==input.count { break }
            guard cursor+2<=input.count else { throw AoVLZ4Error.truncated }
            let offset=Int(input[cursor])|(Int(input[cursor+1])<<8); cursor+=2
            guard offset>0,offset<=output.count else { throw AoVLZ4Error.invalidBackReference }
            let matches=try length(token&15,&cursor)+4
            guard matches<=expectedSize-output.count else { throw AoVLZ4Error.outputTooLarge }
            for _ in 0..<matches { output.append(output[output.count-offset]) }
        }
        guard output.count==expectedSize else { throw AoVLZ4Error.sizeMismatch(expectedSize,output.count) }
        return Data(output)
    }
}
