import unittest
import struct
from asteri.http import HTTPParser, FAST_PARSER_AVAILABLE
from asteri.uwsgi import UWSGIHandler


class TestFastParser(unittest.TestCase):
    def test_c_extension_availability(self):
        # Assert that the C-Extension is compiled and active on this environment
        self.assertTrue(
            FAST_PARSER_AVAILABLE,
            "C fastparser extension should be compiled and active.",
        )

    def test_http_parser_success(self):
        raw = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\nContent-Length: 12\r\nUser-Agent: test\r\n\r\nHello World!"

        # Test C-Extension parser
        req = HTTPParser.parse(raw)
        self.assertIsNotNone(req)
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.path, "/index.html")
        self.assertEqual(req.version, "HTTP/1.1")
        self.assertEqual(req.headers["host"], "localhost")
        self.assertEqual(req.headers["content-length"], "12")
        self.assertEqual(req.headers["user-agent"], "test")
        self.assertEqual(req.body, b"Hello World!")

    def test_http_parser_case_insensitivity(self):
        raw = b"POST /submit HTTP/1.1\r\nCONTENT-TYPE: application/json\r\nX-Custom: value\r\n\r\n{}"
        req = HTTPParser.parse(raw)
        self.assertIsNotNone(req)
        self.assertEqual(req.method, "POST")
        self.assertEqual(req.path, "/submit")
        self.assertIn("content-type", req.headers)
        self.assertEqual(req.headers["content-type"], "application/json")
        self.assertEqual(req.headers["x-custom"], "value")

    def test_http_parser_malformed(self):
        # Invalid request line space separation
        req1 = HTTPParser.parse(b"GET_BAD_FORMAT")
        self.assertIsNone(req1)

        # Invalid request line space separation with body
        req2 = HTTPParser.parse(b"GET_BAD_FORMAT\r\n\r\nbody")
        self.assertIsNone(req2)

    def test_uwsgi_parser_success(self):
        # Build uWSGI packet manually
        # Vars: {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/'}
        vars_dict = {"REQUEST_METHOD": "GET", "PATH_INFO": "/"}

        # Serialize uWSGI format:
        # Each var: [key_len_low, key_len_high, key_bytes, val_len_low, val_len_high, val_bytes]
        var_data = b""
        for k, v in vars_dict.items():
            kb = k.encode("latin-1")
            vb = v.encode("latin-1")
            var_data += struct.pack("<H", len(kb)) + kb
            var_data += struct.pack("<H", len(vb)) + vb

        header = struct.pack("<BHB", 0, len(var_data), 0)
        packet = header + var_data

        # Parse packet using our fast C fallback parser
        parsed_vars, modifier = UWSGIHandler.parse(packet)
        self.assertEqual(modifier, 0)
        self.assertEqual(parsed_vars["REQUEST_METHOD"], "GET")
        self.assertEqual(parsed_vars["PATH_INFO"], "/")

    def test_uwsgi_parser_malformed(self):
        # Short packet
        vars_dict, modifier = UWSGIHandler.parse(b"\x00\x00")
        self.assertIsNone(vars_dict)

        # Size mismatch
        vars_dict, modifier = UWSGIHandler.parse(b"\x00\x05\x00\x00\x00\x00")
        self.assertIsNone(vars_dict)

    def test_pure_python_fallback_verification(self):
        # We temporarily disable C extension globally in imports to test the Pure-Python fallback parser
        import asteri.http as http_module
        import asteri.uwsgi as uwsgi_module

        old_http_flag = http_module.FAST_PARSER_AVAILABLE
        old_uwsgi_flag = uwsgi_module.FAST_PARSER_AVAILABLE

        try:
            http_module.FAST_PARSER_AVAILABLE = False
            uwsgi_module.FAST_PARSER_AVAILABLE = False

            # Verify Pure-Python HTTP Parser
            raw = b"GET /index.html HTTP/1.1\r\nHost: localhost\r\n\r\n"
            req = HTTPParser.parse(raw)
            self.assertIsNotNone(req)
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.headers["host"], "localhost")

            # Verify Pure-Python uWSGI Parser
            vars_dict = {"PATH_INFO": "/fallback"}
            var_data = b""
            for k, v in vars_dict.items():
                kb = k.encode("latin-1")
                vb = v.encode("latin-1")
                var_data += struct.pack("<H", len(kb)) + kb
                var_data += struct.pack("<H", len(vb)) + vb

            header = struct.pack("<BHB", 0, len(var_data), 0)
            packet = header + var_data
            parsed_vars, modifier = UWSGIHandler.parse(packet)
            self.assertEqual(parsed_vars["PATH_INFO"], "/fallback")

        finally:
            http_module.FAST_PARSER_AVAILABLE = old_http_flag
            uwsgi_module.FAST_PARSER_AVAILABLE = old_uwsgi_flag


if __name__ == "__main__":
    unittest.main()
