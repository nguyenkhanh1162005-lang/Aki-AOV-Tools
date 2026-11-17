#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$SCRIPT_DIR/ParserPackage"
SOURCE_DIR="$PACKAGE_DIR/Sources/AOVSkinMerger"
TEST_DIR="$PACKAGE_DIR/Tests/AOVSkinMergerTests"

mkdir -p "$SOURCE_DIR" "$TEST_DIR"
cp "$SCRIPT_DIR/Sources/AoVSerializedFileParser.swift" \
   "$SOURCE_DIR/AoVSerializedFileParser.swift"
cp "$SCRIPT_DIR/Tests/AoVSerializedFileParserTests.swift" \
   "$TEST_DIR/AoVSerializedFileParserTests.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityCommonStrings.swift" \
   "$SOURCE_DIR/AoVUnityCommonStrings.swift"
cp "$SCRIPT_DIR/Sources/AoVUnityTypeTreeParser.swift" \
   "$SOURCE_DIR/AoVUnityTypeTreeParser.swift"
cp "$SCRIPT_DIR/Tests/AoVUnityTypeTreeParserTests.swift" \
   "$TEST_DIR/AoVUnityTypeTreeParserTests.swift"

swift test --package-path "$PACKAGE_DIR" --parallel
