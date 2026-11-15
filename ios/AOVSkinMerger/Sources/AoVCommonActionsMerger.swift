import Foundation
import CryptoKit
import ZIPFoundation

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

    func replaceDirectChild(_ target: AoVXMLNode, with replacement: AoVXMLNode) {
        for index in content.indices {
            if case .child(let node) = content[index], node === target {
                content[index] = .child(replacement)
                return
            }
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
                                target.replaceDirectChild(current, with: replacement)
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
            if !ids.isEmpty {
                let key = trackKey(sourceTrack)
                let present = Set(verifiedAction.directChildren("Track")
                    .filter { trackKey($0) == key }
                    .flatMap { $0.directChildren("SkinOrAvatarList").compactMap { $0.attributes["id"] } })
                guard ids.isSubset(of: present) else {
                    throw AoVCommonActionsError.roundTripFailed(resourceName)
                }
                continue
            }

            guard let sourceCondition = try skinCondition(sourceTrack, targetIDs: specialSkinIDs) else {
                continue
            }
            if sourceCondition.kind == .scalar {
                guard let scalarID = sourceCondition.scalarID,
                      specialSkinIDs.contains(scalarID) else { continue }
                let hasCondition = verifiedAction.directChildren("Track").contains { candidate in
                    guard trackKey(candidate) == trackKey(sourceTrack),
                          let info = try? skinCondition(candidate, targetIDs: specialSkinIDs) else { return false }
                    return info.kind == .scalar && info.scalarID == scalarID
                }
                guard hasCondition else {
                    throw AoVCommonActionsError.roundTripFailed(resourceName)
                }
            } else {
                let key = conditionArrayKey(sourceTrack)
                let matches = verifiedAction.directChildren("Track").filter {
                    conditionArrayKey($0) == key
                }
                let values = Set(matches.flatMap { candidate in
                    (try? skinCondition(candidate, targetIDs: []))?.arrayValues ?? []
                })
                let required = Set(sourceCondition.arrayValues.filter { specialSkinIDs.contains($0) })
                guard required.isSubset(of: values) else {
                    throw AoVCommonActionsError.roundTripFailed(resourceName)
                }
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

struct AoVActorSkinInventory: Sendable {
    let skinIDs: Set<String>
    let candidateFiles: Int
    let inspectedTracks: Int
    let conditionTracks: Int
}

struct AoVCommonActionsPackageReport: Sendable {
    let baseEntries: Int
    let overlayEntries: Int
    let outputEntries: Int
    let mergedFiles: [String]
    let appendedFiles: [String]
    let identicalFiles: [String]
    let wrapperMode: String
    let verified: Bool
}

extension AoVCommonActionsMerger {
    private static let packageXMLCandidates: Set<String> = [
        "commonresource/Back.xml",
        "commonresource/BackFast.xml",
        "commonresource/Born.xml",
        "commonresource/Dance.xml",
        "commonresource/DanceBullet.xml",
        "commonresource/Dead_Born.xml",
        "commonresource/HasteE1.xml",
        "commonresource/HasteE1_leave.xml",
        "commonresource/HasteE1_super.xml",
        "commonresource/HasteE2.xml",
        "commonresource/HasteE3.xml",
        "commonresource/QuickBack.xml"
    ]

    static func inspectActorSkinIDs(
        packageData: Data,
        actorIDs: Set<String>,
        resourceName: String = "Ages/Prefab_Characters/Prefab_Hero/CommonActions.pkg.bytes"
    ) throws -> AoVActorSkinInventory {
        guard !actorIDs.isEmpty else { return AoVActorSkinInventory(skinIDs: [], candidateFiles: 0, inspectedTracks: 0, conditionTracks: 0) }
        let wrapper = try unwrapPackage(packageData, resourceName: resourceName)
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-CommonActions-Inspect-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let packageURL = directory.appendingPathComponent("CommonActions.zip")
        try wrapper.data.write(to: packageURL, options: .atomic)
        let archive = try Archive(url: packageURL, accessMode: .read)

        var skinIDs = Set<String>()
        var candidateFiles = 0
        var inspectedTracks = 0
        var conditionTracks = 0

        for entry in archive where entry.type == .file {
            let normalizedPath = entry.path.replacingOccurrences(of: "\\", with: "/").lowercased()
            guard packageXMLCandidates.contains(normalizedPath) else { continue }
            guard entry.uncompressedSize <= maximumCandidateXMLBytes else {
                throw AoVCommonActionsError.invalidXML("\(entry.path): XML vượt giới hạn 16 MiB")
            }
            var bytes = Data()
            _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { bytes.append($0) })
            let decoded = try AoVBytesDecoder.decodeResource(bytes, resourceName: entry.path)
            let root = try AoVXMLTreeBuilder.parse(decoded.data, resourceName: entry.path)
            guard let action = root.directChildren("Action").first else {
                throw AoVCommonActionsError.missingAction(entry.path)
            }
            candidateFiles += 1

            for track in action.directChildren("Track") {
                inspectedTracks += 1
                let direct = track.directChildren("SkinOrAvatarList")
                let all = track.descendants("SkinOrAvatarList")
                if all.count != direct.count {
                    let directObjectIDs = Set(direct.map { ObjectIdentifier($0) })
                    let nestedIDs = all.filter { !directObjectIDs.contains(ObjectIdentifier($0)) }
                    if nestedIDs.contains(where: {
                        guard let id = $0.attributes["id"], id.count >= 3 else { return false }
                        return actorIDs.contains(String(id.prefix(3)))
                    }) {
                        throw AoVCommonActionsError.unsupportedNestedSkinList(
                            normalizedTrackName(track.attributes["trackName"] ?? "")
                        )
                    }
                }
                for node in direct {
                    guard let id = node.attributes["id"], id.allSatisfy(\.isNumber), id.count >= 3 else {
                        continue
                    }
                    if actorIDs.contains(String(id.prefix(3))) {
                        skinIDs.insert(id)
                    }
                }

                if direct.isEmpty,
                   conditionEvents.contains(track.attributes["eventType"] ?? "") {
                    conditionTracks += 1
                    let info = try skinCondition(track, targetIDs: actorIDs)
                    guard let info else { continue }
                    if info.kind == .scalar, let id = info.scalarID, id.count >= 3,
                       actorIDs.contains(String(id.prefix(3))) {
                        skinIDs.insert(id)
                    } else if info.kind == .array {
                        for id in info.arrayValues where
                            id.count >= 3 && actorIDs.contains(String(id.prefix(3))) {
                            skinIDs.insert(id)
                        }
                    }
                }
            }
        }

        return AoVActorSkinInventory(
            skinIDs: skinIDs,
            candidateFiles: candidateFiles,
            inspectedTracks: inspectedTracks,
            conditionTracks: conditionTracks
        )
    }

    private static let maximumPackageBytes = 512 * 1024 * 1024
    private static let maximumPackageEntries = 20_000
    private static let maximumPackageExpandedBytes: UInt64 = 1024 * 1024 * 1024
    private static let maximumCandidateXMLBytes: UInt64 = 16 * 1024 * 1024

    static func mergeCommonActionsPackage(
        basePackage: Data,
        overlayPackage: Data,
        resourceName: String = "CommonActions.pkg.bytes",
        specialSkinIDs: Set<String>
    ) throws -> (Data, AoVCommonActionsPackageReport) {
        guard basePackage.count <= maximumPackageBytes,
              overlayPackage.count <= maximumPackageBytes else {
            throw AoVCommonActionsError.invalidXML("\(resourceName): package vượt giới hạn 512 MiB")
        }
        let baseWrapper = try unwrapPackage(basePackage, resourceName: resourceName)
        let overlayWrapper = try unwrapPackage(overlayPackage, resourceName: resourceName)
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent("AKI-CommonActions-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let baseURL = directory.appendingPathComponent("base.pkg.zip")
        let overlayURL = directory.appendingPathComponent("overlay.pkg.zip")
        let outputURL = directory.appendingPathComponent("merged.pkg.zip")
        try baseWrapper.data.write(to: baseURL, options: .atomic)
        try overlayWrapper.data.write(to: overlayURL, options: .atomic)

        let baseArchive = try Archive(url: baseURL, accessMode: .read)
        let overlayArchive = try Archive(url: overlayURL, accessMode: .read)
        let baseEntries = try entryMap(baseArchive, label: "base")
        let overlayEntries = try entryMap(overlayArchive, label: "overlay")
        let totalExpanded = baseEntries.values.reduce(UInt64(0)) { $0 + $1.uncompressedSize }
            .addingReportingOverflow(overlayEntries.values.reduce(UInt64(0)) { $0 + $1.uncompressedSize })
        guard !totalExpanded.overflow, totalExpanded.partialValue <= 2 * maximumPackageExpandedBytes else {
            throw AoVCommonActionsError.invalidXML("\(resourceName): tổng dữ liệu giải nén vượt giới hạn")
        }

        var mergedNames: [String] = []
        var appendedNames: [String] = []
        var identicalNames: [String] = []

        do {
            let outputArchive = try Archive(url: outputURL, accessMode: .create)
            for (path, baseEntry) in baseEntries.sorted(by: { $0.key < $1.key }) {
                guard let overlayEntry = overlayEntries[path] else {
                    try copyEntry(baseEntry, from: baseArchive, to: outputArchive,
                                  path: path, scratchDirectory: directory)
                    continue
                }

                if packageXMLCandidates.contains(path.lowercased()) {
                    let baseData = try readCandidate(baseEntry, from: baseArchive, path: path)
                    let overlayData = try readCandidate(overlayEntry, from: overlayArchive, path: path)
                    if SHA256.hash(data: baseData) == SHA256.hash(data: overlayData) {
                        try addData(baseData, to: outputArchive, path: path,
                                    scratchDirectory: directory)
                        identicalNames.append(path)
                        continue
                    }
                    do {
                        let (merged, report) = try mergeActionXML(
                            baseBytes: baseData,
                            overlayBytes: overlayData,
                            resourceName: path,
                            specialSkinIDs: specialSkinIDs
                        )
                        guard report.verified else {
                            throw AoVCommonActionsError.roundTripFailed(path)
                        }
                        try addData(merged, to: outputArchive, path: path,
                                    scratchDirectory: directory)
                        mergedNames.append(path)
                    } catch AoVCommonActionsError.noSupportedOverlayTracks {
                        // The candidate XML contains no actor-owned gate for this overlay.
                        try addData(baseData, to: outputArchive, path: path,
                                    scratchDirectory: directory)
                    }
                    continue
                }

                let baseDigest = try sha256(baseEntry, in: baseArchive)
                let overlayDigest = try sha256(overlayEntry, in: overlayArchive)
                guard baseDigest == overlayDigest else {
                    throw AoVCommonActionsError.conflictingSkinEntry(path, "opaque resource")
                }
                try copyEntry(baseEntry, from: baseArchive, to: outputArchive,
                              path: path, scratchDirectory: directory)
                identicalNames.append(path)
            }

            for (path, entry) in overlayEntries.sorted(by: { $0.key < $1.key })
                where baseEntries[path] == nil {
                try copyEntry(entry, from: overlayArchive, to: outputArchive,
                              path: path, scratchDirectory: directory)
                appendedNames.append(path)
            }
        }

        let verifyArchive = try Archive(url: outputURL, accessMode: .read)
        let verifiedEntries = try entryMap(verifyArchive, label: "output")
        for entry in verifiedEntries.values {
            _ = try verifyArchive.extract(entry, bufferSize: 64 * 1024, consumer: { _ in })
        }
        let outputZIP = try Data(contentsOf: outputURL)
        let result: Data
        if baseWrapper.mode == "raw_zip" {
            result = outputZIP
        } else {
            result = try AoVBytesDecoder.encodeResource(
                outputZIP,
                resourceName: resourceName,
                wrapperMode: baseWrapper.mode
            )
        }
        let roundTrip: Data
        if baseWrapper.mode == "raw_zip" {
            roundTrip = result
        } else {
            roundTrip = try AoVBytesDecoder.decodeResource(result, resourceName: resourceName).data
        }
        guard SHA256.hash(data: roundTrip) == SHA256.hash(data: outputZIP) else {
            throw AoVCommonActionsError.roundTripFailed(resourceName)
        }

        return (result, AoVCommonActionsPackageReport(
            baseEntries: baseEntries.count,
            overlayEntries: overlayEntries.count,
            outputEntries: verifiedEntries.count,
            mergedFiles: mergedNames,
            appendedFiles: appendedNames,
            identicalFiles: identicalNames,
            wrapperMode: baseWrapper.mode,
            verified: true
        ))
    }

    private static func unwrapPackage(
        _ data: Data,
        resourceName: String
    ) throws -> (data: Data, mode: String) {
        if data.count >= 4,
           data[0] == 0x50, data[1] == 0x4B,
           (data[2] == 0x03 || data[2] == 0x05 || data[2] == 0x07) {
            return (data, "raw_zip")
        }
        let decoded = try AoVBytesDecoder.decodeResource(data, resourceName: resourceName)
        return (decoded.data, decoded.wrapperMode)
    }

    private static func entryMap(_ archive: Archive, label: String) throws -> [String: Entry] {
        var result: [String: Entry] = [:]
        var count = 0
        var expanded: UInt64 = 0
        for entry in archive {
            if entry.type == .directory { continue }
            guard entry.type == .file else {
                throw AoVCommonActionsError.invalidXML("\(label): symbolic link không được hỗ trợ")
            }
            let path = entry.path.replacingOccurrences(of: "\\", with: "/")
            let components = path.split(separator: "/", omittingEmptySubsequences: false)
            guard !path.hasPrefix("/"), !path.hasPrefix("~"),
                  !components.contains(".."), !components.contains("."),
                  !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else {
                throw AoVCommonActionsError.invalidXML("\(label): đường dẫn ZIP không an toàn: \(path)")
            }
            guard result[path] == nil else {
                throw AoVCommonActionsError.conflictingSkinEntry(path, "duplicate entry")
            }
            count += 1
            guard count <= maximumPackageEntries else {
                throw AoVCommonActionsError.invalidXML("\(label): ZIP có quá nhiều entry")
            }
            let sum = expanded.addingReportingOverflow(entry.uncompressedSize)
            guard !sum.overflow, sum.partialValue <= maximumPackageExpandedBytes else {
                throw AoVCommonActionsError.invalidXML("\(label): ZIP vượt giới hạn giải nén")
            }
            expanded = sum.partialValue
            result[path] = entry
        }
        return result
    }

    private static func readCandidate(
        _ entry: Entry,
        from archive: Archive,
        path: String
    ) throws -> Data {
        guard entry.uncompressedSize <= maximumCandidateXMLBytes else {
            throw AoVCommonActionsError.invalidXML("\(path): XML vượt giới hạn 16 MiB")
        }
        var data = Data()
        _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { chunk in
            data.append(chunk)
        })
        return data
    }

    private static func sha256(_ entry: Entry, in archive: Archive) throws -> Data {
        var hasher = SHA256()
        _ = try archive.extract(entry, bufferSize: 64 * 1024, consumer: { chunk in
            hasher.update(data: chunk)
        })
        return Data(hasher.finalize())
    }

    private static func copyEntry(
        _ entry: Entry,
        from source: Archive,
        to destination: Archive,
        path: String,
        scratchDirectory: URL
    ) throws {
        let scratch = scratchDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        _ = try source.extract(entry, to: scratch, bufferSize: 64 * 1024)
        try destination.addEntry(with: path, fileURL: scratch, compressionMethod: .deflate,
                                 bufferSize: 64 * 1024)
    }

    private static func addData(
        _ data: Data,
        to destination: Archive,
        path: String,
        scratchDirectory: URL
    ) throws {
        let scratch = scratchDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: scratch) }
        try data.write(to: scratch, options: .atomic)
        try destination.addEntry(with: path, fileURL: scratch, compressionMethod: .deflate,
                                 bufferSize: 64 * 1024)
    }
}
