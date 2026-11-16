import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import threading
from unittest.mock import patch
import unittest
from tools.verify_console_pressure import EXPECTED, request, validate_response


class ConsolePressureTests(unittest.TestCase):
    def test_accepts_only_complete_synthetic_success_or_bounded_busy(self):
        validate_response(200, json.dumps(EXPECTED).encode())
        validate_response(429, b'demo busy\n')
        for status, body in [(503, b'Workload verification unavailable\n'),
                             (429, b'unexpected sensitive detail'), (200, b'x'*4097),
                             (200, b'{}'), (200, json.dumps(dict(EXPECTED, tokenRevoked=False)).encode()),
                             (200, json.dumps(dict(EXPECTED, tokenTTLSeconds=True)).encode()),
                             (200, json.dumps(dict(EXPECTED, token='must-not-be-returned')).encode()),
                             (200, b'{"verified":true,"verified":true}')]:
            with self.subTest(status=status, bodyLength=len(body)):
                with self.assertRaises(ValueError):
                    validate_response(status, body)


    def test_redirects_never_contact_location(self):
        destinations = []
        code = [302]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != '/api/demo':
                    destinations.append(self.path)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(json.dumps(EXPECTED).encode())
                    return
                self.send_response(code[0])
                self.send_header('Location', '/outside-console')
                self.send_header('Content-Length', '0')
                self.end_headers()
            def do_GET(self):
                destinations.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(EXPECTED).encode())
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for status in (301, 302, 303, 307, 308):
                code[0] = status
                with self.subTest(status=status), self.assertRaises(ValueError):
                    request('http://127.0.0.1:'+str(server.server_port), ssl.create_default_context())
            self.assertEqual(destinations, [])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_request_timeout_is_not_retried(self):
        with patch('tools.verify_console_pressure.urllib.request.build_opener') as factory:
            factory.return_value.open.side_effect = TimeoutError('synthetic timeout')
            with self.assertRaises(TimeoutError):
                request('https://127.0.0.1:8443', ssl.create_default_context())
            self.assertEqual(factory.return_value.open.call_count, 1)
            self.assertEqual(factory.return_value.open.call_args.kwargs['timeout'], 10)


if __name__ == '__main__':
    unittest.main()
