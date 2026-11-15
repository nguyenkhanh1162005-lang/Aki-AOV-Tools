# AKI AOV SKIN for iOS — prototype 0.4.0

This is an **unsigned iOS device build prototype**, not a finished AoV skin merger.

## Implemented on-device

- Imports multiple ZIP archives from the iOS Files picker and keeps originals in the app sandbox.
- Reads ZIP entries in bounded chunks using ZIPFoundation.
- Reads every payload during inventory, calculates SHA-256 incrementally and validates entry CRC.
- Distinguishes repeated paths with identical payloads from same-path payload conflicts.
- Scans both outer MOD ZIPs and one-level nested \`Resources.zip\` files for AoV \`.bytes\`.
- Ports the verified AoV AES-CBC/PKCS7 path for header \`22 4A 67 00\`, with the filename-derived key, zero IV and exact decoded-size check.
- Reports decoded payload SHA-256 without modifying the source ZIP.
- Checks the latest public GitHub Release for update information.

## Safety limits

- Up to 500 MiB total input ZIP size.
- Up to 20,000 entries per ZIP.
- Up to 256 MiB per ZIP entry and 1 GiB declared expanded bytes per ZIP.
- AES inspection reads \`.bytes\` entries of at most 32 MiB at a time.
- Unsafe paths, symlinks, duplicate paths within one ZIP and failed CRC/payload reads are rejected.

## Explicit limitations

- AoV AES-CBC, AES+Zstandard (\`22 4A 67 EF\`) and standalone AoV Zstandard wrappers are decoded/encoded with the bundled AoV dictionary and round-trip checks.
- MSES v7, IFix and supported CommonActions tracks are parsed and merged with schema/round-trip checks; unknown forms fail closed.
- Wwise BNK files may be added byte-for-byte only when the read-only append-only inspector validates Wwise version, bank ID, DIDX media IDs/payload hashes, HIRC object IDs/types/payload hashes and overlapping platform/locale namespaces. The BNK itself is never rewritten; HIRC references are not remapped.
- Standalone WEM, external-media references, unknown Wwise chunks and same-path BNK payload conflicts remain blocked.
- AssetReferenceSet baseSubset/skinSubset graphs, Unity AssetBundle Packer/Verification metadata and VTK package catalogs are not semantically merged by this build.
- It does not combine game resources and does not output a game-ready ZIP.
- Repeated paths are classified by their content hashes, but no automatic winner is selected for conflicting contents.
- No in-game test or game compatibility claim is made.
- Online update checking opens a GitHub release page. iOS apps cannot silently replace their own installed IPA.

## Build and test

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

CI packages the unsigned device app into an \`.ipa\` artifact. **Unsigned IPA cannot be installed directly**: it must be signed with the user's own Apple development identity and provisioning profile, or re-signed with a sideloading tool such as AltStore/Sideloadly. No Apple signing credentials are stored in this repository.


## App branding 0.4.0

- iOS home-screen display name: `AKI AOV SKIN`.
- App icon is generated from the image supplied by the user at build time.
- The exported artifact is named `AKI AOV SKIN.ipa`.
