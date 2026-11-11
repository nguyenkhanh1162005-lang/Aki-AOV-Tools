import unittest

from app.aov_bytes import (
    AOVBytesError,
    compress_aov_bytes,
    decode_aov_resource,
    decompress_aov_bytes,
    decrypt_aes_resource,
    encrypt_aes_resource,
    encrypt_aes_zstd_resource,
    encode_aov_resource,
    _compress_frame,
    _decompress_frame,
)


# Small real AoV resource from the Actor 595 package, used as a stable format fixture.
VTK_SAMPLE = bytes.fromhex(
    "224a00ef440d000028b52ffd63e755430b440c8d080063cd2b77a6f0df8879dc03ac97ad2a45a729c92d9187de81758034878b83bda15e6a679534ce961ce1eccb16562dd486f9640d91869633d817aa0bb485c9825c21aad05938a670fb041851b026d4122809730429420ca141e00f6063b957ac955a85529953a407514a47612890517852c375629bd41dca64eac89c88d337346839002c8016d6225406d3360c2868d2c2f908096193962a72396144438c369396ed6d59bb4ab38b6d10d698711d86dd8abd048093a811a037b4ff06c09532cc01821082866008212817be1174fcfe1924f7a43e65d5dfe210613fd391e5b9ffa86f67efb035cd103d3925ad4f3ff03fd998e6934f57463e3f421595544a5c397608a7f5b62402b7e67fa7a00133c1eb7407"
)


AES_SAMPLE = bytes.fromhex(
    "224a6700cd0200008791646bd02a34c49fa5c166879dec16dc4de77e247574d27d9e6319c432b8faad1861c5a9f0d7bf09fba0bff12227640b76ac4990bbaac0a1b3b63b951728e06fe8a65d3ddc365932672f8b06f14ecd4fadc930e53653edede161b3506d26c85c27fbc232422703660a7c48df48261e0d1ceee130d3d2af26e3672621c96e197ba4c873f613c7c9fe2bcfab9e272beeac45097685002abf229754f9effb6122b8c94e40c2fc4b4e45870ec9827bea068ff97fc593ce6510b2af71d966a29ab0059632d5f1f72d7123d387576268ec859a39ae9d045af2588ae4489e5c15b68ccf4e0dde023ea30c2bdc457870286f8f1f327e7ccf0dd1a7728211f2128281f8817a4cd6c78a18b3e51dadf966567770ad0d029d625316ca695c832c47752d89fcb09e7798e1c336084288f85deefdc01b58798c042b44e8359ac66697f057746f4f1ece5eb91521a5892fb26a7fa199e2878cf624682c1bf39fe53751e0a2d3af3a8d889d59dff0c709ab0a34bd6ae9e52abccf093f60b55ac25b917d1ddac542268206e977c08be18a0353f7956a26c9f74d914e5538d5e00ecdd86de4dc12e4c3039d47543a8f005dae07880c24eef8cde91229bbde31b41f12202e08627310581dfc38c80793720f0e5b109d7b2573351f30fb95b15fff0efa1c6420b578d12e781c8277615a957cb52afa16454d0816d34a56bc04d6e00788c262a4a9acf0767b2363d21dbf39c4cdd157c479c22c4207bc89a904744bcba0fc5c4b9b210b56be3186fbab58044f0932a1fabc214461ad26f9b99e684ffde2518707936061e49712689779e0f95da15969b75ffbf27e6c02cadfd717ea1bac1dbfd6be59823c08b64993df57aef0b3240fe5b9a154a567e9559c048b67e0cc1a0b71e26294aa9552ee846c22efeff0ac613f9c5c607d05a8327d3d7468eacceb510d6dcf998601c2d358175440d9959a87abb3d065b0dbf442afba593a1cd4e722f2b0bb47d64f1e7823ff568d364fbc904efdfc"
)


LUA_AES_ZSTD_SAMPLE = bytes.fromhex(
    "224a67ef9d080000b1f3b6f8a17c99bc6c67d9499bc699e35496ac788a398d001f7f4122ce1876b7e170dcbcadda01472f7c720eaa470f5520cd4cc5a077b8a5290cdea811c999ab893f62c12dcc88d10fbd41e241de9dcb05827ea22f806654d4a2e11ea84ac28d678920e8966eb023c96edd87b42aa8523d152b7d225bfba0a8d64417ace4b75bbb10788c26bdae4cee27469334f996a03576137fa36cb65960268710ec830a9625d3204ffc24423b140545015590b5164f2cf10a9d02d0a3900338a6ce83234b84bbff63c80e5b599767abbef17936d7f42666cc0d0e2c099b85b068137f1ab465611f3c7c9815da6e5758dc7310026ef79ccf7d6acaacfb19c83f608f9457b9d1c18ad5cb158b3be3463e08d07d75a11f8b8f9dff23ae2b7ba4632ef8ee9d5bb40497cd3745c8f8f1f996fc6cd1538e2063c4530ef7cd57b5957f0d381c72dfe4801a6a63f0aa0ada609c01d2223a1efb3cbd5eed1c64178c6c6d1cc7f6ee17db98b2c4f7ddf2d6a937b1eb237d51deafc5be2007091a365687037bd50e672a19390e0cd8ce4f9d967c1473fdcdedd6c008a80bbbcd6c3148322fb335ca0bb5a5b604add9615a3ee355ba222abf85a4457a616eeac7dc65ceccac51d72cd12e05e261944ec5a068bf03457caa7a832eaead39b694c29d5b8c7c392aaeeed4f0b0ca9ae5549cf27c4229101ebe86e0411c639872a77925739d9ecbc5dc5ccae1448945dc59aa04c40b9288580939f49c1118fbbe1ebdbec73146544cba045f85eb54f0224f1af91a07e86a28872156a28ee24f822888c144a414137a0b0c097961b8613fbcecadc332b16120ee70895233dac9a7cf01c0730a130069094f7a9d1439ab5fb006b2ac8c2fd6daa6e0de67d37f9fb93904d9d6aec85cb5e91a754a57ce9f021410e35cdb99b5d532484d8c3f89236b2f8d903acd6c42e30d3fc9960b1bfb736b745bdf3fb20ef37dd016e41b7bf99aa5e9e2d74b396b9dd8691bcc79ab2565d42d72b48130fbcd1758accffe0d7f379c53427cc0694f8cce575441e317e44f5f79bfe2b25431840c9f27e37873c0a271e272cd244015d48fe187d2b5d6377b98c15cd6ba04f596731229ab4115dde36bfe8258e2e329fdf6b459cd81f79c0be1b1662e972c522845496950bb9081e6ec5f45d607cc69d3c41363edca20303ff164eb77040627527c016c157fe703707749b8c20c00e3e95f832adbf572cca96179acac6b703d0a2d229a0c1baf5aac15bc39dfefbdb97eea04d4cbfdc3a6486b6b5637d00b64da03907720c32e056c5a81d721004e6bde71e3ace13ea0397aad5eed8ef280f2ad0c6a69ce3019c9de5606209e4b424122f78422a4b703fb65ded324caf8cf7f836bfc99cfbabde40a0aa35c1f9b348d9679c462ee5ee46a3eca145d87032662b92daf113f004050a9ffdd2e2e7e5aad67c6cdf3f7194e3e3cc66414379d89ba34fde34e7a4b8803d0a5745c2a96211bac813cd6247c0178d7334b3b3d4e9686f48b9a3e0a66e25d4c43df2b4c0619557e0e8fc320"
)


class AOVBytesTests(unittest.TestCase):
    def test_decompresses_real_vtk_resource(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(len(decoded), 3396)

    def test_compress_decompress_round_trip(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(decompress_aov_bytes(compress_aov_bytes(decoded)), decoded)

    def test_decrypts_and_reencrypts_real_vtk_aes_resource(self):
        decoded = decrypt_aes_resource(AES_SAMPLE, "ResSkinSeniorLabelCfg")
        self.assertEqual(len(decoded), 717)
        self.assertEqual(
            decrypt_aes_resource(encrypt_aes_resource(decoded, "ResSkinSeniorLabelCfg"), "ResSkinSeniorLabelCfg"),
            decoded,
        )

    def test_aes_zstd_round_trip(self):
        original = (b"MSES\x07\x00\x00\x00" + b"test-resource-" * 200)
        encoded = encrypt_aes_zstd_resource(original, "111_AssetRef")
        decoded, mode = decode_aov_resource(encoded, "111_AssetRef.bytes")
        self.assertEqual(mode, "aes+zstd")
        self.assertEqual(decoded, original)

    def test_decodes_aes_wrapped_zstd_lua_resource(self):
        decoded, mode = decode_aov_resource(LUA_AES_ZSTD_SAMPLE, "Independent_lua.bytes")
        self.assertEqual(mode, "aes+zstd")
        self.assertEqual(len(decoded), 2205)
        rebuilt = encode_aov_resource(decoded, "Independent_lua.bytes", mode)
        self.assertEqual(decode_aov_resource(rebuilt, "Independent_lua.bytes")[0], decoded)

    def test_streaming_zstd_decompression_enforces_output_limit(self):
        import app.aov_bytes as aov_bytes
        from unittest.mock import patch

        with patch.object(aov_bytes, "MAX_DECODED_RESOURCE", 64):
            self.assertEqual(_decompress_frame(_compress_frame(b"A" * 64)), b"A" * 64)
            with self.assertRaisesRegex(AOVBytesError, "vượt giới hạn 256 MiB"):
                _decompress_frame(_compress_frame(b"A" * 65))

    def test_rejects_unknown_header(self):
        with self.assertRaises(AOVBytesError):
            decompress_aov_bytes(b"not-an-aov-resource")


if __name__ == "__main__":
    unittest.main()
