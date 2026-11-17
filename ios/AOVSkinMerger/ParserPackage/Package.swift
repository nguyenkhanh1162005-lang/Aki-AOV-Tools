// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AOVSkinMerger",
    products: [
        .library(name: "AOVSkinMerger", targets: ["AOVSkinMerger"])
    ],
    targets: [
        .target(
            name: "AOVSkinMerger",
            path: "Sources/AOVSkinMerger",
            sources: [
                "AoVSerializedFileParser.swift",
                "AoVRuntimeResourceCatalogParser.swift",
                "AoVSerializedAssetBundleWriter.swift",
                "AoVAssetBundleCatalogMerger.swift",
                "AoVUnityCommonStrings.swift",
                "AoVUnityTypeTreeParser.swift"
            ]
        ),
        .testTarget(
            name: "AOVSkinMergerTests",
            dependencies: ["AOVSkinMerger"]
        )
    ]
)
