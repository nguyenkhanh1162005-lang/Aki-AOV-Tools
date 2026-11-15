# AOV Skin Merger for iOS — prototype 0.1.0

This is an **unsigned iOS device build prototype**, not a finished AoV skin merger.

## What the prototype currently does

- Imports multiple ZIP archives from the iOS Files picker.
- Copies originals into the app sandbox; it does not upload selected archives.
- Reads ZIP central-directory metadata with ZIPFoundation.
- Reports file counts, declared expanded sizes, and paths appearing in more than one source ZIP.
- Rejects ZIPs with unsafe paths, symbolic links, more than 20,000 files, entries larger than 256 MiB, or more than 1 GiB of declared expanded data.
- Checks the latest public GitHub Release for update information.

## Important limitations

- It does not decode AoV `.bytes`, Unity AssetBundles, AssetReferenceSet graphs, MSES, IFix, CommonActions, Wwise banks, or VTK package metadata.
- It does not combine game resources and does not output a game-ready ZIP.
- A repeated ZIP path is only a collision candidate. The prototype does not compare the underlying payload bytes.
- ZIP central-directory inventory is not a full CRC/payload validation.
- Online update checking opens a GitHub release page. iOS apps cannot silently replace their own installed IPA.

The next engineering step is to port the validated AoV parsers/adapters to Swift one at a time and add fixture-based parity tests before enabling any merge operation.

## Build

Requires macOS, Xcode, Homebrew, and XcodeGen:

```sh
brew install xcodegen
cd ios/AOVSkinMerger
xcodegen generate
xcodebuild -project AOVSkinMerger.xcodeproj \
  -scheme AOVSkinMerger \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  CODE_SIGNING_ALLOWED=NO build
```

CI packages the unsigned device `.app` into an `.ipa` artifact. **Unsigned IPA cannot be installed directly**: it must be signed with a provisioning profile through Xcode or re-signed using a sideloading tool such as AltStore/Sideloadly with the user's Apple account. No Apple signing credentials are stored in this repository.
