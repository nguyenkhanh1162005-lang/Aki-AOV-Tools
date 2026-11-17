import Foundation

struct AoVActorEvidence: Sendable {
    let actorActionIDs: Set<String>
    let assetReferenceIDs: Set<String>
    let assetBundleIDs: Set<String>
    let prefabPathIDs: Set<String>
    let actorDirectoryIDs: Set<String>

    var allActorIDs: Set<String> {
        actorActionIDs
            .union(assetReferenceIDs)
            .union(assetBundleIDs)
            .union(prefabPathIDs)
            .union(actorDirectoryIDs)
    }
}

enum AoVActorDetector {
    private static let patterns: [(keyPath: WritableKeyPath<MutableEvidence, Set<String>>, pattern: String)] = [
        (\.actorActionIDs, #"(?i)(?:^|/)Actor[_-](\d{3,4})[_-]Actions\.pkg\.bytes$"#),
        (\.assetReferenceIDs, #"(?i)(?:^|/)AssetRefs/Hero/(\d{3,4})_AssetRef\.bytes$"#),
        (\.assetBundleIDs, #"(?i)(?:^|/)assetbundle/(?:[^/]+/)*(\d{3,4})_[^/]+\.assetbundle$"#),
        (\.prefabPathIDs, #"(?i)(?:^|/)Prefab_Hero/(\d{3,4})_[^/]+(?:\.bytes|\.pkg\.bytes)$"#),
        (\.actorDirectoryIDs, #"(?i)(?:^|/)(?:actor|hero)[_-](\d{3,4})(?:/|$)"#),
        // Totorox 2.0's embedded detector also recognizes Resources/hero/<id>/...
        (\.actorDirectoryIDs, #"(?i)(?:^|/)hero/(\d{3,4})(?:_|/|\.|$)"#),
        (\.actorDirectoryIDs, #"(?i)(?:^|/)commercializenotify_(?:blue|red)_(\d{3,4})(?:_|\.|$)"#)
    ]

    private struct MutableEvidence {
        var actorActionIDs = Set<String>()
        var assetReferenceIDs = Set<String>()
        var assetBundleIDs = Set<String>()
        var prefabPathIDs = Set<String>()
        var actorDirectoryIDs = Set<String>()
    }

    static func detect(paths: [String]) -> AoVActorEvidence {
        var evidence = MutableEvidence()
        for rawPath in paths {
            let path = rawPath.replacingOccurrences(of: "\\", with: "/").trimmingCharacters(in: CharacterSet(charactersIn: "/"))
            for (keyPath, pattern) in patterns {
                guard let regex = try? NSRegularExpression(pattern: pattern) else { continue }
                let range = NSRange(path.startIndex..<path.endIndex, in: path)
                for match in regex.matches(in: path, range: range) where match.numberOfRanges > 1 {
                    guard let actorRange = Range(match.range(at: 1), in: path) else { continue }
                    evidence[keyPath: keyPath].insert(String(path[actorRange]))
                }
            }
        }
        return AoVActorEvidence(
            actorActionIDs: evidence.actorActionIDs,
            assetReferenceIDs: evidence.assetReferenceIDs,
            assetBundleIDs: evidence.assetBundleIDs,
            prefabPathIDs: evidence.prefabPathIDs,
            actorDirectoryIDs: evidence.actorDirectoryIDs
        )
    }

    static func skinIDs(in text: String, actorIDs: Set<String>) -> Set<String> {
        guard !actorIDs.isEmpty,
              let regex = try? NSRegularExpression(pattern: #"(?<!\d)(\d{5,6})(?!\d)"#) else {
            return []
        }
        let range = NSRange(text.startIndex..<text.endIndex, in: text)
        let candidates = regex.matches(in: text, range: range).compactMap { match -> String? in
            guard let r = Range(match.range(at: 1), in: text) else { return nil }
            return String(text[r])
        }
        // AoV paths can expose either 3-digit or 4-digit hero IDs. Match the
        // complete detected ID rather than blindly taking the first 3 digits.
        let orderedActorIDs = actorIDs.sorted {
            if $0.count != $1.count { return $0.count > $1.count }
            return $0 < $1
        }
        return Set(candidates.filter { value in
            orderedActorIDs.contains { value.hasPrefix($0) }
        })
    }

    static func skinNames(in text: String, limit: Int = 100) -> [String] {
        var result: [String] = []
        var seen = Set<String>()
        let lines = text.replacingOccurrences(of: "\r", with: "\n").components(separatedBy: "\n")
        for rawLine in lines {
            let stripped = String(rawLine.trimmingCharacters(in: .whitespacesAndNewlines)
                .drop(while: { "-*•· ".contains($0) }))
            let value = stripped.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !value.isEmpty, value.count <= 160 else { continue }
            guard value.rangeOfCharacter(from: .letters) != nil else { continue }
            let key = value.lowercased()
            if seen.insert(key).inserted {
                result.append(value)
            }
            if result.count >= max(1, limit) { break }
        }
        return result
    }
}
