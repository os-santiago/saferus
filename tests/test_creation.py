import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import Demo, CLUSTER, private_write


class CreationRecoveryTests(unittest.TestCase):
    def fixture(self, directory, failure):
        demo=object.__new__(Demo);demo.state=Path(directory)
        demo.env={'KIND_EXPERIMENTAL_PROVIDER':'podman'};demo.tools={'kind':'kind'}
        state={'live':False,'creates':0,'partial':False,'wrongNonce':False,'failure':failure}
        def run(command,**kwargs):
            if command[0]=='podman':return b'partial-container\n' if state['partial'] else b''
            if command[1]=='get':return (CLUSTER+'\n').encode() if state['live'] else b''
            if command[1]!='create':raise AssertionError('Unexpected provider mutation')
            self.assertTrue((demo.state/'creation-intent.json').is_file())
            nonce=json.loads((demo.state/'creation-intent.json').read_text())['nonce']
            self.assertLessEqual(len(nonce),63)  # Kubernetes label-value limit.
            state['creates']+=1
            if state['failure']=='before-node':raise OSError('fixture failed before node creation')
            if state['failure']=='partial':
                state['partial']=True
                raise OSError('fixture left an unidentified provider resource')
            state['live']=True;private_write(demo.state/'kubeconfig',b'fixture')
            if state['failure']=='lost-reply':raise OSError('fixture lost successful creation response')
            return b''
        demo.run=run
        def kubectl(*args):
            self.assertEqual(args,('get','nodes','-o','json'))
            nonce=json.loads((demo.state/'creation-intent.json').read_text())['nonce']
            return json.dumps({'items':[{'metadata':{'name':CLUSTER+'-control-plane',
                'labels':{'saferus.io/creation':'wrong' if state['wrongNonce'] else nonce}}}]}).encode()
        demo.kubectl=kubectl
        demo.get=lambda *args:{'metadata':{'uid':'original-cluster-uid'}}
        return demo,state

    def test_lost_create_reply_recovers_original_identity_without_recreate(self):
        with tempfile.TemporaryDirectory() as directory:
            demo,state=self.fixture(directory,'lost-reply')
            with self.assertRaises(OSError):demo.cluster()
            self.assertFalse((demo.state/'ownership.json').exists())
            demo.cluster()
            self.assertEqual(state['creates'],1)
            self.assertEqual(json.loads((demo.state/'ownership.json').read_text())['clusterUID'],'original-cluster-uid')

    def test_receipt_write_failure_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            demo,state=self.fixture(directory,None)
            def write(path,value):
                if path.name=='ownership.json':raise OSError('fixture receipt persistence failure')
                private_write(path,value)
            with patch('tools.demo.private_write',side_effect=write),self.assertRaises(OSError):demo.cluster()
            demo.cluster()
            self.assertEqual(state['creates'],1)

    def test_wrong_nonce_or_provider_cannot_be_adopted(self):
        for change in ('nonce','provider'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as directory:
                demo,state=self.fixture(directory,'lost-reply')
                with self.assertRaises(OSError):demo.cluster()
                if change=='nonce':state['wrongNonce']=True
                else:demo.env['KIND_EXPERIMENTAL_PROVIDER']='docker'
                with self.assertRaises(ValueError):demo.cluster()
                self.assertFalse((demo.state/'ownership.json').exists())
                self.assertEqual(state['creates'],1)

    def test_no_cluster_can_restart_or_clear_its_owned_attempt(self):
        for operation in ('restart','clear'):
            with self.subTest(operation=operation),tempfile.TemporaryDirectory() as directory:
                demo,state=self.fixture(directory,'before-node')
                with self.assertRaises(OSError):demo.cluster()
                if operation=='restart':
                    state['failure']=None;demo.cluster()
                    self.assertEqual(state['creates'],2)
                else:
                    demo.destroy()
                    self.assertFalse((demo.state/'creation-intent.json').exists())
                    self.assertFalse((demo.state/'kind.json').exists())

    def test_partial_provider_resources_are_preserved_without_name_only_adoption(self):
        with tempfile.TemporaryDirectory() as directory:
            demo,state=self.fixture(directory,'partial')
            with self.assertRaises(OSError):demo.cluster()
            for action in (demo.cluster,demo.destroy):
                with self.assertRaises(ValueError):action()
            self.assertTrue((demo.state/'creation-intent.json').exists())
            self.assertEqual(state['creates'],1)
