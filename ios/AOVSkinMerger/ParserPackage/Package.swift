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
                "AoVSerializedFileRewriter.swift",
                "AoVAssetBundleCatalogMerger.swift",
                "AoVUnityCommonStrings.swift",
                "AoVUnityTypeTreeParser.swift",
                "AoVUnityTypeTreeWriter.swift",
                "AoVUnityTypeTreePointerRemapper.swift",
                "AoVRuntimeCatalogMergePlanner.swift",
                "AoVSerializedRuntimeCatalogWriter.swift"
            ]
        ),
        .testTarget(
            name: "AOVSkinMergerTests",
            dependencies: ["AOVSkinMerger"]
        )
    ]
)
