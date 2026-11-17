# AKI AOV SKIN for iOS — prototype 0.5.0 (build 17)

This is an **unsigned iOS device-build prototype**. It is not a universal or game-certified AoV skin merger. The current project target is iOS 26.5.

## Implemented on-device

- Import multiple MOD ZIP archives from the iOS Files picker. Source files remain in the app sandbox and are not uploaded.
- Inventory ZIP entries, validate CRC, calculate SHA-256, inspect nested `Resources.zip`, validate paths, and compare game version markers.
- Decode and encode the currently supported AoV AES-CBC, AES+Zstandard, and standalone Zstandard wrappers; preserve wrapper mode and require round-trip verification.
- Inspect UnityFS AssetBundle headers, engine/format versions, archive flags, block-info metadata, block compression descriptors, and directory node paths. For read-only inspection, decompress block payloads using none/LZ4/LZ4HC and extract directory nodes under a 128 MiB catalog budget. Extracted catalog nodes are checked for Unity SerializedFile type/object tables and class 142 AssetBundle objects; supported Android/iOS layouts expose preload counts, container keys, bundle names and dependency strings. This is inventory only and does not modify catalog bytes.
- Detect Actor IDs and merge supported MSES v7 rows, IFix HeroSkin patches, and supported CommonActions tracks/skin conditions with schema and round-trip checks.
- Preserve unique-path AssetReferenceSet resources byte-for-byte. This does not merge conflicting baseSubset/skinSubset graphs.
- Add new Wwise BNK files byte-for-byte only when the read-only append-only checker validates Wwise version, bank IDs, DIDX media IDs/payload hashes, HIRC object IDs/types/payload hashes, and overlapping platform/locale namespaces.
- Keep the original BNK payload unchanged. No HIRC object or reference remapping is performed.
- Build an output ZIP and read it back for CRC validation before exposing the iOS share sheet.
- Check the latest public GitHub Release for update information.

## Safety limits

- Up to 10 input ZIP files and 500 MiB total compressed input.
- Up to 20,000 entries per ZIP.
- Up to 512 MiB per ZIP entry and 1 GiB declared expanded bytes per ZIP.
- UnityFS input inspection: up to 64 MiB per `.assetbundle` and 256 MiB cumulatively per ZIP scan; catalog-node extraction is capped at 128 MiB expanded and 256 nodes per catalog bundle.
- The Wwise append-only inspector reads BNK files up to 128 MiB each.
- ZIP import runs on a background task; the app retains security-scoped access until the coordinated copy into Documents completes.
- Unsupported or conflicting resources fail closed instead of choosing a winner or overwriting the base file.

## Not yet implemented or verified

- UnityFS AssetBundle rebuilding, LZMA/LZHAM data-block support, and modification of extracted bundle contents. Current extraction is read-only.
- Merging Packer/Verification catalog objects, rewriting dependency IDs, container references or verification checksums, and rebuilding UnityFS. The current build parses supported class 142 AssetBundle fields read-only; unsupported target-platform layouts and catalog-dependent merges remain blocked.
- Semantic merge of AssetReferenceSet `baseSubset`/`skinSubset` graphs when the same AssetRef path has different payloads.
- Wwise HIRC reference-graph parsing/remapping, standalone WEM pairing, external media, unknown Wwise chunks, and same-path BNK replacement.
- Unknown MSES/IFix schemas and unsupported CommonActions condition trees.
- Full VTK package-catalog merge.
- ZIP ↔ `.3105` conversion has not been completed or verified in this native iOS target.
- Physical-iPhone in-game compatibility testing. A structurally valid ZIP does not prove Liên Quân will load the content or preserve every animation, sound, recall, kill notice, and UI element.

## Build and signing

Requires macOS, Xcode, Homebrew, and XcodeGen:

```sh
brew install xcodegen
cd ios/AOVSkinMerger
xcodegen generate
xcodebuild test -project AOVSkinMerger.xcodeproj \
  -scheme AOVSkinMerger \
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
xcodebuild -project AOVSkinMerger.xcodeproj \
  -scheme AOVSkinMerger \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

CI runs the unit-test suite before packaging an unsigned device IPA. A test failure blocks the build. The IPA must be signed with the user's own Apple development identity and provisioning profile, or re-signed with AltStore/Sideloadly, before installation. The app cannot silently replace its own installed IPA.

## App identity

- Home-screen name: `AKI AOV SKIN`.
- Bundle identifier: `com.aki.aovskinmerger`.
- Target: iOS 26.5+.
- App icon: generated from the supplied artwork stored in `IconSource`.
- IPA artifact: `AKI AOV SKIN.ipa`.
