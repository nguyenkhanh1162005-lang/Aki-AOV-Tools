import unittest

from app.aov_format_detector import detect_resource_format, summarize_formats


class AOVFormatDetectorTests(unittest.TestCase):
    def test_aov_wrappers(self):
        self.assertEqual(detect_resource_format("BattleBank.bytes", bytes.fromhex("22 4A 67 EF"))[0], "AOV AES + Zstandard")
        self.assertEqual(detect_resource_format("LobbyBank.bytes", bytes.fromhex("22 4A 67 00"))[0], "AOV AES")
        self.assertEqual(detect_resource_format("table.bytes", bytes.fromhex("22 4A 00 EF"))[0], "AOV Zstandard wrapper")

    def test_unity_and_nested_zip(self):
        self.assertEqual(detect_resource_format("hero.assetbundle", b"UnityFS\x00")[0], "Unity AssetBundle")
        self.assertEqual(detect_resource_format("Resources.zip", bytes.fromhex("50 4B 03 04"))[0], "ZIP container")

    def test_audio_and_text(self):
        self.assertEqual(detect_resource_format("sound.bnk", b"BKHD")[0], "Wwise SoundBank")
        self.assertEqual(detect_resource_format("config.xml", b"<?xml version='1.0'?>")[0], "XML/text")

    def test_unknown_bytes_are_not_misclassified(self):
        self.assertEqual(detect_resource_format("unknown.bytes", b"\x00\x01\x02")[0], "Unknown .bytes payload")

    def test_summary_counts_and_limits_examples(self):
        counts, examples = summarize_formats([("Unity AssetBundle", f"file-{i}") for i in range(10)])
        self.assertEqual(counts["Unity AssetBundle"], 10)
        self.assertEqual(len(examples["Unity AssetBundle"]), 8)


if __name__ == "__main__":
    unittest.main()
