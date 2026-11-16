"""Bootstrap Saferus test production in the installation-owned AI-SDLC cluster.

This provisions baseline infrastructure only. It never creates workflow events,
invokes AI-SDLC stages, deletes a cluster, or changes an application image after
an autonomous deployment.
"""
import json
import os
from pathlib import Path
import re
import secrets
import stat

from .demo import Demo, private_write


class Environment(Demo):
    namespace = 'ai-sdlc-production'
    cluster_name = 'ai-sdlc-saferus'

    def __init__(self, args):
        super().__init__(args)
        self.installation = args.ai_sdlc_state.absolute()
        if self.installation == self.state:
            raise ValueError('Application custody must be separate from AI-SDLC state')

    def private_input(self, path, public=False):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & (0o022 if public else 0o077) or st.st_size > 65536:
                raise ValueError('Installation input must be an owned private bounded regular file')
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(65537)
            if len(raw) > 65536:
                raise ValueError('Installation input exceeds bound')
            return raw
        finally:
            os.close(fd)

    def cluster(self):
        marker = json.loads(self.private_input(self.installation / 'ownership.json', public=True))
        if (set(marker) != {'cluster', 'provider', 'clusterUID'} or marker['cluster'] != self.cluster_name
                or marker['provider'] != self.env['KIND_EXPERIMENTAL_PROVIDER']
                or not re.fullmatch(r'[a-f0-9-]{36}', marker['clusterUID'])):
            raise ValueError('Expected the dedicated AI-SDLC Saferus installation owner')
        kubeconfig = self.private_input(self.installation / 'kubeconfig')
        destination = self.state / 'kubeconfig'
        if destination.exists() or destination.is_symlink():
            if self.private_input(destination) != kubeconfig:
                raise ValueError('Installed Kubernetes client changed; refusing implicit adoption')
        else:
            private_write(destination, kubeconfig)
        live = self.get('namespace', 'kube-system', 'default')['metadata']['uid']
        if marker['clusterUID'] != live:
            raise ValueError('AI-SDLC cluster UID differs from its owner record')
        intent_path = self.state / 'environment-intent.json'
        owner_path = self.state / 'environment-ownership.json'
        if intent_path.exists() or intent_path.is_symlink():
            intent = json.loads(self.private_input(intent_path))
            if (set(intent) != {'clusterUID', 'namespace', 'nonce'} or intent['clusterUID'] != live
                    or intent['namespace'] != self.namespace or not re.fullmatch(r'[a-f0-9]{48}', intent['nonce'])):
                raise ValueError('Test environment creation intent differs')
        else:
            if owner_path.exists() or owner_path.is_symlink():
                raise ValueError('Test environment ownership lacks creation intent')
            intent = {'clusterUID': live, 'namespace': self.namespace, 'nonce': secrets.token_hex(24)}
            private_write(intent_path, intent)
        raw = self.kubectl('get', 'namespace', self.namespace, '--ignore-not-found', '-o', 'json')
        if raw.strip():
            namespace = json.loads(raw)
        else:
            if owner_path.exists() or owner_path.is_symlink():
                raise ValueError('Owned test namespace disappeared; refusing recreation')
            namespace = json.loads(self.kubectl('create', '-f', '-', '-o', 'json', data=json.dumps({
                'apiVersion':'v1', 'kind':'Namespace', 'metadata':{'name':self.namespace,
                'annotations':{'saferus.io/creation-nonce':intent['nonce'], 'saferus.io/cluster-uid':live},
                'labels':{'pod-security.kubernetes.io/enforce':'restricted', 'saferus.io/scope':'local-demo'}}}).encode()))
        meta = namespace['metadata']
        if (meta.get('annotations', {}).get('saferus.io/creation-nonce') != intent['nonce']
                or meta.get('annotations', {}).get('saferus.io/cluster-uid') != live
                or meta.get('deletionTimestamp') or not meta.get('uid')):
            raise ValueError('Refusing to adopt a foreign or terminating test namespace')
        expected = {'cluster':self.cluster_name, 'clusterUID':live, 'namespace':self.namespace, 'namespaceUID':meta['uid']}
        if owner_path.exists() or owner_path.is_symlink():
            if json.loads(self.private_input(owner_path)) != expected:
                raise ValueError('Test namespace UID changed')
        else:
            private_write(owner_path, expected)

    def destroy(self):
        raise ValueError('Test environment setup cannot delete the shared AI-SDLC cluster')

    def install(self):
        self.cluster()
        # Reapplying bootstrap after an autonomous image promotion must preserve
        # that image; otherwise setup would silently roll back successful work.
        raw = self.kubectl('get', 'deployment', 'saferus-console', '-n', self.namespace, '--ignore-not-found', '-o', 'json')
        if raw.strip():
            live = json.loads(raw)
            containers = live.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
            saved = self.state / 'values.json'
            expected = json.loads(saved.read_text()).get('console', {}).get('image') if saved.exists() else None
            if len(containers) != 1 or containers[0].get('image') != expected:
                raise ValueError('Console image changed since bootstrap; use verification, not baseline reinstallation')
        super().install()
