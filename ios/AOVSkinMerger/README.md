# AKI AOV SKIN for iOS — prototype 0.5.3 (build 20)

This is an **unsigned iOS device-build prototype**. It is not a universal or game-certified AoV skin merger. The app supports iOS 15.0 and later in one build; CI compiles it with the iOS 26.5 SDK.

## Implemented on-device

- Import multiple MOD packages from the iOS Files picker. The picker accepts `.zip` and `.3105`; `.3105` is supported only when its contents are actually ZIP-compatible. Source files remain in the app sandbox and are not uploaded.
- Inventory ZIP entries, validate CRC, calculate SHA-256, inspect nested `Resources.zip`, validate paths, and compare game version markers.
- Decode and encode the currently supported AoV AES-CBC, AES+Zstandard, and standalone Zstandard wrappers; preserve wrapper mode and require round-trip verification.
- Inspect UnityFS AssetBundle headers, engine/format versions, archive flags, block-info metadata, block compression descriptors, and directory node paths. Extract directory nodes under bounded memory limits and inspect Unity SerializedFile class 142 AssetBundle records plus class 114 Packer/Verification MonoBehaviours.
- Repack UnityFS with verified round-trip support for ordinary bundles and the observed AoV AES-encrypted header + SM4-encrypted block-info form. The correct legacy/current encryption flag is selected from the Unity version; node payloads and flags are rechecked after rewrite. Unsupported encryption schemes remain blocked.
- Build conservative class 142 merge plans that remap local/external PPtrs, append container keys/preload ranges and check dependencies. The CAB batch importer now imports TypeTree-compatible object payloads plus their local dependency closure into an in-memory destination CAB, updates its class 142 record, repacks the enclosing UnityFS bundle and verifies the object/node round-trip. Cross-file runtime catalogs and final RES packaging are still separate stages.
- Build Packer/Verification runtime-catalog plans. A separate executor locates class-114 catalogs inside UnityFS and writes them only when final AssetBundle metadata and checksum values are supplied by verified upstream adapters. Unknown checksum algorithms are never guessed.
- Provide an archive-level RES exporter that accepts already-merged resource payloads, their runtime IFS paths, verified Packer rows and verified ZIP/Databin checksums. It updates both runtime catalogs, rebuilds either a plain RES ZIP or a wrapper with nested Resources.zip, preserves source entry compression where possible, and reopens the output to CRC-check all entries. This is a final packaging stage, not a MOD payload merger by itself.
- Detect Actor IDs and merge supported MSES v7 rows, IFix HeroSkin patches, and supported CommonActions tracks/skin conditions with schema and round-trip checks.
- Preserve unique-path AssetReferenceSet resources byte-for-byte. This does not merge conflicting baseSubset/skinSubset graphs.
- Add new Wwise BNK files byte-for-byte only when the read-only append-only checker validates Wwise version, bank IDs, DIDX media IDs/payload hashes, HIRC object IDs/types/payload hashes, and overlapping platform/locale namespaces.
- Keep the original BNK payload unchanged. No HIRC object or reference remapping is performed.
- Build an output ZIP and read it back for CRC validation before exposing the iOS share sheet.
- Check the latest public GitHub Release for update information.

## Safety limits

- Up to 10 ZIP or ZIP-compatible `.3105` input packages and 500 MiB total compressed input for the standard MOD merge flow.
- Up to 20,000 entries per ZIP.
- Up to 512 MiB per ZIP entry and 1 GiB declared expanded bytes per ZIP.
- UnityFS input inspection: up to 64 MiB per `.assetbundle` and 256 MiB cumulatively per ZIP scan; catalog-node extraction is capped at 128 MiB expanded and 256 nodes per catalog bundle.
- The Wwise append-only inspector reads BNK files up to 128 MiB each.
- ZIP import runs on a background task; the app retains security-scoped access until the coordinated copy into Documents completes.
- Unsupported or conflicting resources fail closed instead of choosing a winner or overwriting the base file.

## Not yet implemented or verified

- End-to-end RMK/RES export orchestration from arbitrary MOD packages: the current CAB batch stage can import supported object closures into in-memory AssetBundle candidates, but the app does not yet combine every candidate with independently verified final Packer entries and authoritative Verification checksums before calling the RES archive exporter.
- The CAB importer is deliberately restricted to supported SerializedFile formats, exact TypeTree compatibility, resolvable local dependency closure and audited external PPtrs. Unsupported or ambiguous graph edges block the candidate; a synthetic round-trip test does not prove the same graph is compatible with a live game RES.
- Read-only cross-build inspection is supported for legacy archives with an older or missing `version.txt`: each present runtime catalog must agree with its own marker, and markerless catalogs must agree with one another. The clean RES must still have a valid version marker. This mode only enables CAB candidate inspection/import analysis; it does not relax the final RES exporter's strict build checks.
- CAB object import is currently restricted to Unity SerializedFile formats 22–30 with matching source/destination format and target platform. Older formats can be inventoried and compared, but their object writer is not implemented and the CAB import candidate is blocked before mutation.
- Real-RES validation of class 142 pointer remapping, external target pathIDs, runtime catalog checksums and output compatibility. Synthetic exporter tests cover plain and nested RES ZIP packaging, plus Packer/Verification writes inside encrypted AOV UnityFS; the Dropbox clean RES bytes have not been parsed in this environment.
- LZHAM and unsupported UnityFS layouts. Unsupported layouts must remain blocked instead of being skipped or partially rewritten.
- Semantic merge of AssetReferenceSet `baseSubset`/`skinSubset` graphs when the same AssetRef path has different payloads.
- Wwise HIRC reference-graph parsing/remapping, standalone WEM pairing, external media, unknown Wwise chunks, and same-path BNK replacement.
- Unknown MSES/IFix schemas and unsupported CommonActions condition trees.
- Full VTK package-catalog merge and generic AssetReferenceSet subset graph union.
- Proprietary non-ZIP `.3105` decoding and ZIP ↔ `.3105` conversion have not been implemented or verified. The new `.3105` support is extension/picker integration for ZIP-compatible packages, not a decoder for an unknown binary container.
- Physical-iPhone in-game compatibility testing. A structurally valid ZIP does not prove Liên Quân will load the content or preserve every animation, sound, recall, kill notice, and UI element.

## Build and signing

The deployment target is iOS 15.0. `SWCompression` is pinned to 4.8.7 because 4.9.x raises its minimum iOS deployment target to 17.0; the 4.8.7 LZMA API used by the UnityFS parser is compatible with the current call sites.

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
- Target: iOS 15.0+ (one IPA supports all iOS versions from this minimum upward).
- App icon: generated from the supplied artwork stored in `IconSource`.
- IPA artifact: `AKI AOV SKIN.ipa`.
