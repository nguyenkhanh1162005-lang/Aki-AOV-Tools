import unittest

from app.aov_bytes import (AOVBytesError, compress_aov_bytes, decompress_aov_bytes,\n                           decrypt_aes_resource, encrypt_aes_resource)


# Small real AoV resource from the Actor 595 package, used as a stable format fixture.
VTK_SAMPLE = bytes.fromhex(
    "224a00ef440d000028b52ffd63e755430b440c8d080063cd2b77a6f0df8879dc03ac97ad2a45a729c92d9187de81758034878b83bda15e6a679534ce961ce1eccb16562dd486f9640d91869633d817aa0bb485c9825c21aad05938a670fb041851b026d4122809730429420ca141e00f6063b957ac955a85529953a407514a47612890517852c375629bd41dca64eac89c88d337346839002c8016d6225406d3360c2868d2c2f908096193962a72396144438c369396ed6d59bb4ab38b6d10d698711d86dd8abd048093a811a037b4ff06c09532cc01821082866008212817be1174fcfe1924f7a43e65d5dfe210613fd391e5b9ffa86f67efb035cd103d3925ad4f3ff03fd998e6934f57463e3f421595544a5c397608a7f5b62402b7e67fa7a00133c1eb7407"
)


AES_SAMPLE = bytes.fromhex(\n    "224a6700cd0200008791646bd02a34c49fa5c166879dec16dc4de77e247574d27d9e6319c432b8faad1861c5a9f0d7bf09fba0bff12227640b76ac4990bbaac0a1b3b63b951728e06fe8a65d3ddc365932672f8b06f14ecd4fadc930e53653edede161b3506d26c85c27fbc232422703660a7c48df48261e0d1ceee130d3d2af26e3672621c96e197ba4c873f613c7c9fe2bcfab9e272beeac45097685002abf229754f9effb6122b8c94e40c2fc4b4e45870ec9827bea068ff97fc593ce6510b2af71d966a29ab0059632d5f1f72d7123d387576268ec859a39ae9d045af2588ae4489e5c15b68ccf4e0dde023ea30c2bdc457870286f8f1f327e7ccf0dd1a7728211f2128281f8817a4cd6c78a18b3e51dadf966567770ad0d029d625316ca695c832c47752d89fcb09e7798e1c336084288f85deefdc01b58798c042b44e8359ac66697f057746f4f1ece5eb91521a5892fb26a7fa199e2878cf624682c1bf39fe53751e0a2d3af3a8d889d59dff0c709ab0a34bd6ae9e52abccf093f60b55ac25b917d1ddac542268206e977c08be18a0353f7956a26c9f74d914e5538d5e00ecdd86de4dc12e4c3039d47543a8f005dae07880c24eef8cde91229bbde31b41f12202e08627310581dfc38c80793720f0e5b109d7b2573351f30fb95b15fff0efa1c6420b578d12e781c8277615a957cb52afa16454d0816d34a56bc04d6e00788c262a4a9acf0767b2363d21dbf39c4cdd157c479c22c4207bc89a904744bcba0fc5c4b9b210b56be3186fbab58044f0932a1fabc214461ad26f9b99e684ffde2518707936061e49712689779e0f95da15969b75ffbf27e6c02cadfd717ea1bac1dbfd6be59823c08b64993df57aef0b3240fe5b9a154a567e9559c048b67e0cc1a0b71e26294aa9552ee846c22efeff0ac613f9c5c607d05a8327d3d7468eacceb510d6dcf998601c2d358175440d9959a87abb3d065b0dbf442afba593a1cd4e722f2b0bb47d64f1e7823ff568d364fbc904efdfc"\n)\n\n\nclass AOVBytesTests(unittest.TestCase):
    def test_decompresses_real_vtk_resource(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(len(decoded), 0x0D44)
        self.assertEqual(len(decoded), 3396)

    def test_compress_decompress_round_trip(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(decompress_aov_bytes(compress_aov_bytes(decoded)), decoded)

    def test_decrypts_and_reencrypts_real_vtk_aes_resource(self):\n        decoded = decrypt_aes_resource(AES_SAMPLE, "ResSkinSeniorLabelCfg")\n        self.assertEqual(len(decoded), 717)\n        self.assertEqual(decrypt_aes_resource(encrypt_aes_resource(decoded, "ResSkinSeniorLabelCfg"), "ResSkinSeniorLabelCfg"), decoded)\n\n    def test_rejects_unknown_header(self):
        with self.assertRaises(AOVBytesError):
            decompress_aov_bytes(b"not-an-aov-resource")


if __name__ == "__main__":
    unittest.main()
