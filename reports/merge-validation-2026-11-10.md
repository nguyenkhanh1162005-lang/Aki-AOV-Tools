# Actual archive merge validation — 2026-11-10

## Inputs
- `IOS-Pack 10 Skin-Nút Bấm Thông Báo Hạ.zip`
- `Part1.zip`
- `Part2.zip`
- `Part3.zip`

All four source ZIPs passed `ZipFile.testzip()`.

## Generated candidate
- Output: `Aki-AOV-10Skin-VanTamKiem.zip`
- Size: 150,793,052 bytes
- SHA-256: `5e54dbf66ed045d192efb09d7e7afdaa90b02aed2c1c70a3e32fd936645a049d`
- Outer ZIP: 3 entries, CRC passed
- Nested `Resources.zip`: 83 entries, CRC passed
- All 59 base resource paths retained
- 24 VTK resource files added
- 8 base resource paths intentionally rebuilt: HeroSkin_1 IFix, CommonActions, four MSES tables, ResourcePackerInfoSet, and ResourceVerificationInfoSet

## Structural checks
- IFix: 17 FixInfo and 25 methods; parse and round-trip passed
- CommonActions: 6 XML files merged; 38 VTK tracks appended; required skin IDs 59500/59501 verified by the merge routine
- MSES outputs passed checksum validation:
  - ResSkinSeniorLabelCfg: 69 fields / 54 rows
  - BattleBank: 59 fields / 1,932 rows
  - LobbyBank: 61 fields / 888 rows
  - skillcombine: 316 fields / 14,613 rows
- All 10 expected Actor 595 AssetBundles were present and readable by the AoV Unity parser
- PackerInfo and ResourceVerificationInfo remained at base version 1.64.1.12
- Seven iOS soundbank files were included; Android soundbanks were excluded
- The web AOV-merge endpoint handler returned a ZIP FileResponse for the same four real inputs

## Not verified
This is a structurally validated candidate, not a game-certified package. No installation or in-game test was possible in this environment. Recall animation, kill notifications, UI buttons, effects, and sound behavior still require testing in Liên Quân iOS 1.64.1.
