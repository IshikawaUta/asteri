import unittest
import struct
from asteri.uwsgi import UWSGIHandler

class TestUWSGI(unittest.TestCase):
    def test_is_uwsgi(self):
        # Starts with 0
        self.assertTrue(UWSGIHandler.is_uwsgi(b"\x00\x00\x00\x00"))
        # Doesn't start with 0
        self.assertFalse(UWSGIHandler.is_uwsgi(b"\x01\x00\x00\x00"))
        # Too short
        self.assertFalse(UWSGIHandler.is_uwsgi(b"\x00"))

    def test_parse_valid_packet(self):
        # Construct key-value data:
        # Key 1: "KEY1" -> len 4
        # Val 1: "VAL1" -> len 4
        # Key 2: "TEST" -> len 4
        # Val 2: "OK"   -> len 2
        var_data = (
            struct.pack("<H", 4) + b"KEY1" + struct.pack("<H", 4) + b"VAL1" +
            struct.pack("<H", 4) + b"TEST" + struct.pack("<H", 2) + b"OK"
        )
        size = len(var_data)
        modifier1 = 0
        modifier2 = 0
        header = struct.pack("<BHB", modifier1, size, modifier2)
        packet = header + var_data

        vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertEqual(mod, modifier1)
        self.assertEqual(vars_dict, {
            "KEY1": "VAL1",
            "TEST": "OK"
        })

    def test_parse_truncated_packet(self):
        # Header specifies size of 10, but we only supply 2 bytes of body
        header = struct.pack("<BHB", 0, 10, 0)
        packet = header + b"12"
        vars_dict, mod = UWSGIHandler.parse(packet)
        self.assertIsNone(vars_dict)
        self.assertIsNone(mod)

        # Packet too small even for header
        vars_dict, mod = UWSGIHandler.parse(b"\x00\x00")
        self.assertIsNone(vars_dict)
        self.assertIsNone(mod)

if __name__ == "__main__":
    unittest.main()
