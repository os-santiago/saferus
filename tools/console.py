"""Build and exercise the local synthetic console using the owned demo cluster."""
import base64
import hashlib
import json
from pathlib import Path
import re
import secrets
import tempfile
import urllib.request

from .demo import ROOT, private_write


def install(d):
    if hasattr(d, 'existing_console'):
        existing = d.existing_console()
        if existing is not None:
            return existing
    source = hashlib.sha256()
    for p in sorted([ROOT/'go.mod',ROOT/'Containerfile',*(ROOT/'cmd').rglob('*.go'),*(ROOT/'web').rglob('*')]):
        if p.is_file():
            source.update(str(p.relative_to(ROOT)).encode()+b'\0'+p.read_bytes())
    tag = 'localhost/saferus-console:'+source.hexdigest()[:20]
    provider = d.env['KIND_EXPERIMENTAL_PROVIDER']
    d.run([provider,'build','-t',tag,'-f',str(ROOT/'Containerfile'),str(ROOT)],timeout=300)
    node = d.cluster_name+'-control-plane'
    with tempfile.TemporaryDirectory(prefix='image-',dir=d.state) as directory:
        archive = Path(directory)/'console.tar'
        d.run([provider,'save','-o',str(archive),tag],timeout=120)
        if archive.stat().st_size > 64*1024*1024:
            raise ValueError('Console image exceeds import budget')
        d.run([provider,'exec','-i',node,'ctr','-n','k8s.io','images','import','--digests','-'],data=archive.read_bytes(),timeout=120)
    inventory = d.run([provider,'exec',node,'ctr','-n','k8s.io','images','ls']).decode().splitlines()
    matches = [line.split()[2] for line in inventory if line.split() and line.split()[0]==tag]
    if len(matches)!=1 or not re.fullmatch('sha256:[a-f0-9]{64}',matches[0]):
        raise ValueError('Cannot resolve imported immutable console image')
    image = 'localhost/saferus-console@'+matches[0]
    aliases = [line.split()[2] for line in inventory if line.split() and line.split()[0]==image]
    if aliases and aliases!=matches:
        raise ValueError('Imported digest alias differs')
    if not aliases:
        d.run([provider,'exec',node,'ctr','-n','k8s.io','images','tag',tag,image])
    if hasattr(d, 'before_console_create'):
        d.before_console_create()
    if not (d.state/'console.crt').exists():
        openssl = d.tools['openssl']
        d.run([openssl,'req','-new','-newkey','ec','-pkeyopt','ec_paramgen_curve:P-256','-nodes',
            '-subj','/CN=saferus-console','-keyout',str(d.state/'console.key'),'-out',str(d.state/'console.csr')])
        private_write(d.state/'console-extensions',b'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\nsubjectAltName=DNS:saferus-console,DNS:localhost,IP:127.0.0.1\n')
        d.run([openssl,'x509','-req','-in',str(d.state/'console.csr'),'-CA',str(d.state/'ca.crt'),'-CAkey',str(d.state/'ca.key'),
            '-CAcreateserial','-days','2','-extfile',str(d.state/'console-extensions'),'-out',str(d.state/'console.crt')])
    d.run([d.tools['openssl'],'verify','-x509_strict','-CAfile',str(d.state/'ca.crt'),str(d.state/'console.crt')])
    d.apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':'saferus-console-tls','namespace':d.namespace},
        'type':'kubernetes.io/tls','data':{'tls.crt':base64.b64encode((d.state/'console.crt').read_bytes()).decode(),
                                       'tls.key':base64.b64encode((d.state/'console.key').read_bytes()).decode()}})
    values = json.loads((d.state/'values.json').read_text())
    values['console']={'enabled':True,'image':image}
    private_write(d.state/'values.json',values)
    if hasattr(d, 'before_console_create'):
        d.before_console_create()
    d.run([d.tools['helm'],'--kubeconfig',str(d.state/'kubeconfig'),'upgrade','saferus',str(ROOT/'charts/saferus'),
        '-n',d.namespace,'-f',str(d.state/'values.json'),'--wait','--timeout','180s'],timeout=210)
    return image


def probe(d, image, allowed):
    name = 'saferus-probe-'+secrets.token_hex(5)
    labels = {'saferus.io/probe':'true'}
    if allowed:
        labels['saferus.io/client']='allowed'
    manifest={'apiVersion':'v1','kind':'Pod','metadata':{'name':name,'namespace':d.namespace,'labels':labels},'spec':{
        'automountServiceAccountToken':False,'restartPolicy':'Never','activeDeadlineSeconds':45,
        'securityContext':{'runAsNonRoot':True,'runAsUser':1000,'runAsGroup':1000,'seccompProfile':{'type':'RuntimeDefault'}},
        'containers':[{'name':'probe','image':image,'args':['--probe' if allowed else '--probe-denied'],
            'securityContext':{'readOnlyRootFilesystem':True,'allowPrivilegeEscalation':False,'capabilities':{'drop':['ALL']}},
            'resources':{'requests':{'cpu':'20m','memory':'16Mi'},'limits':{'cpu':'100m','memory':'32Mi'}},
            'env':[{'name':'SAFERUS_VAULT_URL','value':'https://saferus:8200'},{'name':'SAFERUS_CA_FILE','value':'/ca/ca.crt'}],
            'volumeMounts':[{'name':'ca','mountPath':'/ca','readOnly':True}]}],
        'volumes':[{'name':'ca','configMap':{'name':'saferus-ca'}}]}}
    created=json.loads(d.kubectl('create','-f','-','-o','json',data=json.dumps(manifest).encode()))
    try:
        d.kubectl('wait','pod/'+name,'-n',d.namespace,'--for=jsonpath={.status.phase}=Succeeded','--timeout=50s',timeout=55)
    finally:
        # The unique name was created by this invocation. Verify identity before
        # asking Kubernetes to delete only the acknowledged Pod UID.
        current=d.get('pod',name)
        if current['metadata']['uid']!=created['metadata']['uid']:
            raise ValueError('Probe identity changed; cleanup refused')
        # kubectl raw DELETE supports the API's atomic UID precondition.
        options={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':created['metadata']['uid']},'gracePeriodSeconds':0}
        d.kubectl('delete','--raw','/api/v1/namespaces/'+d.namespace+'/pods/'+name,'-f','-',data=json.dumps(options).encode())


def verify(d, image):
    probe(d,image,True)
    probe(d,image,False)
    probe(d,image,True)
    with d.api('deployment/saferus-console',8443) as api:
        origin=api.origin.removesuffix('/v1/')
        request=urllib.request.Request(origin+'/api/demo',data=b'{}',headers={'Content-Type':'application/json','Origin':origin},method='POST')
        with api.opener.open(request,timeout=12) as response:
            result=json.loads(response.read(65537))
        if result.get('verified') is not True or result.get('tokenRevoked') is not True:
            raise ValueError('Actual console workload verification failed')
        with api.opener.open(origin+'/',timeout=5) as response:
            if b'Run verification' not in response.read(65537):
                raise ValueError('Console page unavailable')
    report=json.loads((d.state/'verification.json').read_text())
    report.update({'status':'passed','realWorkloadConsole':True,'projectedWorkloadIdentity':True,'positiveNegativePositiveNetworkProbe':True,
        'consoleImage':image,'consoleRuntimeUID':d.get('deployment','saferus-console')['metadata']['uid']})
    private_write(d.state/'verification.json',report)
    print(json.dumps({'console':'verified','networkPolicy':'positive-negative-positive passed','autonomousAISDLCE2E':False}))
