import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import Demo, private_write
from tools.test_environment import Environment


class TestEnvironmentCustody(unittest.TestCase):
    def fixture(self, directory):
        d=object.__new__(Environment)
        d.state=Path(directory)/'application';d.state.mkdir(mode=0o700)
        d.installation=Path(directory)/'runtime';d.installation.mkdir(mode=0o700)
        d.env={'KIND_EXPERIMENTAL_PROVIDER':'podman'}
        marker={'cluster':'ai-sdlc-saferus','provider':'podman','clusterUID':'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'}
        private_write(d.installation/'ownership.json',marker)
        private_write(d.installation/'kubeconfig',b'fixture-kubeconfig')
        state={'namespace':None,'creates':0,'lost':False,'clusterUID':marker['clusterUID']}
        d.get=lambda *args:{'metadata':{'uid':state['clusterUID']}}
        def kubectl(*args, data=None):
            if args[:2]==('get','namespace'):
                return json.dumps(state['namespace']).encode() if state['namespace'] else b''
            if args[:2]==('create','-f'):
                state['creates']+=1
                state['namespace']=json.loads(data)
                state['namespace']['metadata']['uid']='namespace-original'
                if state['lost']:raise OSError('lost create response')
                return json.dumps(state['namespace']).encode()
            raise AssertionError('Unexpected mutation: '+str(args))
        d.kubectl=kubectl
        return d,state

    def test_lost_namespace_create_reply_recovers_exact_identity_once(self):
        with tempfile.TemporaryDirectory() as directory:
            d,state=self.fixture(directory);state['lost']=True
            with self.assertRaises(OSError):d.cluster()
            d.cluster();d.cluster()
            self.assertEqual(state['creates'],1)
            self.assertEqual(json.loads((d.state/'environment-ownership.json').read_text())['namespaceUID'],'namespace-original')
            self.assertFalse((d.state/'ownership.json').exists())
            with self.assertRaises(ValueError):d.destroy()

    def test_foreign_namespace_and_replacements_are_never_adopted(self):
        for scenario in ('foreign','uid','cluster','removed','nonce'):
            with self.subTest(scenario=scenario),tempfile.TemporaryDirectory() as directory:
                d,state=self.fixture(directory)
                if scenario=='foreign':state['namespace']={'metadata':{'uid':'foreign'}}
                else:
                    d.cluster()
                    if scenario=='uid':state['namespace']['metadata']['uid']='replaced'
                    if scenario=='cluster':state['clusterUID']='ffffffff-bbbb-cccc-dddd-eeeeeeeeeeee'
                    if scenario=='removed':state['namespace']=None
                    if scenario=='nonce':state['namespace']['metadata']['annotations']['saferus.io/creation-nonce']='foreign'
                with self.assertRaises(ValueError):d.cluster()
                self.assertEqual(state['creates'],0 if scenario=='foreign' else 1)

    def test_different_or_public_kubeconfig_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            d,state=self.fixture(directory);d.cluster()
            private_write(d.installation/'kubeconfig',b'changed')
            with self.assertRaisesRegex(ValueError,'changed'):d.cluster()
            (d.installation/'kubeconfig').chmod(0o644)
            with self.assertRaisesRegex(ValueError,'private'):d.cluster()

    def test_reapply_cannot_rollback_promoted_console(self):
        with tempfile.TemporaryDirectory() as directory:
            d,state=self.fixture(directory);d.cluster()
            original=d.kubectl
            def kubectl(*args,**kwargs):
                if args[:2]==('get','deployment'):
                    return json.dumps({'spec':{'template':{'spec':{'containers':[{'image':'registry/promoted@sha256:'+'b'*64}]}}}}).encode()
                return original(*args,**kwargs)
            d.kubectl=kubectl
            private_write(d.state/'values.json',{'console':{'image':'localhost/baseline@sha256:'+'a'*64}})
            with patch.object(Demo,'install') as install, self.assertRaisesRegex(ValueError,'Console image changed'):
                d.install()
            install.assert_not_called()

    def test_reapply_existing_console_does_not_reconcile_helm_or_rebuild(self):
        from tools import console
        with tempfile.TemporaryDirectory() as directory:
            d,state=self.fixture(directory);d.cluster()
            image='localhost/baseline@sha256:'+'a'*64
            with patch.object(d,'existing_console',return_value=image), patch.object(Demo,'install') as base, patch.object(d,'run',create=True) as run:
                d.install()
                self.assertEqual(console.install(d),image)
                base.assert_not_called();run.assert_not_called()

    def test_console_appearing_during_build_prevents_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            d,_=self.fixture(directory)
            with patch.object(d,'existing_console',return_value='reviewed-image'):
                with self.assertRaisesRegex(ValueError,'appeared'):
                    d.before_console_create()

    def test_promotion_during_console_image_import_never_reaches_helm(self):
        from tools import console
        with tempfile.TemporaryDirectory() as directory:
            d,_=self.fixture(directory)
            image={'tag':None};commands=[]
            def run(command,**kwargs):
                commands.append(command)
                if command[1]=='build':image['tag']=command[3]
                elif command[1]=='save':Path(command[3]).write_bytes(b'synthetic OCI fixture')
                elif command[-2:]==['images','ls']:
                    return (image['tag']+' application/vnd.oci.image.manifest.v1+json sha256:'+'a'*64+' 100 linux/amd64\n').encode()
                return b''
            d.run=run
            with patch.object(d,'existing_console',side_effect=[None,'promoted-image']):
                with self.assertRaisesRegex(ValueError,'appeared'):
                    console.install(d)
            self.assertTrue(any(c[1]=='build' for c in commands))
            self.assertTrue(any('import' in c for c in commands))
            self.assertFalse(any('upgrade' in c for c in commands))

    def test_create_race_cannot_update_concurrent_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            d,_=self.fixture(directory);d.cluster=lambda:None
            private_write(d.state/'environment-intent.json',{'nonce':'a'*48})
            current={};writes=[]
            kinds={'policy':'NetworkPolicy','service':'Service','deployment':'Deployment'}
            def kubectl(*args,data=None,**kwargs):
                if args[:2]==('create','--dry-run=client'):
                    return json.dumps({'apiVersion':'fixture/v1','kind':kinds[data.decode().strip()], 'metadata':{'name':'saferus-console'}}).encode()
                if args[0]=='get':
                    kind=args[1]
                    if kind=='Deployment':
                        current[kind]={'metadata':{'uid':'concurrent-owner'},'image':'promoted'}
                        return b''  # Original GET observed absence; object appears afterward.
                    return b''
                if args[0]=='create':
                    value=json.loads(data);kind=value['kind'];writes.append(kind)
                    if kind in current:raise ValueError('AlreadyExists')
                    value['metadata']['uid']='owned-'+kind;current[kind]=value
                    return json.dumps(value).encode()
                raise AssertionError('Unexpected reconciliation operation')
            d.kubectl=kubectl
            with self.assertRaisesRegex(ValueError,'AlreadyExists'):
                d.create_console(b'policy\n---\nservice\n---\ndeployment')
            self.assertEqual(writes,['NetworkPolicy','Service','Deployment'])
            self.assertEqual(current['Deployment'],{'metadata':{'uid':'concurrent-owner'},'image':'promoted'})
            self.assertEqual(set(json.loads((d.state/'console-resources.json').read_text())),{'NetworkPolicy','Service'})

    def test_tls_secret_create_race_preserves_concurrent_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            d,_=self.fixture(directory);d.cluster=lambda:None
            private_write(d.state/'environment-intent.json',{'nonce':'a'*48})
            foreign={'metadata':{'uid':'foreign-secret'},'data':{'tls.key':'concurrent-bytes'}}
            writes=[]
            def kubectl(*args,data=None,**kwargs):
                if args[0]=='get':return b''
                self.assertEqual(args[0],'create')
                writes.append(args[0])
                raise ValueError('AlreadyExists')
            d.kubectl=kubectl
            with self.assertRaisesRegex(ValueError,'AlreadyExists'):
                d.create_console_resource({'kind':'Secret','metadata':{'name':'saferus-console-tls'},'data':{'tls.key':'baseline-bytes'}})
            self.assertEqual(writes,['create'])
            self.assertEqual(foreign['data']['tls.key'],'concurrent-bytes')
            self.assertFalse((d.state/'console-resources.json').exists())

    def test_live_verification_image_ignores_saved_baseline_and_requires_stability(self):
        import copy
        with tempfile.TemporaryDirectory() as directory:
            d,_=self.fixture(directory)
            private_write(d.state/'values.json',{'console':{'image':'localhost/baseline@sha256:'+'a'*64}})
            live={'metadata':{'uid':'active-uid','generation':2},'spec':{'replicas':1,'template':{'spec':{'containers':[{'name':'console','image':'registry.invalid/promoted@sha256:'+'b'*64}]}}},'status':{'observedGeneration':2,'replicas':1,'updatedReplicas':1,'readyReplicas':1,'availableReplicas':1}}
            d.get=lambda *args:live
            self.assertEqual(d.live_console()['image'],'registry.invalid/promoted@sha256:'+'b'*64)
            original=copy.deepcopy(live)
            for key in ('observedGeneration','replicas','updatedReplicas','readyReplicas','availableReplicas'):
                live=copy.deepcopy(original);live['status'][key]=0
                with self.assertRaisesRegex(ValueError,'stable'):d.live_console()

    def test_verification_refuses_image_changed_after_selection(self):
        from tools import console
        d=object.__new__(Environment)
        with patch.object(d,'live_console',return_value={'uid':'x','generation':2,'image':'new'}),patch.object(console,'probe') as probe:
            with self.assertRaisesRegex(ValueError,'changed before'):
                console.verify(d,'old')
            probe.assert_not_called()
