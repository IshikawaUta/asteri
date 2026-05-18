import struct
from .utils import logger

try:
    from asteri import fastparser  # type: ignore

    FAST_PARSER_AVAILABLE = True
except ImportError:
    FAST_PARSER_AVAILABLE = False


class UWSGIHandler:
    """Parser for the uWSGI binary protocol."""

    @staticmethod
    def parse(data):
        """
        Parses uWSGI packet with fast C fallback.
        Header: 4 bytes [modifier1, size_low, size_high, modifier2]
        """
        if FAST_PARSER_AVAILABLE:
            try:
                res = fastparser.parse_uwsgi(data)
                if res is not None:
                    return res
            except Exception as e:
                logger.debug(f"C fastparser failed, falling back: {e}")

        if len(data) < 4:
            return None, None

        modifier1, size, modifier2 = struct.unpack("<BHB", data[:4])

        # Check if we have enough data
        if len(data) < 4 + size:
            return None, None

        var_data = data[4: 4 + size]
        vars_dict = {}

        pos = 0
        while pos < size:
            if pos + 2 > size:
                break
            key_len = struct.unpack("<H", var_data[pos: pos + 2])[0]
            pos += 2
            if pos + key_len > size:
                break
            key = var_data[pos: pos + key_len].decode("latin-1")
            pos += key_len

            if pos + 2 > size:
                break
            val_len = struct.unpack("<H", var_data[pos: pos + 2])[0]
            pos += 2
            if pos + val_len > size:
                break
            val = var_data[pos: pos + val_len].decode("latin-1")
            pos += val_len

            vars_dict[key] = val

        return vars_dict, modifier1

    @staticmethod
    def is_uwsgi(data):
        """Heuristic check for uWSGI protocol."""
        if len(data) < 4:
            return False
        # modifier1 is usually 0 for WSGI
        return data[0] == 0
