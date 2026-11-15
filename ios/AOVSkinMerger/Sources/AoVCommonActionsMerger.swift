import Foundation
import FoundationXML
import CryptoKit

enum AoVCommonActionsError: LocalizedError {
    case invalidSkinIDs
    case invalidXML(String)
    case missingAction(String)
    case unsupportedNestedSkinList(String)
    case unsupportedSkinCondition(String)
    case conflictingSkinEntry(String, String)
    case noSupportedOverlayTracks
    case roundTripFailed(String)

    var errorDescription: String? {
        switch self {
        case .invalidSkinIDs:
            return "Danh sách skin ID phải là tập số không rỗng."
        case .invalidXML(let path):
            return "\(path): XML không hợp lệ hoặc không đọc được."
        case .missingAction(let path):
            return "\(path): thiếu node Action."
        case .unsupportedNestedSkinList(let track):
            return "Track \(track): SkinOrAvatarList lồng chưa có schema ghép đã xác minh."
        case .unsupportedSkinCondition(let track):
            return "Track \(track): CheckSkinIdTick/CheckSkinIdVirtualTick không khớp schema targetId + skinId/skinIdArray đã hỗ trợ."
        case .conflictingSkinEntry(let track, let skinID):
            return "Track \(track): skin ID \(skinID) có metadata khác nhau trong dữ liệu nền."
        case .noSupportedOverlayTracks:
            return "Không tìm thấy track skin thuộc các ID đã chọn trong overlay."
        case .roundTripFailed(let path):
            return "\(path): track skin không còn nguyên vẹn sau encode/decode."
        }
    }
}

private enum AoVXMLContent {
    case text(String)
    case child(AoVXMLNode)
}

private final class AoVXMLNode {
    let name: String
    var attributes: [String: String]
    var content: [AoVXMLContent] = []

    init(name: String, attributes: [String: String] = [:]) {
        self.name = name
        self.attributes = attributes
    }

    var children: [AoVXMLNode] {
        content.compactMap {
            if case .child(let node) = $0 { return node }
            return nil
        }
    }

    func directChildren(_ name: String) -> [AoVXMLNode] {
        children.filter { $0.name == name }
    }

    func descendants(_ name: String) -> [AoVXMLNode] {
        var result: [AoVXMLNode] = []
        for child in children {
            if child.name == name { result.append(child) }
            result.append(contentsOf: child.descendants(name))
        }
        return result
    }

    func append(_ child: AoVXMLNode) {
        content.append(.child(child))
    }

    func removeDirectChild(_ target: AoVXMLNode) {
        content.removeAll {
            if case .child(let node) = $0 { return node === target }
            return false
        }
    }

    func clone() -> AoVXMLNode {
        let node = AoVXMLNode(name: name, attributes: attributes)
        for part in content {
            switch part {
            case .text(let value): node.content.append(.text(value))
            case .child(let child): node.content.append(.child(child.clone()))
            }
        }
        return node
    }

    func serialized(canonical: Bool = false, rootTrack: Bool = false) -> String {
        var attrs = attributes
        if rootTrack, let name = attrs["trackName"] {
            attrs["trackName"] = AoVCommonActionsMerger.normalizedTrackName(name)
        }
        for key in Array(attrs.keys) where
            key.lowercased() == "guid" || key.lowercased() == "m_guid" ||
            (rootTrack && ["guid", "userefparam", "refparamname"].contains(key.lowercased())) {
            attrs.removeValue(forKey: key)
        }

        let opening = "<\(name)" + attrs.keys.sorted().map {
            " \($0)=\"\(AoVXMLNode.escapeAttribute(attrs[$0] ?? ""))\""
        }.joined()

        var renderedContent = ""
        for part in content {
            switch part {
            case .text(let value):
                if canonical && value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    continue
                }
                renderedContent += AoVXMLNode.escapeText(value)
            case .child(let child):
                if canonical && child.name == "SkinOrAvatarList" {
                    continue
                }
                renderedContent += child.serialized(canonical: canonical)
            }
        }

        if renderedContent.isEmpty {
            return opening + "/>"
        }
        return opening + ">" + renderedContent + "</\(name)>"
    }

    private static func escapeText(_ value: String) -> String {
        value.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    private static func escapeAttribute(_ value: String) -> String {
        escapeText(value).replacingOccurrences(of: "\"", with: "&quot;")
            .replacingOccurrences(of: "'", with: "&apos;")
    }
}

private final class AoVXMLTreeBuilder: NSObject, XMLParserDelegate {
    private(set) var root: AoVXMLNode?
    private var stack: [AoVXMLNode] = []

    func parser(
        _ parser: XMLParser,
        didStartElement elementName: String,
        namespaceURI: String?,
        qualifiedName qName: String?,
        attributes attributeDict: [String: String]
    ) {
        let node = AoVXMLNode(name: qName ?? elementName, attributes: attributeDict)
        if let parent = stack.last {
            parent.append(node)
        } else {
            root = node
        }
        stack.append(node)
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        guard let current = stack.last else { return }
        if case .text(let previous)? = current.content.last {
            current.content.removeLast()
            current.content.append(.text(previous + string))
        } else {
            current.content.append(.text(string))
        }
    }

    func parser(_ parser: XMLParser, foundCDATA CDATABlock: Data) {
        guard let string = String(data: CDATABlock, encoding: .utf8),
              let current = stack.last else { return }
        current.content.append(.text(string))
    }

    func parser(
        _ parser: XMLParser,
        didEndElement elementName: String,
        namespaceURI: String?,
        qualifiedName qName: String?
    ) {
        if !stack.isEmpty { stack.removeLast() }
    }

    static func parse(_ data: Data, resourceName: String) throws -> AoVXMLNode {
        let parserDelegate = AoVXMLTreeBuilder()
        let parser = XMLParser(data: data)
        parser.shouldResolveExternalEntities = false
        parser.delegate = parserDelegate
        guard parser.parse(), let root = parserDelegate.root else {
            throw AoVCommonActionsError.invalidXML(resourceName)
        }
        return root
    }
}

private struct AoVTrackKey: Hashable {
    let normalizedName: String
    let eventType: String
    let signature: String
}

private struct AoVConditionInfo {
    enum Kind { case scalar, array }
    let kind: Kind
    let eventType: String
    let scalarID: String?
    let arrayValues: [String]
    let arrayNode: AoVXMLNode?
}

struct AoVCommonActionsMergeReport: Sendable {
    let resourceName: String
    let wrapperMode: String
    let overlayWrapperMode: String
    let appendedTracks: [String]
    let mergedIntoExistingTracks: [String]
    let replacedTracks: [String]
    let alreadyPresentTracks: [String]
    let conditionAppendedTracks: [String]
    let conditionMergedTracks: [String]
    let verified: Bool
}

enum AoVCommonActionsMerger {
    private static let conditionEvents: Set<String> = [
        "CheckSkinIdTick",
        "CheckSkinIdVirtualTick"
    ]

    static func normalizedTrackName(_ name: String) -> String {
        guard let range = name.range(of: #"^\d+_"#, options: .regularExpression) else {
            return name
        }
        return String(name[range.upperBound...])
    }

    private static func trackKey(_ track: AoVXMLNode) -> AoVTrackKey {
        AoVTrackKey(
            normalizedName: normalizedTrackName(track.attributes["trackName"] ?? ""),
            eventType: track.attributes["eventType"] ?? "",
            signature: SHA256.hash(data: Data(track.serialized(canonical: true, rootTrack: true).utf8))
                .map { String(format: "%02x", $0) }.joined()
        )
    }

    private static func conditionArrayKey(_ track: AoVXMLNode) -> AoVTrackKey {
        let clone = track.clone()
        for event in clone.directChildren("Event") {
            for array in event.directChildren("Array") where array.attributes["name"] == "skinIdArray" {
                array.content.removeAll {
                    if case .child(let child) = $0 { return child.name == "int" }
                    return false
                }
            }
        }
        return trackKeyWithSignatureSource(clone)
    }

    private static func trackKeyWithSignatureSource(_ track: AoVXMLNode) -> AoVTrackKey {
        AoVTrackKey(
            normalizedName: normalizedTrackName(track.attributes["trackName"] ?? ""),
            eventType: track.attributes["eventType"] ?? "",
            signature: SHA256.hash(data: Data(track.serialized(canonical: true, rootTrack: true).utf8))
                .map { String(format: "%02x", $0) }.joined()
        )
    }

    private static func skinCondition(
        _ track: AoVXMLNode,
        targetIDs: Set<String>
    ) throws -> AoVConditionInfo? {
        let eventType = track.attributes["eventType"] ?? ""
        guard conditionEvents.contains(eventType) else { return nil }
        guard track.descendants("SkinOrAvatarList").isEmpty else { return nil }

        let events = track.directChildren("Event")
        guard events.count == 1,
              events[0].attributes["eventName"] == eventType else {
            throw AoVCommonActionsError.unsupportedSkinCondition(
                normalizedTrackName(track.attributes["trackName"] ?? "")
            )
        }
        let event = events[0]
        let targetNodes = event.directChildren("TemplateObject").filter {
            $0.attributes["name"] == "targetId"
        }
        guard targetNodes.count == 1,
              ["self", "target"].contains(targetNodes[0].attributes["objectName"] ?? "") else {
            throw AoVCommonActionsError.unsupportedSkinCondition(
                normalizedTrackName(track.attributes["trackName"] ?? "")
            )
        }

        let scalarNodes = event.directChildren("int").filter { $0.attributes["name"] == "skinId" }
        let arrayNodes = event.directChildren("Array").filter { $0.attributes["name"] == "skinIdArray" }
        guard scalarNodes.count <= 1, arrayNodes.count <= 1,
              !scalarNodes.isEmpty || !arrayNodes.isEmpty else {
            throw AoVCommonActionsError.unsupportedSkinCondition(
                normalizedTrackName(track.attributes["trackName"] ?? "")
            )
        }

        var scalarID: String?
        if let scalar = scalarNodes.first {
            let value = scalar.attributes["value"] ?? ""
            guard value.allSatisfy(\.isNumber), !value.isEmpty else {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            scalarID = value
        }

        if let array = arrayNodes.first {
            guard array.attributes["type"] == "int" else {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            let children = array.children
            guard !children.isEmpty, children.allSatisfy({ $0.name == "int" }) else {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            let values = children.map { $0.attributes["value"] ?? "" }
            guard values.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) }),
                  Set(values).count == values.count else {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            let negateNodes = event.directChildren("bool").filter { $0.attributes["name"] == "useNegateValue" }
            guard negateNodes.count <= 1,
                  negateNodes.allSatisfy({ ["true", "false"].contains($0.attributes["value"] ?? "") }) else {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            if let scalarID, targetIDs.contains(scalarID) {
                throw AoVCommonActionsError.unsupportedSkinCondition(
                    normalizedTrackName(track.attributes["trackName"] ?? "")
                )
            }
            return AoVConditionInfo(kind: .array, eventType: eventType,
                                    scalarID: scalarID, arrayValues: values, arrayNode: array)
        }

        return AoVConditionInfo(kind: .scalar, eventType: eventType,
                                scalarID: scalarID, arrayValues: [], arrayNode: nil)
    }

    private static func appendTrack(
        _ source: AoVXMLNode,
        key: AoVTrackKey,
        resourceName: String,
        usedNames: inout Set<String>,
        nextIndex: inout Int,
        action: AoVXMLNode
    ) {
        let addition = source.clone()
        let original = addition.attributes["trackName"] ?? ""
        var name = original
        if name.isEmpty || usedNames.contains(name) {
            let suffix = normalizedTrackName(original).isEmpty
                ? "CommonActionTrack"
                : normalizedTrackName(original)
            repeat {
                name = "\(nextIndex)_\(suffix)"
                nextIndex += 1
            } while usedNames.contains(name)
        }
        addition.attributes["trackName"] = name
        if addition.attributes["guid"] == nil {
            let seed = Data("\(resourceName):\(key.signature)".utf8)
            var bytes = Array(SHA256.hash(data: seed).prefix(16))
            bytes[6] = (bytes[6] & 0x0F) | 0x50
            bytes[8] = (bytes[8] & 0x3F) | 0x80
            let uuid = UUID(uuid: (
                bytes[0], bytes[1], bytes[2], bytes[3],
                bytes[4], bytes[5], bytes[6], bytes[7],
                bytes[8], bytes[9], bytes[10], bytes[11],
                bytes[12], bytes[13], bytes[14], bytes[15]
            ))
            addition.attributes["guid"] = uuid.uuidString.lowercased()
        }
        if addition.attributes["useRefParam"] == nil { addition.attributes["useRefParam"] = "false" }
        if addition.attributes["refParamName"] == nil { addition.attributes["refParamName"] = "" }
        action.append(addition)
        usedNames.insert(name)
    }

    static func mergeActionXML(
        baseBytes: Data,
        overlayBytes: Data,
        resourceName: String,
        specialSkinIDs: Set<String>
    ) throws -> (Data, AoVCommonActionsMergeReport) {
        guard !specialSkinIDs.isEmpty,
              specialSkinIDs.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) }) else {
            throw AoVCommonActionsError.invalidSkinIDs
        }

        let baseDecoded = try AoVBytesDecoder.decodeResource(baseBytes, resourceName: resourceName)
        let overlayDecoded = try AoVBytesDecoder.decodeResource(overlayBytes, resourceName: resourceName)
        let baseRoot = try AoVXMLTreeBuilder.parse(baseDecoded.data, resourceName: resourceName)
        let overlayRoot = try AoVXMLTreeBuilder.parse(overlayDecoded.data, resourceName: resourceName)

        guard let baseAction = baseRoot.directChildren("Action").first,
              let overlayAction = overlayRoot.directChildren("Action").first else {
            throw AoVCommonActionsError.missingAction(resourceName)
        }

        let baseTracks = baseAction.directChildren("Track")
        let overlayTracks = overlayAction.directChildren("Track")
        var index: [AoVTrackKey: AoVXMLNode] = [:]
        for track in baseTracks where index[trackKey(track)] == nil {
            index[trackKey(track)] = track
        }

        var usedNames = Set(baseTracks.compactMap { $0.attributes["trackName"] })
        let numericPrefixes = (baseTracks + overlayTracks).compactMap { track -> Int? in
            let name = track.attributes["trackName"] ?? ""
            guard let range = name.range(of: #"^\d+_"#, options: .regularExpression) else { return nil }
            return Int(name[..<range.upperBound].dropLast())
        }
        var nextIndex = (numericPrefixes.max() ?? -1) + 1

        var appended: [String] = []
        var mergedIntoExisting: [String] = []
        var replaced: [String] = []
        var alreadyPresent: [String] = []
        var conditionAppended: [String] = []
        var conditionMerged: [String] = []
        var contributed = false

        for sourceTrack in overlayTracks {
            let directNodes = sourceTrack.directChildren("SkinOrAvatarList")
            let allNodes = sourceTrack.descendants("SkinOrAvatarList")
            if allNodes.count != directNodes.count {
                throw AoVCommonActionsError.unsupportedNestedSkinList(
                    normalizedTrackName(sourceTrack.attributes["trackName"] ?? "")
                )
            }

            if !directNodes.isEmpty {
                let ownedNodes = directNodes.filter {
                    specialSkinIDs.contains($0.attributes["id"] ?? "")
                }
                if ownedNodes.isEmpty { continue }
                contributed = true

                let filteredTrack = sourceTrack.clone()
                for node in filteredTrack.directChildren("SkinOrAvatarList")
                    where !specialSkinIDs.contains(node.attributes["id"] ?? "") {
                    filteredTrack.removeDirectChild(node)
                }
                let key = trackKey(filteredTrack)
                let name = normalizedTrackName(filteredTrack.attributes["trackName"] ?? "")

                if let target = index[key] {
                    let wasSpecial = !target.directChildren("SkinOrAvatarList").filter {
                        specialSkinIDs.contains($0.attributes["id"] ?? "")
                    }.isEmpty
                    var existing: [String: AoVXMLNode] = [:]
                    for node in target.directChildren("SkinOrAvatarList") {
                        if let id = node.attributes["id"] { existing[id] = node }
                    }
                    var changed = false
                    for sourceNode in filteredTrack.directChildren("SkinOrAvatarList") {
                        guard let skinID = sourceNode.attributes["id"] else { continue }
                        if let current = existing[skinID] {
                            if current.attributes != sourceNode.attributes {
                                let replacement = sourceNode.clone()
                                target.removeDirectChild(current)
                                target.append(replacement)
                                existing[skinID] = replacement
                                changed = true
                            }
                        } else {
                            let addition = sourceNode.clone()
                            target.append(addition)
                            existing[skinID] = addition
                            changed = true
                        }
                    }
                    for attribute in ["useRefParam", "refParamName"] {
                        if target.attributes[attribute] == nil,
                           let value = sourceTrack.attributes[attribute] {
                            target.attributes[attribute] = value
                            changed = true
                        }
                    }
                    if changed {
                        if wasSpecial { mergedIntoExisting.append(name) }
                        else { replaced.append(name) }
                    } else {
                        alreadyPresent.append(name)
                    }
                } else {
                    appendTrack(filteredTrack, key: key, resourceName: resourceName,
                                usedNames: &usedNames, nextIndex: &nextIndex, action: baseAction)
                    index[key] = baseAction.directChildren("Track").last
                    appended.append(name)
                }
                continue
            }

            guard let condition = try skinCondition(sourceTrack, targetIDs: specialSkinIDs) else {
                continue
            }
            let name = normalizedTrackName(sourceTrack.attributes["trackName"] ?? "")
            if condition.kind == .scalar {
                guard let scalarID = condition.scalarID,
                      specialSkinIDs.contains(scalarID) else { continue }
                let key = trackKey(sourceTrack)
                let exists = baseAction.directChildren("Track").contains {
                    trackKey($0) == key &&
                    ((try? skinCondition($0, targetIDs: specialSkinIDs))?.scalarID == scalarID)
                }
                if exists {
                    alreadyPresent.append(name)
                } else {
                    appendTrack(sourceTrack, key: key, resourceName: resourceName,
                                usedNames: &usedNames, nextIndex: &nextIndex, action: baseAction)
                    conditionAppended.append(name)
                }
                contributed = true
                continue
            }

            guard let sourceArray = condition.arrayNode else { continue }
            let ownedValues = condition.arrayValues.filter { specialSkinIDs.contains($0) }
            if ownedValues.isEmpty { continue }
            let key = conditionArrayKey(sourceTrack)
            let candidates = baseAction.directChildren("Track").filter {
                $0.attributes["eventType"] == key.eventType &&
                normalizedTrackName($0.attributes["trackName"] ?? "") == key.normalizedName &&
                conditionArrayKey($0) == key
            }
            if !candidates.isEmpty {
                var changed = false
                for target in candidates {
                    guard let targetInfo = try skinCondition(target, targetIDs: []) ,
                          let targetArray = targetInfo.arrayNode else { continue }
                    var currentValues = targetArray.children.compactMap { $0.attributes["value"] }
                    let missing = ownedValues.filter { !currentValues.contains($0) }
                    if !missing.isEmpty, let template = targetArray.children.first {
                        for value in missing {
                            let child = template.clone()
                            child.attributes["value"] = value
                            targetArray.append(child)
                            currentValues.append(value)
                        }
                        changed = true
                    }
                }
                if changed { conditionMerged.append(name) }
                else { alreadyPresent.append(name) }
            } else {
                let filteredTrack = sourceTrack.clone()
                guard let filteredEvent = filteredTrack.directChildren("Event").first,
                      let filteredArray = filteredEvent.directChildren("Array").first(where: {
                        $0.attributes["name"] == "skinIdArray"
                      }),
                      let template = filteredArray.children.first else {
                    throw AoVCommonActionsError.unsupportedSkinCondition(name)
                }
                for child in filteredArray.children { filteredArray.removeDirectChild(child) }
                for value in ownedValues {
                    let newChild = template.clone()
                    newChild.attributes["value"] = value
                    filteredArray.append(newChild)
                }
                let filteredKey = trackKey(filteredTrack)
                appendTrack(filteredTrack, key: filteredKey, resourceName: resourceName,
                            usedNames: &usedNames, nextIndex: &nextIndex, action: baseAction)
                conditionAppended.append(name)
            }
            contributed = true
            _ = sourceArray
        }

        guard contributed else { throw AoVCommonActionsError.noSupportedOverlayTracks }

        let outputXML = Data(("<?xml version=\"1.0\" encoding=\"utf-8\"?>" + baseRoot.serialized()).utf8)
        let output = try AoVBytesDecoder.encodeResource(
            outputXML,
            resourceName: resourceName,
            wrapperMode: baseDecoded.wrapperMode
        )
        let verified = try AoVBytesDecoder.decodeResource(output, resourceName: resourceName)
        let verifiedRoot = try AoVXMLTreeBuilder.parse(verified.data, resourceName: resourceName)
        guard let verifiedAction = verifiedRoot.directChildren("Action").first else {
            throw AoVCommonActionsError.roundTripFailed(resourceName)
        }

        for sourceTrack in overlayTracks {
            let ids = Set(sourceTrack.directChildren("SkinOrAvatarList").compactMap {
                $0.attributes["id"]
            }.filter { specialSkinIDs.contains($0) })
            if ids.isEmpty { continue }
            let key = trackKey(sourceTrack)
            let present = Set(verifiedAction.directChildren("Track")
                .filter { trackKey($0) == key }
                .flatMap { $0.directChildren("SkinOrAvatarList").compactMap { $0.attributes["id"] } })
            guard ids.isSubset(of: present) else {
                throw AoVCommonActionsError.roundTripFailed(resourceName)
            }
        }

        return (output, AoVCommonActionsMergeReport(
            resourceName: resourceName,
            wrapperMode: baseDecoded.wrapperMode,
            overlayWrapperMode: overlayDecoded.wrapperMode,
            appendedTracks: appended,
            mergedIntoExistingTracks: mergedIntoExisting,
            replacedTracks: replaced,
            alreadyPresentTracks: alreadyPresent,
            conditionAppendedTracks: conditionAppended,
            conditionMergedTracks: conditionMerged,
            verified: true
        ))
    }
}
