import os
import sys
import struct
import socket
import threading
from asteri.utils import import_app, logger

# =====================================================================
# 1. TLV Binary Encoder / Decoder
# =====================================================================
class TLV:
    """Helper for Type-Length-Value binary encoding and decoding."""
    
    @staticmethod
    def encode(t: int, v: bytes) -> bytes:
        """Encode type (2 bytes) and length (4 bytes) followed by value."""
        return struct.pack(">HI", t, len(v)) + v

    @staticmethod
    def decode(data: bytes):
        """Decode a single TLV packet from data stream.
        Returns: (type, value_bytes, remaining_bytes) or (None, None, data) if incomplete.
        """
        if len(data) < 6:
            return None, None, data
        
        t, length = struct.unpack(">HI", data[:6])
        if len(data) < 6 + length:
            return None, None, data
        
        v = data[6:6+length]
        return t, v, data[6+length:]


# =====================================================================
# 2. Shared Stash Server & Client (IPC Key-Value Memory)
# =====================================================================
OP_SET = 1
OP_GET = 2
OP_DELETE = 3
OP_SUCCESS = 101
OP_NOT_FOUND = 102
OP_ERROR = 103

class StashServer:
    """A lightweight shared memory key-value server running in the Arbiter."""
    
    def __init__(self, address):
        self.address = address
        self.data = {}
        self.server_sock = None
        self.thread = None
        self.running = False

    def start(self):
        self.running = True
        if isinstance(self.address, str):
            # Unix Domain Socket
            if os.path.exists(self.address):
                try:
                    os.unlink(self.address)
                except OSError:
                    pass
            self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            # TCP Socket (host, port)
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        self.server_sock.bind(self.address)
        self.server_sock.listen(128)
        self.server_sock.settimeout(1.0)
        
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.server_sock:
            try:
                self.server_sock.close()
            except OSError:
                pass
        if isinstance(self.address, str) and os.path.exists(self.address):
            try:
                os.unlink(self.address)
            except OSError:
                pass

    def _run_loop(self):
        while self.running:
            try:
                client, _ = self.server_sock.accept()
                threading.Thread(target=self._handle_client, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_client(self, client):
        client.settimeout(5.0)
        buffer = b""
        try:
            while self.running:
                data = client.recv(4096)
                if not data:
                    break
                buffer += data
                
                while True:
                    t, val, remaining = TLV.decode(buffer)
                    if t is None:
                        break
                    buffer = remaining
                    
                    response = self._process_request(t, val)
                    client.sendall(response)
        except OSError:
            pass
        finally:
            try:
                client.close()
            except OSError:
                pass

    def _process_request(self, op: int, payload: bytes) -> bytes:
        try:
            if op == OP_SET:
                kt, k_bytes, rem = TLV.decode(payload)
                if kt is None: return TLV.encode(OP_ERROR, b"Invalid SET key")
                vt, v_bytes, _ = TLV.decode(rem)
                if vt is None: return TLV.encode(OP_ERROR, b"Invalid SET value")
                
                key = k_bytes.decode('utf-8')
                self.data[key] = v_bytes
                return TLV.encode(OP_SUCCESS, b"")
                
            elif op == OP_GET:
                key = payload.decode('utf-8')
                if key in self.data:
                    return TLV.encode(OP_SUCCESS, self.data[key])
                return TLV.encode(OP_NOT_FOUND, b"")
                
            elif op == OP_DELETE:
                key = payload.decode('utf-8')
                if key in self.data:
                    del self.data[key]
                    return TLV.encode(OP_SUCCESS, b"")
                return TLV.encode(OP_NOT_FOUND, b"")
                
            return TLV.encode(OP_ERROR, b"Unknown operation")
        except Exception as e:
            return TLV.encode(OP_ERROR, str(e).encode('utf-8'))


class StashClient:
    """Client interface for workers to interact with StashServer."""
    
    def __init__(self, address):
        self.address = address

    def _send_request(self, op: int, payload: bytes):
        if isinstance(self.address, str):
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        else:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        
        try:
            sock.connect(self.address)
            sock.sendall(TLV.encode(op, payload))
            
            buffer = b""
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buffer += data
                t, val, _ = TLV.decode(buffer)
                if t is not None:
                    return t, val
            return OP_ERROR, b"No response"
        except OSError as e:
            return OP_ERROR, str(e).encode('utf-8')
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def set(self, key: str, value: bytes) -> bool:
        k_bytes = key.encode('utf-8')
        payload = TLV.encode(1, k_bytes) + TLV.encode(2, value)
        t, val = self._send_request(OP_SET, payload)
        return t == OP_SUCCESS

    def get(self, key: str) -> bytes:
        t, val = self._send_request(OP_GET, key.encode('utf-8'))
        if t == OP_SUCCESS:
            return val
        return None

    def delete(self, key: str) -> bool:
        t, val = self._send_request(OP_DELETE, key.encode('utf-8'))
        return t == OP_SUCCESS


# =====================================================================
# 3. Dynamic Dirty App Loader (WSGI & ASGI Wrapper)
# =====================================================================
class DirtyAppLoader:
    """Dynamic multi-app router that delegates requests to multiple apps based on Host header or Path."""
    
    def __init__(self, mapping_str: str):
        self.routes = {}
        self.loaded_apps = {}
        self._parse_mapping(mapping_str)

    def _parse_mapping(self, mapping_str: str):
        if not mapping_str:
            return
        
        parts = mapping_str.split(",")
        for part in parts:
            if "=" not in part:
                continue
            pattern, app_str = part.strip().split("=", 1)
            pattern = pattern.strip()
            app_str = app_str.strip()
            
            if pattern.startswith("/"):
                self.routes[("path", pattern)] = app_str
            elif pattern == "default":
                self.routes[("default", "")] = app_str
            else:
                self.routes[("host", pattern)] = app_str

    def _match_app(self, host: str, path: str):
        if host:
            clean_host = host.split(":")[0]
            for (rtype, rpat), app_str in self.routes.items():
                if rtype == "host" and rpat == clean_host:
                    return app_str
        
        for (rtype, rpat), app_str in self.routes.items():
            if rtype == "path" and path.startswith(rpat):
                return app_str
        
        default_app = self.routes.get(("default", ""))
        if default_app:
            return default_app
            
        if self.routes:
            return list(self.routes.values())[0]
            
        return None

    def _get_app(self, app_str: str):
        if not app_str:
            return None
        if app_str not in self.loaded_apps:
            self.loaded_apps[app_str] = import_app(app_str)
        return self.loaded_apps[app_str]

    def __call__(self, arg1, arg2, arg3=None):
        if arg3 is not None:
            # ASGI mode
            return self.asgi_call(arg1, arg2, arg3)
        else:
            # WSGI mode
            return self.wsgi_call(arg1, arg2)

    def wsgi_call(self, environ, start_response):
        host = environ.get("HTTP_HOST", "")
        path = environ.get("PATH_INFO", "")
        
        app_str = self._match_app(host, path)
        app = self._get_app(app_str)
        
        if app is None:
            status = "404 Not Found"
            headers = [("Content-Type", "text/plain")]
            start_response(status, headers)
            return [b"No dynamic app routed for host/path"]
        
        return app(environ, start_response)

    async def asgi_call(self, scope, receive, send):
        if scope["type"] == "http":
            host = ""
            for k, v in scope.get("headers", []):
                if k == b"host":
                    host = v.decode('utf-8')
                    break
            path = scope.get("path", "")
            
            app_str = self._match_app(host, path)
            app = self._get_app(app_str)
            
            if app is None:
                await send({
                    "type": "http.response.start",
                    "status": 404,
                    "headers": [(b"content-type", b"text/plain")]
                })
                await send({
                    "type": "http.response.body",
                    "body": b"No dynamic app routed for host/path",
                    "more_body": False
                })
                return
            
            await app(scope, receive, send)
        else:
            app_str = self.routes.get(("default", "")) or (list(self.routes.values())[0] if self.routes else None)
            app = self._get_app(app_str)
            if app:
                await app(scope, receive, send)
