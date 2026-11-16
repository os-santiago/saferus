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
