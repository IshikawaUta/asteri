import unittest
from asteri.dirty import TLV


class TestDirtyTLV(unittest.TestCase):
    def test_encode_decode_success(self):
        encoded = TLV.encode(1, b"hello")
        self.assertEqual(len(encoded), 11)  # 2 type + 4 len + 5 payload

        t, val, rem = TLV.decode(encoded)
        self.assertEqual(t, 1)
        self.assertEqual(val, b"hello")
        self.assertEqual(rem, b"")

    def test_incomplete_packet(self):
        # Very short packet (less than header size)
        t, val, rem = TLV.decode(b"\x00\x01")
        self.assertIsNone(t)
        self.assertEqual(rem, b"\x00\x01")

        # Missing payload part
        encoded = TLV.encode(5, b"verylongpayload")
        t, val, rem = TLV.decode(encoded[:10])
        self.assertIsNone(t)
        self.assertEqual(rem, encoded[:10])

    def test_multiple_packets(self):
        encoded1 = TLV.encode(1, b"first")
        encoded2 = TLV.encode(2, b"second")

        t1, val1, rem1 = TLV.decode(encoded1 + encoded2)
        self.assertEqual(t1, 1)
        self.assertEqual(val1, b"first")

        t2, val2, rem2 = TLV.decode(rem1)
        self.assertEqual(t2, 2)
        self.assertEqual(val2, b"second")
        self.assertEqual(rem2, b"")


if __name__ == "__main__":
    unittest.main()
