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
                "AoVSerializedAssetBundleWriter.swift",
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
