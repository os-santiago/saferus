import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.demo import Demo, CLUSTER, private_write


class VaultFixture:
    def __init__(self, fail=None):
        self.fail=fail;self.failed=False;self.mounts={};self.seed=False;self.key=False;self.auth=False
        self.root=True;self.seed_writes=0;self.mount_writes=0;self.revocations=0

    def call(self, method, path, body=None, token=None, expected=(200,204)):
        if path=='sys/health':return {'initialized':True,'sealed':False}
        if path=='sys/leader':return {'is_self':True}
        if path=='sys/audit':return {'data':{'saferus/':{'type':'file','options':{'file_path':'/openbao/audit/audit.json'}}}}
        if path=='sys/mounts':return {'data':dict(self.mounts)}
        result={}
        if path.startswith('sys/mounts/'):
            name=path.removeprefix('sys/mounts/')+'/'
            if name in self.mounts:raise AssertionError('Mount replayed')
            self.mounts[name]=body;self.mount_writes+=1
        elif path=='secret/data/demo/service':
            if method=='GET':return {'data':{'data':{'password':'a'*64}}} if self.seed else {}
            if self.seed:raise AssertionError('Secret overwritten')
            self.seed=True;self.seed_writes+=1
        elif path=='transit/keys/demo':
            if method=='GET':return {'data':{'type':'aes256-gcm96','exportable':False,'allow_plaintext_backup':False}} if self.key else {}
            if self.key:raise AssertionError('Key replayed')
            self.key=True
        elif path=='sys/auth':return {'data':{'kubernetes/':{'type':'kubernetes'}} if self.auth else {}}
        elif path=='sys/auth/kubernetes':self.auth=True
        elif path=='auth/token/lookup-self':
            if self.root:
                if 200 not in expected:raise AssertionError('Root still active')
                return {'data':{'policies':['root']}}
            if 403 not in expected:raise AssertionError('Unexpected root rejection')
            return {'errors':['permission denied']}
        elif path=='auth/token/revoke-self':
            if not self.root:raise AssertionError('Root revoke replayed')
            self.root=False;self.revocations+=1
        if (method,path)==self.fail and not self.failed:
            self.failed=True
            raise OSError('fixture lost response after applied effect')
        return result


class BootstrapRecoveryTests(unittest.TestCase):
    def test_interruption_preserves_secret_key_and_revokes_original_root(self):
        for failure in [('POST','sys/mounts/secret'),('POST','secret/data/demo/service'),
                        ('POST','transit/keys/demo'),('POST','auth/kubernetes/role/demo'),('POST','auth/token/revoke-self')]:
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                d=object.__new__(Demo);d.state=Path(directory)
                private_write(d.state/'demo-custody.json',{'keys_base64':[],'root_token':'fixture-root'})
                api=VaultFixture(failure)
                with self.assertRaises(OSError):d.bootstrap(api)
                d.bootstrap(api)
                self.assertEqual(api.seed_writes,1);self.assertEqual(api.mount_writes,2);self.assertEqual(api.revocations,1)
                self.assertTrue(json.loads((d.state/'configured.json').read_text())['rootRevoked'])
                self.assertNotIn('root_token',json.loads((d.state/'demo-custody.json').read_text()))

    def test_completion_write_failure_after_root_removal_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            d=object.__new__(Demo);d.state=Path(directory)
            private_write(d.state/'demo-custody.json',{'keys_base64':[],'root_token':'fixture-root'})
            def interrupted(path,data):
                if path.name=='configured.json':raise OSError('fixture persistence interruption')
                private_write(path,data)
            api=VaultFixture()
            with patch('tools.demo.private_write',side_effect=interrupted),self.assertRaises(OSError):d.bootstrap(api)
            d.bootstrap(api)
            self.assertEqual(api.revocations,1)
            self.assertNotIn('root_token',json.loads((d.state/'demo-custody.json').read_text()))


class DestroyRecoveryTests(unittest.TestCase):
    def fixture(self, directory, fail_delete=False, uid='fixture-uid', live=True):
        d=object.__new__(Demo);d.state=Path(directory);d.env={'KIND_EXPERIMENTAL_PROVIDER':'podman'};d.tools={'kind':'kind'}
        private_write(d.state/'ownership.json',{'cluster':CLUSTER,'provider':'podman','clusterUID':'fixture-uid'})
        private_write(d.state/'demo-custody.json',{'fixture':True})
        state={'live':live,'deletes':0}
        def run(cmd,**kwargs):
            if cmd[1]=='get':return (CLUSTER+'\n').encode() if state['live'] else b''
            if cmd[1]=='delete':
                state['live']=False;state['deletes']+=1
                if fail_delete:raise OSError('fixture lost delete response')
                return b''
            raise AssertionError('Unexpected command')
        d.run=run;d.get=lambda *args:{'metadata':{'uid':uid}}
        return d,state

    def test_lost_delete_response_resumes_local_cleanup_only(self):
        with tempfile.TemporaryDirectory() as directory:
            d,s=self.fixture(directory,fail_delete=True)
            with self.assertRaises(OSError):d.destroy()
            self.assertTrue((d.state/'destroy-intent.json').exists())
            d.destroy()
            self.assertEqual(s['deletes'],1)
            self.assertFalse((d.state/'demo-custody.json').exists())
            self.assertFalse((d.state/'ownership.json').exists())

    def test_wrong_uid_or_missing_cluster_never_authorizes_cleanup(self):
        for options in [{'uid':'replacement-uid'},{'live':False}]:
            with self.subTest(options=options),tempfile.TemporaryDirectory() as directory:
                d,s=self.fixture(directory,**options)
                with self.assertRaises(ValueError):d.destroy()
                self.assertEqual(s['deletes'],0)
                self.assertTrue((d.state/'demo-custody.json').exists())

    def test_symlink_is_rejected_before_cluster_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            d,s=self.fixture(directory)
            (d.state/'ca.key').symlink_to(d.state/'demo-custody.json')
            with self.assertRaises(ValueError):d.destroy()
            self.assertEqual(s['deletes'],0)

    def test_interrupted_file_cleanup_keeps_deletion_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            d,s=self.fixture(directory)
            original=Path.unlink
            def interrupt(path,*args,**kwargs):
                if path.name=='demo-custody.json':raise OSError('fixture unlink interrupted')
                return original(path,*args,**kwargs)
            with patch.object(Path,'unlink',interrupt),self.assertRaises(OSError):d.destroy()
            self.assertTrue((d.state/'ownership.json').exists())
            d.destroy()
            self.assertEqual(s['deletes'],1)
