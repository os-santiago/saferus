import json
import unittest
from tools.verify_console_pressure import EXPECTED, validate_response


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


if __name__ == '__main__':
    unittest.main()
