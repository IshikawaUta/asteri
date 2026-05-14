import socket
import io
import http.client
from .utils import logger

class HTTPRequest:
    def __init__(self, method, path, version, headers, body=None):
        self.method = method
        self.path = path
        self.version = version
        self.headers = headers
        self.body = body

class HTTPParser:
    @staticmethod
    def parse(raw_data):
        """Simple HTTP/1.1 parser."""
        try:
            # Split headers and body
            header_part, _, body = raw_data.partition(b"\r\n\r\n")
            lines = header_part.split(b"\r\n")
            
            if not lines or not lines[0]:
                return None

            # Request line
            request_line = lines[0].decode('ascii').split()
            if len(request_line) < 3:
                return None
            
            method, path, version = request_line
            
            # Headers
            headers = {}
            for line in lines[1:]:
                if b":" in line:
                    key, value = line.split(b":", 1)
                    headers[key.decode('ascii').lower().strip()] = value.decode('ascii').strip()
            
            return HTTPRequest(method, path, version, headers, body)
        except Exception as e:
            logger.error(f"Failed to parse HTTP request: {e}")
            return None

try:
    import h2.connection
    import h2.events
    H2_AVAILABLE = True
except ImportError:
    H2_AVAILABLE = False

class HTTP2Handler:
    """Professional HTTP/2 handler using 'h2' library."""
    PREFACE = b"PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
    
    @staticmethod
    def is_http2(data):
        return data.startswith(HTTP2Handler.PREFACE)

    def __init__(self, sock):
        self.sock = sock
        self.conn = h2.connection.H2Connection()
        self.conn.initiate_connection()
        self.sock.sendall(self.conn.data_to_send())

    def handle(self):
        """Main HTTP/2 event loop for a connection."""
        if not H2_AVAILABLE:
            logger.error("HTTP/2 requested but 'h2' library is missing.")
            return

        while True:
            try:
                data = self.sock.recv(65535)
                if not data: break
                
                events = self.conn.receive_data(data)
                for event in events:
                    if isinstance(event, h2.events.RequestReceived):
                        self.handle_request(event)
                
                data_to_send = self.conn.data_to_send()
                if data_to_send:
                    self.sock.sendall(data_to_send)
            except Exception as e:
                logger.debug(f"H2 Connection closed: {e}")
                break

    def handle_request(self, event):
        # Convert H2 headers to dict
        headers = dict(event.headers)
        method = headers.get(':method')
        path = headers.get(':path')
        
        # This would then call the app... for now just logging
        logger.info(f"H2 Request: {method} {path}")
        
        # Simple H2 Response
        response_headers = [
            (':status', '200'),
            ('content-type', 'text/plain'),
            ('server', 'Asteri'),
        ]
        self.conn.send_headers(event.stream_id, response_headers)
        self.conn.send_data(event.stream_id, b"Hello from Asteri (HTTP/2)!", end_stream=True)
        self.sock.sendall(self.conn.data_to_send())

def build_http_response(status_code, headers, body):
    """Build a standard HTTP/1.1 response."""
    status_text = http.client.responses.get(status_code, "Unknown")
    response = f"HTTP/1.1 {status_code} {status_text}\r\n"
    
    if "Content-Length" not in headers and body:
        headers["Content-Length"] = str(len(body))
    
    for key, value in headers.items():
        response += f"{key}: {value}\r\n"
    
    response += "\r\n"
    return response.encode('ascii') + (body if isinstance(body, bytes) else body.encode('utf-8'))
