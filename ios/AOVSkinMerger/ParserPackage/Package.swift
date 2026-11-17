// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AOVSkinMerger",
    products: [
        .library(name: "AOVSkinMerger", targets: ["AOVSkinMerger"])
    ],
    targets: [
        .target(name: "AOVSkinMerger"),
        .testTarget(
            name: "AOVSkinMergerTests",
            dependencies: ["AOVSkinMerger"]
        )
    ]
)
