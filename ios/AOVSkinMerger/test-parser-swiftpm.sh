#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$SCRIPT_DIR/ParserPackage"
SOURCE_DIR="$PACKAGE_DIR/Sources/AOVSkinMerger"
TEST_DIR="$PACKAGE_DIR/Tests/AOVSkinMergerTests"

mkdir -p "$SOURCE_DIR" "$TEST_DIR"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileParser.swift" \
   "$SOURCE_DIR/AoVSerializedFileParser.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileObjectAppender.swift" \
   "$SOURCE_DIR/AoVSerializedFileObjectAppender.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileObjectImportPlanner.swift" \
   "$SOURCE_DIR/AoVSerializedFileObjectImportPlanner.swift"
cp "$SCRIPT_DIR/Sources/AoVRuntimeResourceCatalogParser.swift" \
   "$SOURCE_DIR/AoVRuntimeResourceCatalogParser.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedAssetBundleWriter.swift" \
   "$SOURCE_DIR/AoVSerializedAssetBundleWriter.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileRewriter.swift" \
   "$SOURCE_DIR/AoVSerializedFileRewriter.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileObjectImportPlanner.swift" \
   "$SOURCE_DIR/AoVSerializedFileObjectImportPlanner.swift"
cp "$SCRIPT_DIR/Sources/AoVAssetBundleCatalogMerger.swift" \
   "$SOURCE_DIR/AoVAssetBundleCatalogMerger.swift"
test -s "$SOURCE_DIR/AoVSerializedAssetBundleWriter.swift"
cp "$SCRIPT_DIR/ParserPackageTests/AoVSerializedFileParserPortableTests.swift" \
   "$TEST_DIR/AoVSerializedFileParserPortableTests.swift"
cp "$SCRIPT_DIR/Tests/AoVRuntimeResourceCatalogParserTests.swift" \
   "$TEST_DIR/AoVRuntimeResourceCatalogParserTests.swift"
cp "$SCRIPT_DIR/Tests/AoVAssetBundleCatalogMergerTests.swift" \
   "$TEST_DIR/AoVAssetBundleCatalogMergerTests.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityCommonStrings.swift" \
   "$SOURCE_DIR/AoVUnityCommonStrings.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityTypeTreeParser.swift" \
   "$SOURCE_DIR/AoVUnityTypeTreeParser.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityTypeTreeWriter.swift" \
   "$SOURCE_DIR/AoVUnityTypeTreeWriter.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityTypeTreePointerRemapper.swift" \
   "$SOURCE_DIR/AoVUnityTypeTreePointerRemapper.swift"
cp "$SCRIPT_DIR/Sources/AoVRuntimeCatalogMergePlanner.swift" \
   "$SOURCE_DIR/AoVRuntimeCatalogMergePlanner.swift"
cp "$SCRIPT_DIR/Sources/AoVSerializedRuntimeCatalogWriter.swift" \
   "$SOURCE_DIR/AoVSerializedRuntimeCatalogWriter.swift"
cp "$SCRIPT_DIR/Tests/AoVUnityTypeTreeParserTests.swift" \
   "$TEST_DIR/AoVUnityTypeTreeParserTests.swift"
cp "$SCRIPT_DIR/Tests/AoVSerializedFileObjectImportPlannerTests.swift" \
   "$TEST_DIR/AoVSerializedFileObjectImportPlannerTests.swift"
cp "$SCRIPT_DIR/Tests/AoVSerializedFileObjectImportPlannerTests.swift" \
   "$TEST_DIR/AoVSerializedFileObjectImportPlannerTests.swift"
cp "$SCRIPT_DIR/Tests/AoVRuntimeCatalogMergePlannerTests.swift" \
   "$TEST_DIR/AoVRuntimeCatalogMergePlannerTests.swift"
cp "$SCRIPT_DIR/ParserPackageTests/AoVSerializedRuntimeCatalogWriterPortableTests.swift" \
   "$TEST_DIR/AoVSerializedRuntimeCatalogWriterPortableTests.swift"

swift package describe --package-path "$PACKAGE_DIR"
swift test --package-path "$PACKAGE_DIR" --parallel
