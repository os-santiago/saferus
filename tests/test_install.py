import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest
import http.server
import threading
import time
import urllib.request
from unittest.mock import patch

from tools.demo import Demo, ROOT, CHART_SHA, private_write
from tools.bootstrap import trusted_url, DownloadRedirect, bounded_process


def read_trickled_response(url, marker):
    with urllib.request.urlopen(url,timeout=2) as response:
        Path(marker).write_text('reading')
        response.read(1024*1024)


class Trickle(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length',str(1024*1024));self.end_headers()
        try:
            for _ in range(100):
                self.wfile.write(b'x');self.wfile.flush();time.sleep(.1)
        except (BrokenPipeError,ConnectionResetError):pass


class LocalCustodyTests(unittest.TestCase):
    def test_total_download_deadline_interrupts_a_trickling_http_read(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Trickle)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                marker=Path(directory)/'started';started=time.monotonic()
                with self.assertRaisesRegex(ValueError,'deadline'):
                    bounded_process(read_trickled_response,('http://127.0.0.1:'+str(server.server_port),str(marker)),timeout=1)
                self.assertLess(time.monotonic()-started,4)
                self.assertEqual(marker.read_text(),'reading')
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_private_atomic_state_and_symlink_rejection(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'record'
            private_write(path,{'scope':'test'})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
            self.assertEqual(json.loads(path.read_text()),{'scope':'test'})
            alias=root/'alias';alias.symlink_to(path)
            with self.assertRaises(ValueError):private_write(alias,b'replaced')
            self.assertEqual(json.loads(path.read_text()),{'scope':'test'})

    def test_generated_certificates_pass_strict_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            demo=object.__new__(Demo);demo.state=Path(directory);demo.tools={'openssl':shutil.which('openssl')}
            def run(cmd):
                return subprocess.run(cmd,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
            demo.run=run
            old=os.umask(0o077)
            try:demo.tls()
            finally:os.umask(old)
            run([demo.tools['openssl'],'verify','-x509_strict','-verify_hostname','localhost','-CAfile',str(demo.state/'ca.crt'),str(demo.state/'tls.crt')])
            self.assertEqual(stat.S_IMODE((demo.state/'ca.key').stat().st_mode),0o600)
            self.assertEqual(stat.S_IMODE((demo.state/'tls.key').stat().st_mode),0o600)
            before=(demo.state/'tls.crt').read_bytes();demo.tls()
            self.assertEqual(before,(demo.state/'tls.crt').read_bytes())

    def test_vendored_chart_matches_reviewed_archive(self):
        raw=(ROOT/'charts/saferus/charts/openbao-0.30.7.tgz').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),CHART_SHA)

    def test_download_endpoint_and_redirect_constraints(self):
        for value in ['http://get.helm.sh/tool','https://get.helm.sh.evil.test/tool',
                      'https://user:password@get.helm.sh/tool','https://get.helm.sh:8443/tool','file:///tmp/tool']:
            with self.subTest(value=value):
                self.assertFalse(trusted_url(value))
                with self.assertRaises(ValueError):DownloadRedirect().redirect_request(None,None,302,'',{},value)
        self.assertTrue(trusted_url('https://release-assets.githubusercontent.com/signed-artifact'))


class HelmSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helm=os.environ.get('SAFERUS_HELM','helm')
        if not shutil.which(cls.helm):raise unittest.SkipTest('Helm is required for manifest tests')

    def test_insecure_modes_and_mutable_console_image_rejected(self):
        for option in ['openbao.global.tlsDisable=true','openbao.injector.enabled=true',
                       'openbao.csi.enabled=true','openbao.server.dev.enabled=true',
                       'console.image=example/image:latest','console.enabled=true']:
            with self.subTest(option=option):
                result=subprocess.run([self.helm,'template','saferus',str(ROOT/'charts/saferus'),'--set',option],capture_output=True)
                self.assertNotEqual(result.returncode,0)

    def test_rendered_default_security(self):
        import yaml
        raw=subprocess.check_output([self.helm,'template','saferus',str(ROOT/'charts/saferus'),'--namespace','saferus-demo'])
        docs=[d for d in yaml.safe_load_all(raw) if d]
        server=next(d for d in docs if d['kind']=='StatefulSet')['spec']['template']['spec']
        self.assertEqual(len(server['containers']),1)
        security=server['containers'][0]['securityContext']
        self.assertTrue(security['readOnlyRootFilesystem'])
        self.assertFalse(security['allowPrivilegeEscalation'])
        self.assertEqual(security['capabilities']['drop'],['ALL'])
        self.assertIn('@sha256:',server['containers'][0]['image'])
        self.assertTrue(server['securityContext']['runAsNonRoot'])
        self.assertFalse(any(d['kind']=='MutatingWebhookConfiguration' for d in docs))
        grants=[r for d in docs if d['kind']=='ClusterRole' for r in d['rules']]
        self.assertEqual(grants,[{'apiGroups':['authentication.k8s.io'],'resources':['tokenreviews'],'verbs':['create']}])
        network=next(d for d in docs if d['kind']=='NetworkPolicy')['spec']
        self.assertEqual(network['policyTypes'],['Ingress','Egress'])
        self.assertTrue(all('to' in rule and rule['to'] for rule in network['egress']))

    def test_console_identity_mounts_and_network(self):
        import yaml
        raw=subprocess.check_output([self.helm,'template','saferus',str(ROOT/'charts/saferus'),
            '--namespace','saferus-demo','--set','console.enabled=true','--set','console.image=example/console@sha256:'+'a'*64])
        docs=[d for d in yaml.safe_load_all(raw) if d]
        pod=next(d for d in docs if d['kind']=='Deployment')['spec']['template']['spec']
        self.assertEqual(len(pod['containers']),1)
        self.assertFalse(pod['automountServiceAccountToken'])
        self.assertEqual(pod['serviceAccountName'],'saferus-client')
        projected=next(v for v in pod['volumes'] if v['name']=='identity')['projected']
        self.assertEqual(projected['sources'],[{'serviceAccountToken':{'audience':'saferus','expirationSeconds':600,'path':'token'}}])
        tls=next(v for v in pod['volumes'] if v['name']=='tls')['secret']
        self.assertEqual(tls['secretName'],'saferus-console-tls')
        self.assertEqual(tls['defaultMode'],0o440)
        self.assertTrue(all(m['readOnly'] for m in pod['containers'][0]['volumeMounts']))
        net=next(d for d in docs if d['kind']=='NetworkPolicy' and d['metadata']['name']=='saferus-console')['spec']
        self.assertEqual(net['ingress'],[])
        ports={p['port'] for rule in net['egress'] for p in rule['ports']}
        self.assertEqual(ports,{53,8200})
