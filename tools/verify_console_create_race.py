"""Exercise the console create-only race against a real disposable namespace.

The fixture schedules no Pods and never accesses AI-SDLC workflow endpoints.
"""
import argparse
import json
import secrets
import subprocess
import tempfile
from pathlib import Path

from .bootstrap import ensure
from .demo import ROOT, private_write
from .test_environment import Environment


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ai-sdlc-state',type=Path,default=Path.home()/'.local/state/ai-sdlc/ai-sdlc-saferus')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    k=[ensure('kubectl'),'--kubeconfig',str(args.ai_sdlc_state/'kubeconfig')]
    marker=json.loads((args.ai_sdlc_state/'ownership.json').read_text())
    class Conflict(Exception):pass
    def call(*argv,data=None,**kwargs):
        result=subprocess.run(k+list(argv),input=data,capture_output=True,timeout=30)
        if result.returncode:
            if b'AlreadyExists' in result.stderr:raise Conflict()
            raise ValueError('Kubernetes race fixture operation failed')
        return result.stdout
    live=json.loads(call('get','namespace','kube-system','-o','json'))['metadata']['uid']
    if marker.get('cluster')!='ai-sdlc-saferus' or marker.get('clusterUID')!=live:
        raise ValueError('Dedicated owned AI-SDLC cluster required')
    namespace='saferus-create-race-'+secrets.token_hex(6)
    created=json.loads(call('create','-f','-','-o','json',data=json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':namespace}}).encode()))
    report={'scope':'real-kubernetes-console-create-race','status':'failed','podsScheduled':False,'autonomousAISDLCE2E':False}
    try:
        rendered=subprocess.run([ensure('helm'),'template','saferus',str(ROOT/'charts/saferus'),'-n',namespace,
            '--set','console.enabled=true','--set-string','console.image=registry.invalid/baseline@sha256:'+'a'*64,
            '--show-only','templates/console.yaml'],check=True,capture_output=True,timeout=30).stdout
        with tempfile.TemporaryDirectory(prefix='saferus-create-race-') as directory:
            d=object.__new__(Environment);d.state=Path(directory);d.namespace=namespace;d.cluster=lambda:None
            private_write(d.state/'environment-intent.json',{'nonce':secrets.token_hex(24)})
            state={"inject":True}
            def kubectl(*argv,data=None,**kwargs):
                raw=call(*argv,data=data)
                if argv[:2]==('create','--dry-run=client'):
                    value=json.loads(raw)
                    if value.get('kind')=='Deployment':
                        value['spec']['replicas']=0
                        state['deployment']=value
                        return json.dumps(value).encode()
                if argv[:2]==('get','Deployment') and not raw.strip() and state['inject']:
                    foreign=json.loads(json.dumps(state['deployment']))
                    foreign['spec']['template']['spec']['containers'][0]['image']='registry.invalid/promoted@sha256:'+'b'*64
                    state['foreign']=json.loads(call('create','-n',namespace,'-f','-','-o','json',data=json.dumps(foreign).encode()))
                    # Return the old absent GET after a concurrent object exists.
                return raw
            d.kubectl=kubectl
            try:
                d.create_console(rendered)
                raise ValueError('Expected atomic create conflict')
            except Conflict:
                pass
            current=json.loads(call('get','deployment','saferus-console','-n',namespace,'-o','json'))
            if (current['metadata']['uid']!=state['foreign']['metadata']['uid']
                    or current['spec']['template']['spec']['containers'][0]['image']!=state['foreign']['spec']['template']['spec']['containers'][0]['image']):
                raise ValueError('Concurrent resource was modified')
            if json.loads(call('get','pods','-n',namespace,'-o','json'))['items']:
                raise ValueError('Fixture unexpectedly scheduled a Pod')
            owners=json.loads((d.state/'console-resources.json').read_text())
            if set(owners)!={'NetworkPolicy','Service'}:
                raise ValueError('Unacknowledged deployment was adopted')
            call('delete','--raw','/apis/apps/v1/namespaces/'+namespace+'/deployments/saferus-console','-f','-',
                data=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':current['metadata']['uid']}}).encode())
            call('wait','--for=delete','deployment/saferus-console','-n',namespace,'--timeout=20s')
            state['inject']=False
            d.create_console(rendered)
            owned=json.loads((d.state/'console-resources.json').read_text())
            d.create_console(rendered)
            if json.loads((d.state/'console-resources.json').read_text())!=owned or set(owned)!={'NetworkPolicy','Service','Deployment'}:
                raise ValueError('Create-only recovery changed resource identities')
            report.update(status='passed',atomicCreateConflict=True,concurrentUIDPreserved=True,concurrentImagePreserved=True,isolationCreatedFirst=True,positiveCreateAndReapply=True)
    finally:
        call('delete','--raw','/api/v1/namespaces/'+namespace,'-f','-',data=json.dumps({'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':created['metadata']['uid']}}).encode())
        call('wait','--for=delete','namespace/'+namespace,'--timeout=25s')
        report['namespaceRemoved']=True
        args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
