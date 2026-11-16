"""Bounded, local-only OpenBao demo. Never prints secret material."""
import argparse
import base64
import contextlib
import datetime
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
NAMESPACE = 'saferus-demo'
CLUSTER = 'saferus-demo'
NODE = 'kindest/node:v1.35.8@sha256:07b2536e30b803ed61d1677a79df6115f798ce64c80f9e22f6ed45afd09323c0'
CHART_SHA = 'fa4e6f3c1bbe936dc7ba079f1303ea56810af2f8600f046d8d5809860a4f980f'


def private_write(path, value):
    """Atomic private state; caller owns and locks the parent directory."""
    data = value if isinstance(value, bytes) else json.dumps(value, indent=2).encode()
    if path.is_symlink():
        raise ValueError('State cannot be a symlink')
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class API:
    def __init__(self, port, ca):
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context), NoRedirect())
        self.origin = 'https://127.0.0.1:' + str(port) + '/v1/'

    def call(self, method, path, body=None, token=None, expected=(200, 204)):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Vault-Token'] = token
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.origin + path, data=data, method=method, headers=headers)
        try:
            response = self.opener.open(req, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            raw = response.read(1048577)
            if len(raw) > 1048576 or response.code not in expected:
                raise ValueError('Vault API rejected ' + method + ' ' + path + ' (HTTP ' + str(response.code) + ')')
            return json.loads(raw) if raw else {}


class Demo:
    def __init__(self, args):
        self.state = args.state.absolute()
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        st = self.state.lstat()
        if self.state.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
            raise ValueError('State must be an owned private directory (0700)')
        self.lock = os.open(self.state / 'lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.deadline = time.monotonic() + 1200
        self.tools = {}
        for name in ('kind', 'kubectl', 'helm', 'openssl'):
            value = os.environ.get('SAFERUS_' + name.upper())
            if value:
                binary = shutil.which(value)
            elif name=='openssl':
                binary = shutil.which(name)
            else:
                from .bootstrap import ensure
                binary = ensure(name)
            if binary is None:
                raise ValueError('Required tool is missing: ' + name)
            self.tools[name] = binary
        self.env = dict(os.environ, KIND_EXPERIMENTAL_PROVIDER=args.provider)
        self.k = [self.tools['kubectl'], '--kubeconfig', str(self.state / 'kubeconfig')]

    def run(self, command, data=None, timeout=120):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('Demo deadline exceeded')
        result = subprocess.run(command, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=min(timeout, remaining), env=self.env)
        if result.returncode:
            raise ValueError('Command failed: ' + Path(command[0]).name + ' '+command[1]+' (exit ' + str(result.returncode) + ')')
        return result.stdout

    def kubectl(self, *args, data=None, timeout=120):
        return self.run(self.k + list(args), data, timeout)

    def get(self, resource, name, namespace=NAMESPACE):
        return json.loads(self.kubectl('get', resource, name, '-n', namespace, '-o', 'json'))

    def apply(self, value):
        return self.kubectl('apply', '-f', '-', data=json.dumps(value).encode())

    def cluster(self):
        receipt = self.state / 'ownership.json'
        existing = self.run([self.tools['kind'], 'get', 'clusters']).decode().splitlines()
        if receipt.exists():
            known = json.loads(receipt.read_text())
            if known.get('provider')!=self.env['KIND_EXPERIMENTAL_PROVIDER'] or CLUSTER not in existing or known.get('clusterUID') != self.get('namespace', 'kube-system', 'default')['metadata']['uid']:
                raise ValueError('Owned cluster identity changed')
            return
        if CLUSTER in existing:
            raise ValueError('Refusing to adopt an unowned cluster')
        for name in ('kubeconfig', 'kind.json'):
            if (self.state / name).exists() or (self.state / name).is_symlink():
                raise ValueError('Incomplete installation requires inspection')
        private_write(self.state / 'kind.json', {'kind':'Cluster','apiVersion':'kind.x-k8s.io/v1alpha4',
            'networking':{'apiServerAddress':'127.0.0.1'},'nodes':[{'role':'control-plane','image':NODE}]})
        self.run([self.tools['kind'], 'create', 'cluster', '--name', CLUSTER, '--config', str(self.state / 'kind.json'),
            '--kubeconfig', str(self.state / 'kubeconfig'), '--wait', '180s'], timeout=240)
        os.chmod(self.state / 'kubeconfig', 0o600)
        private_write(receipt, {'cluster':CLUSTER,'provider':self.env['KIND_EXPERIMENTAL_PROVIDER'],
            'clusterUID':self.get('namespace','kube-system','default')['metadata']['uid']})

    def destroy(self):
        receipt=self.state/'ownership.json';intent=self.state/'destroy-intent.json'
        if not receipt.exists() and not intent.exists():
            raise ValueError('No owned cluster or deletion receipt')
        known=json.loads((receipt if receipt.exists() else intent).read_text())
        provider=self.env['KIND_EXPERIMENTAL_PROVIDER']
        if known.get('cluster')!=CLUSTER or known.get('provider')!=provider or not known.get('clusterUID'):
            raise ValueError('Deletion ownership differs')
        if intent.exists() and json.loads(intent.read_text())!=known:
            raise ValueError('Deletion intent differs from ownership')
        owned = ['kubeconfig','kind.json','ca.key','ca.crt','ca.srl','tls.key','tls.crt','tls.csr',
            'extensions','demo-custody.json','configured.json','demo-token','values.json','console.key','console.crt',
            'console.csr','console-extensions','console-image.tar','bootstrap-progress.json','ownership.json','destroy-intent.json']
        # Validate every local target before the first destructive action.
        for name in owned:
            path=self.state/name
            if path.is_symlink():raise ValueError('Refusing symlink in demo cleanup')
            if path.exists() and (not path.is_file() or path.stat().st_uid!=os.getuid()):raise ValueError('Unowned demo file')
        existing=self.run([self.tools['kind'],'get','clusters']).decode().splitlines()
        if CLUSTER in existing:
            self.cluster()  # Must still be the original live UID and provider.
            private_write(intent,known)
            self.run([self.tools['kind'],'delete','cluster','--name',CLUSTER],timeout=120)
        elif not intent.exists():
            raise ValueError('Cluster disappeared without a recorded deletion intent')
        if CLUSTER in self.run([self.tools['kind'],'get','clusters']).decode().splitlines():
            raise ValueError('Cluster deletion was not confirmed')
        for name in owned:
            path=self.state/name
            if path.exists():path.unlink()
        print('Owned demo cluster and local custody files removed. Verification report retained; no secure-erasure claim.')

    def tls(self):
        if (self.state / 'tls.crt').exists():
            for name in ('tls.crt','tls.key','ca.crt'):
                p = self.state / name
                if not p.is_file() or p.is_symlink():
                    raise ValueError('Incomplete TLS state')
            self.run([self.tools['openssl'],'verify','-x509_strict','-CAfile',str(self.state/'ca.crt'),str(self.state/'tls.crt')])
            return
        openssl = self.tools['openssl']
        self.run([openssl,'req','-x509','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes','-days','2',
            '-subj','/CN=Saferus local demo CA','-addext','basicConstraints=critical,CA:TRUE',
            '-addext','keyUsage=critical,keyCertSign,cRLSign','-keyout',str(self.state/'ca.key'),'-out',str(self.state/'ca.crt')])
        self.run([openssl,'req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
            '-subj','/CN=saferus','-keyout',str(self.state/'tls.key'),'-out',str(self.state/'tls.csr')])
        private_write(self.state/'extensions', b'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:saferus,DNS:saferus.saferus-demo.svc,DNS:saferus.saferus-demo.svc.cluster.local,DNS:localhost,IP:127.0.0.1\n')
        self.run([openssl,'x509','-req','-in',str(self.state/'tls.csr'),'-CA',str(self.state/'ca.crt'),
            '-CAkey',str(self.state/'ca.key'),'-CAcreateserial','-days','2','-extfile',str(self.state/'extensions'),'-out',str(self.state/'tls.crt')])

    def install(self):
        archive = ROOT/'charts/saferus/charts/openbao-0.30.7.tgz'
        if hashlib.sha256(archive.read_bytes()).hexdigest() != CHART_SHA:
            raise ValueError('Vendored chart digest differs')
        self.apply({'apiVersion':'v1','kind':'Namespace','metadata':{'name':NAMESPACE,'labels':{
            'pod-security.kubernetes.io/enforce':'restricted','saferus.io/scope':'local-demo'}}})
        self.tls()
        data = {name:base64.b64encode((self.state/name).read_bytes()).decode() for name in ('tls.crt','tls.key','ca.crt')}
        # Secret bytes stay on stdin, never command arguments or public Helm values.
        self.apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':'saferus-tls','namespace':NAMESPACE},'type':'Opaque','data':data})
        self.apply({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'saferus-ca','namespace':NAMESPACE},'data':{'ca.crt':(self.state/'ca.crt').read_text()}})
        endpoints = self.get('endpoints','kubernetes','default')['subsets']
        cidrs = sorted({str(ipaddress.ip_address(a['ip'])) + '/32' for s in endpoints for a in s['addresses']})
        ports = {p['port'] for s in endpoints for p in s['ports']}
        if len(ports) != 1 or not cidrs:
            raise ValueError('Ambiguous API endpoint')
        values = {'network':{'apiCIDRs':cidrs,'apiPort':ports.pop()}}
        if (self.state/'values.json').exists():
            previous = json.loads((self.state/'values.json').read_text())
            if 'console' in previous:
                values['console'] = previous['console']
        private_write(self.state/'values.json', values)
        self.run([self.tools['helm'],'--kubeconfig',str(self.state/'kubeconfig'),'upgrade','--install','saferus',str(ROOT/'charts/saferus'),'-n',NAMESPACE,
            '-f',str(self.state/'values.json'),'--timeout','180s'],timeout=210)
        self.kubectl('wait','pod/saferus-0','-n',NAMESPACE,'--for=jsonpath={.status.phase}=Running','--timeout=180s',timeout=200)

    @contextlib.contextmanager
    def api(self, target='pod/saferus-0', remote_port=8200):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        proc = subprocess.Popen(self.k + ['port-forward','-n',NAMESPACE,target,str(port)+':'+str(remote_port),'--address','127.0.0.1'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic()+15
            while time.monotonic()<deadline:
                if proc.poll() is not None:
                    raise ValueError('Local port forward failed')
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=1):
                        break
                except OSError:
                    time.sleep(.2)
            else:
                raise ValueError('Local port forward deadline exceeded')
            yield API(port,self.state/'ca.crt')
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill(); proc.wait(timeout=5)

    def bootstrap(self, api):
        health = api.call('GET','sys/health',expected=(200,501,503))
        custody = self.state/'demo-custody.json'
        if not health['initialized']:
            if custody.exists():
                raise ValueError('Vault reset or custody mismatch')
            initialized = api.call('PUT','sys/init',{'secret_shares':3,'secret_threshold':2})
            private_write(custody,initialized)
        if not custody.exists():
            raise ValueError('Initialized vault lacks local demo custody; refusing takeover')
        keys = json.loads(custody.read_text())
        if health['sealed']:
            for key in keys['keys_base64'][:2]:
                api.call('PUT','sys/unseal',{'key':key})
        leader_deadline=time.monotonic()+30
        while api.call('GET','sys/leader').get('is_self') is not True:
            if time.monotonic()>leader_deadline:raise ValueError('Raft leader readiness deadline exceeded')
            time.sleep(.2)
        if (self.state/'configured.json').exists():
            return
        root = keys.get('root_token')
        progress_path=self.state/'bootstrap-progress.json'
        progress=json.loads(progress_path.read_text()) if progress_path.exists() else {}
        policy=(ROOT/'policies/demo-reader.hcl').read_text()
        fingerprint=hashlib.sha256(('saferus-demo-bootstrap/v1\0'+policy).encode()).hexdigest()
        if progress and progress.get('configuration')!=fingerprint:
            raise ValueError('Interrupted bootstrap configuration differs')
        if progress.get('phase') not in ('configured','revoked'):
            if not root:raise ValueError('Interrupted bootstrap requires custody recovery')
            # Audit initialization can complete shortly after unseal. Only this
            # read is polled; no mutation is retried after an ambiguous response.
            deadline=time.monotonic()+15
            while True:
                audit=api.call('GET','sys/audit',token=root,expected=(200,500)).get('data',{}).get('saferus/')
                if audit:break
                if time.monotonic()>deadline:raise ValueError('Required declarative audit device is absent')
                time.sleep(.2)
            if audit.get('type')!='file' or audit.get('options',{}).get('file_path')!='/openbao/audit/audit.json':
                raise ValueError('Declarative audit differs')
            mounts=api.call('GET','sys/mounts',token=root)['data']
            for name,kind,options in [('secret','kv',{'version':'2'}),('transit','transit',{})]:
                current=mounts.get(name+'/')
                if current:
                    if current.get('type')!=kind or (options and current.get('options')!=options):
                        raise ValueError('Existing secrets engine differs')
                else:api.call('POST','sys/mounts/'+name,{'type':kind,'options':options},root)
            api.call('POST','secret/config',{'max_versions':5,'cas_required':True},root)
            seed=api.call('GET','secret/data/demo/service',token=root,expected=(200,404))
            if seed.get('data'):
                if not isinstance(seed['data']['data'].get('password'),str) or len(seed['data']['data']['password'])!=64:
                    raise ValueError('Existing demo secret differs; refusing overwrite')
            else:api.call('POST','secret/data/demo/service',{'data':{'password':secrets.token_hex(32)},'options':{'cas':0}},root)
            key=api.call('GET','transit/keys/demo',token=root,expected=(200,404)).get('data')
            if key:
                if key.get('type')!='aes256-gcm96' or key.get('exportable') or key.get('allow_plaintext_backup'):
                    raise ValueError('Existing cryptographic key differs')
            else:api.call('POST','transit/keys/demo',{'type':'aes256-gcm96','exportable':False,'allow_plaintext_backup':False},root)
            auth=api.call('GET','sys/auth',token=root)['data'].get('kubernetes/')
            if auth:
                if auth.get('type')!='kubernetes':raise ValueError('Existing authentication method differs')
            else:api.call('POST','sys/auth/kubernetes',{'type':'kubernetes'},root)
            api.call('POST','auth/kubernetes/config',{'kubernetes_host':'https://kubernetes.default.svc:443'},root)
            api.call('PUT','sys/policies/acl/saferus-demo-reader',{'policy':policy},root)
            api.call('POST','auth/kubernetes/role/demo',{'bound_service_account_names':['saferus-client'],
                'bound_service_account_namespaces':[NAMESPACE],'audience':'saferus','token_policies':['saferus-demo-reader'],
                'token_ttl':60,'token_max_ttl':300,'token_explicit_max_ttl':300,'token_no_default_policy':True},root)
            progress={'phase':'configured','configuration':fingerprint}
            private_write(progress_path,progress)
        if progress['phase']=='configured':
            if not root:raise ValueError('Missing root revocation evidence')
            # An interrupted revoke is recovered by observing rejection of the
            # exact original token, never by generating replacement authority.
            observed=api.call('GET','auth/token/lookup-self',token=root,expected=(200,403))
            if observed.get('data'):api.call('POST','auth/token/revoke-self',{},root)
            api.call('GET','auth/token/lookup-self',token=root,expected=(403,))
            private_write(progress_path,{'phase':'revoked','configuration':fingerprint})
        keys.pop('root_token',None)
        private_write(custody,keys)
        private_write(self.state/'configured.json',{'rootRevoked':True,'scope':'local-synthetic-demo','configuration':fingerprint})

    def verify(self, api):
        health = api.call('GET','sys/health')
        if health['sealed'] or not health['initialized'] or health.get('version')!='2.7.2':
            raise ValueError('Vault is not initialized and unsealed')
        endpoint = urllib.parse.urlparse(api.origin)
        old_tls = ssl.create_default_context(cafile=str(self.state/'ca.crt'))
        old_tls.maximum_version = ssl.TLSVersion.TLSv1_2
        try:
            with socket.create_connection((endpoint.hostname,endpoint.port),timeout=5) as sock:
                with old_tls.wrap_socket(sock,server_hostname='localhost'):
                    raise ValueError('Vault accepted TLS below 1.3')
        except ssl.SSLError as exc:
            if exc.reason!='TLSV1_ALERT_PROTOCOL_VERSION':
                raise ValueError('TLS version rejection was inconclusive') from None
        try:
            untrusted = ssl.create_default_context()
            with socket.create_connection((endpoint.hostname,endpoint.port),timeout=5) as sock:
                with untrusted.wrap_socket(sock,server_hostname='localhost'):
                    raise ValueError('Untrusted TLS certificate accepted')
        except ssl.SSLCertVerificationError:
            pass
        api.call('GET','secret/data/demo/service',expected=(403,))
        jwt = self.kubectl('create','token','saferus-client','-n',NAMESPACE,'--audience','saferus','--duration','10m').decode().strip()
        login = api.call('POST','auth/kubernetes/login',{'role':'demo','jwt':jwt})['auth']
        token = login['client_token']
        if not 0 < login['lease_duration'] <= 60 or login['policies'] != ['saferus-demo-reader']:
            raise ValueError('Workload token exceeds configured scope')
        secret = api.call('GET','secret/data/demo/service',token=token)['data']['data']['password']
        if len(secret)!=64:
            raise ValueError('Demo persisted secret differs')
        api.call('GET','secret/data/other/service',token=token,expected=(403,))
        api.call('POST','secret/data/demo/service',{'data':{'password':'forbidden'},'options':{'cas':1}},token,expected=(403,))
        api.call('GET','transit/export/encryption-key/demo',token=token,expected=(403,))
        wrong = self.kubectl('create','token','saferus-client','-n',NAMESPACE,'--audience','wrong','--duration','10m').decode().strip()
        api.call('POST','auth/kubernetes/login',{'role':'demo','jwt':wrong},expected=(403,))
        wrong = self.kubectl('create','token','default','-n',NAMESPACE,'--audience','saferus','--duration','10m').decode().strip()
        api.call('POST','auth/kubernetes/login',{'role':'demo','jwt':wrong},expected=(403,))
        plaintext = base64.b64encode(b'Saferus synthetic demonstration').decode()
        encrypted = api.call('POST','transit/encrypt/demo',{'plaintext':plaintext},token)['data']['ciphertext']
        decoded = api.call('POST','transit/decrypt/demo',{'ciphertext':encrypted},token)['data']['plaintext']
        if decoded != plaintext:
            raise ValueError('Authenticated encryption roundtrip failed')
        version, payload = encrypted.rsplit(':',1)
        altered = bytearray(base64.b64decode(payload)); altered[-1] ^= 1
        api.call('POST','transit/decrypt/demo',{'ciphertext':version+':'+base64.b64encode(altered).decode()},token,expected=(400,))
        api.call('POST','auth/token/revoke-self',{},token)
        api.call('GET','secret/data/demo/service',token=token,expected=(403,))
        audit = self.kubectl('exec','-n',NAMESPACE,'saferus-0','--','tail','-c','1048576','/openbao/audit/audit.json')
        if not audit or secret.encode() in audit or token.encode() in audit or jwt.encode() in audit:
            raise ValueError('Audit absent or contains raw secret material')
        pod = self.get('pod','saferus-0')
        if len(pod['spec']['containers'])!=1:
            raise ValueError('Unexpected sidecar')
        report = {'observedAt':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'status':'vault-passed-console-pending',
            'scope':'real-isolated-kubernetes-vault-demo','tlsMinimum':'1.3','engine':'OpenBao 2.7.2','agentless':True,
            'kvRead':True,'unauthenticatedDenied':True,'crossPathDenied':True,'readerWriteDenied':True,
            'keyExportDenied':True,'wrongAudienceDenied':True,'wrongServiceAccountDenied':True,
            'encryptionRoundtrip':True,'tamperingRejected':True,'revocationEnforced':True,'auditRedactionChecked':True,
            'tls12Rejected':True,'untrustedCertificateRejected':True,
            'workloadTokenTTLSeconds':login['lease_duration'],'autonomousAISDLCE2E':False,'productionAssurance':False}
        private_write(self.state/'verification.json',report)
        print(json.dumps(report,indent=2))


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['demo','verify','destroy','tools'])
    parser.add_argument('--confirm-local-demo',action='store_true',help='Confirm deletion of the owned synthetic demo cluster and local custody')
    parser.add_argument('--provider',choices=['podman','docker'],default='podman')
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/state/saferus/demo')
    args = parser.parse_args()
    try:
        if args.command=='tools':
            from .bootstrap import ensure
            print(json.dumps({name:ensure(name) for name in ('kind','kubectl','helm')},indent=2))
            return 0
        if args.command=='destroy' and not args.confirm_local_demo:
            raise ValueError('Use --confirm-local-demo to destroy the owned synthetic demo')
        demo = Demo(args)
        if args.command=='destroy':
            demo.destroy()
            return 0
        if args.command=='verify' and not (demo.state/'ownership.json').exists():
            raise ValueError('No owned installation to verify')
        demo.cluster()
        if args.command=='demo':
            demo.install()
        with demo.api() as api:
            if args.command=='demo':
                demo.bootstrap(api)
            demo.verify(api)
        from .console import install, verify
        image = install(demo) if args.command=='demo' else json.loads((demo.state/'values.json').read_text())['console']['image']
        verify(demo, image)
        return 0
    except (ValueError,OSError,subprocess.SubprocessError,urllib.error.URLError) as exc:
        # No HTTP bodies, command output or credentials in failure diagnostics.
        print('Saferus demo stopped: '+(str(exc) if isinstance(exc,ValueError) else type(exc).__name__))
        return 1
