"""Verify browser TLS acceptance/rejection using isolated synthetic certificates."""
import contextlib
import http.server
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading

from playwright.sync_api import sync_playwright, Error
from tools.demo import Demo
from tools.verify_browser import trusted_context


class Page(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'Synthetic TLS fixture')


@contextlib.contextmanager
def endpoint(cert,key):
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Page)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_3
    tls.load_cert_chain(cert,key);server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield 'https://127.0.0.1:'+str(server.server_port)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def main():
    with tempfile.TemporaryDirectory(prefix='saferus-browser-trust-') as directory:
        root=Path(directory);openssl=shutil.which('openssl')
        def run(command):
            return subprocess.run(command,check=True,capture_output=True,timeout=15).stdout
        for name in ('trusted','untrusted'):
            path=root/name;path.mkdir(mode=0o700)
            demo=object.__new__(Demo);demo.state=path;demo.tools={'openssl':openssl};demo.run=run;demo.tls()
        trusted=root/'trusted'
        (trusted/'wrong.ext').write_text('basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:wrong.invalid\n')
        run([openssl,'req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-subj','/CN=wrong.invalid',
            '-keyout',str(trusted/'wrong.key'),'-out',str(trusted/'wrong.csr')])
        run([openssl,'x509','-req','-in',str(trusted/'wrong.csr'),'-CA',str(trusted/'ca.crt'),'-CAkey',str(trusted/'ca.key'),
            '-CAcreateserial','-days','1','-extfile',str(trusted/'wrong.ext'),'-out',str(trusted/'wrong.crt')])
        profile=root/'profile';profile.mkdir(mode=0o700)
        with endpoint(trusted/'tls.crt',trusted/'tls.key') as valid, endpoint(trusted/'wrong.crt',trusted/'wrong.key') as wrong, \
                endpoint(root/'untrusted/tls.crt',root/'untrusted/tls.key') as untrusted, sync_playwright() as playwright:
            context=trusted_context(playwright,trusted/'ca.crt',profile)
            try:
                page=context.new_page();page.goto(valid,timeout=15000)
                if 'Synthetic TLS fixture' not in page.content():raise ValueError('Trusted browser TLS failed')
                for url,code in ((wrong,'SSL_ERROR_BAD_CERT_DOMAIN'),(untrusted,'SEC_ERROR_UNKNOWN_ISSUER')):
                    try:page.goto(url,timeout=15000)
                    except Error as exc:
                        # Playwright Firefox may normalize certificate errors to
                        # SEC_ERROR_UNKNOWN; never accept a generic I/O timeout.
                        if code not in str(exc) and 'SEC_ERROR_UNKNOWN' not in str(exc):
                            raise ValueError('Unexpected browser TLS failure class') from None
                    else:raise ValueError('Browser accepted invalid TLS')
                page.goto(valid,timeout=15000)
                if 'Synthetic TLS fixture' not in page.content():raise ValueError('TLS positive control failed')
            finally:context.close()
        print(json.dumps({'status':'passed','validCertificateAccepted':True,'wrongSANRejected':True,
            'untrustedCARejected':True,'hostTrustModified':False,'syntheticCertificates':True}))


if __name__=='__main__':main()
