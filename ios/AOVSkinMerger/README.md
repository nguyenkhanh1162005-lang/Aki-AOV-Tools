# AKI AOV SKIN for iOS — prototype 0.4.2

This is an **unsigned iOS device-build prototype**. It is not a universal or game-certified AoV skin merger.

## Implemented on-device

- Import multiple MOD ZIP archives from the iOS Files picker. Source files remain in the app sandbox and are not uploaded.
- Inventory ZIP entries, validate CRC, calculate SHA-256, check nested \`Resources.zip\`, validate paths, and match game version markers.
- Decode and encode the currently supported AoV AES-CBC, AES+Zstandard, and standalone Zstandard wrappers; preserve wrapper mode and require round-trip verification.
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
- The Wwise append-only inspector reads BNK files up to 128 MiB each.
- ZIP import runs on a background task; the app retains security-scoped access until the coordinated copy into Documents completes.
- Unsupported or conflicting resources fail closed instead of choosing a winner or overwriting the base file.

## Not yet implemented or verified

- Semantic merge of AssetReferenceSet \`baseSubset\`/\`skinSubset\` graphs when the same AssetRef path has different payloads.
- Unity AssetBundle \`ResourcePackerInfoSetAll\` and \`ResourceVerificationInfoSetAll\` catalog/dependency/checksum merge. New catalog-dependent AssetBundles/Databin resources remain blocked.
- Wwise HIRC reference-graph parsing/remapping, standalone WEM pairing, external media, unknown Wwise chunks, and same-path BNK replacement.
- Unknown MSES/IFix schemas and unsupported CommonActions condition trees.
- Full VTK package-catalog merge.
- Physical-iPhone in-game compatibility testing. A structurally valid ZIP does not prove Liên Quân will load the content or preserve every animation, sound, recall, kill notice, and UI element.

## Build and signing

Requires macOS, Xcode, Homebrew, and XcodeGen:

\`\`\`sh
brew install xcodegen
cd ios/AOVSkinMerger
xcodegen generate
xcodebuild test -project AOVSkinMerger.xcodeproj \\
  -scheme AOVSkinMerger \\
  -destination 'platform=iOS Simulator,name=iPhone 17 Pro'
xcodebuild -project AOVSkinMerger.xcodeproj \\
  -scheme AOVSkinMerger \\
  -configuration Release \\
  -sdk iphoneos \\
  -destination 'generic/platform=iOS' \\
  CODE_SIGNING_ALLOWED=NO build
\`\`\`

CI packages an unsigned device IPA. It must be signed with the user's own Apple development identity and provisioning profile, or re-signed with AltStore/Sideloadly, before installation. The app cannot silently replace its own installed IPA.

## App identity

- Home-screen name: \`AKI AOV SKIN\`.
- App icon: generated from the supplied artwork stored in \`IconSource\`.
- IPA artifact: \`AKI AOV SKIN.ipa\`.
