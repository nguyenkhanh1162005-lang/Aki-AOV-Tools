import unittest

from app.aov_bytes import AOVBytesError, compress_aov_bytes, decompress_aov_bytes


# Small real AoV resource from the Actor 595 package, used as a stable format fixture.
VTK_SAMPLE = bytes.fromhex(
    "224a00ef440d000028b52ffd63e755430b440c8d080063cd2b77a6f0df8879dc03ac97ad2a45a729c92d9187de81758034878b83bda15e6a679534ce961ce1eccb16562dd486f9640d91869633d817aa0bb485c9825c21aad05938a670fb041851b026d4122809730429420ca141e00f6063b957ac955a85529953a407514a47612890517852c375629bd41dca64eac89c88d337346839002c8016d6225406d3360c2868d2c2f908096193962a72396144438c369396ed6d59bb4ab38b6d10d698711d86dd8abd048093a811a037b4ff06c09532cc01821082866008212817be1174fcfe1924f7a43e65d5dfe210613fd391e5b9ffa86f67efb035cd103d3925ad4f3ff03fd998e6934f57463e3f421595544a5c397608a7f5b62402b7e67fa7a00133c1eb7407"
)


class AOVBytesTests(unittest.TestCase):
    def test_decompresses_real_vtk_resource(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(len(decoded), 0x0D44)
        print("DECODED_RESOURCE_PREVIEW:", decoded.decode("utf-8", errors="replace"))

    def test_compress_decompress_round_trip(self):
        decoded = decompress_aov_bytes(VTK_SAMPLE)
        self.assertEqual(decompress_aov_bytes(compress_aov_bytes(decoded)), decoded)

    def test_rejects_unknown_header(self):
        with self.assertRaises(AOVBytesError):
            decompress_aov_bytes(b"not-an-aov-resource")


if __name__ == "__main__":
    unittest.main()
