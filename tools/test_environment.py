"""Bootstrap Saferus test production in the installation-owned AI-SDLC cluster.

This provisions baseline infrastructure only. It never creates workflow events,
invokes AI-SDLC stages, deletes a cluster, or changes an application image after
an autonomous deployment.
"""
import json
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat

from .demo import Demo, private_write


class Environment(Demo):
    namespace = 'ai-sdlc-production'
    console_create_only = True
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

    def existing_console(self):
        raw = self.kubectl('get', 'deployment', 'saferus-console', '-n', self.namespace, '--ignore-not-found', '-o', 'json')
        if not raw.strip():
            return None
        live = json.loads(raw)
        containers = live.get('spec', {}).get('template', {}).get('spec', {}).get('containers', [])
        saved = self.state / 'values.json'
        expected = json.loads(saved.read_text()).get('console', {}).get('image') if saved.exists() else None
        if len(containers) != 1 or containers[0].get('image') != expected:
            raise ValueError('Console image changed since bootstrap; use verification, not baseline reinstallation')
        return expected

    def install(self):
        self.cluster()
        # Reapplication is observation-only once the console exists. In
        # particular, no Helm reconciliation may undo a concurrent promotion.
        if self.existing_console() is not None:
            return
        super().install()

    def before_console_create(self):
        # Initial setup must not adopt a console that appeared while its image
        # was being built. A running autonomous deployment has a preinstalled
        # Deployment UID; absence is not authority to create one.
        if self.existing_console() is not None:
            raise ValueError('Console appeared during baseline build; refusing reconciliation')

    def create_console(self, rendered):
        """Create-only API writes close the GET/Helm update race atomically."""
        self.cluster()
        intent=json.loads(self.private_input(self.state/'environment-intent.json'))
        objects={}
        for document in re.split(r'^---\s*$', rendered.decode(), flags=re.MULTILINE):
            if not document.strip():continue
            value=json.loads(self.kubectl('create','--dry-run=client','-n',self.namespace,'-f','-','-o','json',data=document.encode()))
            kind=value.get('kind')
            if kind not in ('Deployment','Service','NetworkPolicy') or kind in objects or value.get('metadata',{}).get('name')!='saferus-console':
                raise ValueError('Unexpected console manifest')
            objects[kind]=value
        if set(objects)!= {'Deployment','Service','NetworkPolicy'}:
            raise ValueError('Incomplete console manifests')
        path=self.state/'console-resources.json'
        owners=json.loads(self.private_input(path)) if path.exists() else {}
        # Isolation exists before any console Pod can be scheduled.
        for kind in ('NetworkPolicy','Service','Deployment'):
            value=objects[kind]
            digest=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
            value['metadata'].setdefault('annotations',{}).update({
                'saferus.io/creation-nonce':intent['nonce'],'saferus.io/baseline-manifest':digest})
            raw=self.kubectl('get',kind,'saferus-console','-n',self.namespace,'--ignore-not-found','-o','json')
            if raw.strip():
                current=json.loads(raw)
            else:
                if kind in owners:raise ValueError('Owned console resource disappeared')
                # If a resource appears after GET, create returns AlreadyExists.
                # No apply/patch/Helm fallback may overwrite the concurrent object.
                current=json.loads(self.kubectl('create','-n',self.namespace,'-f','-','-o','json',data=json.dumps(value).encode()))
            meta=current.get('metadata',{})
            annotations=meta.get('annotations',{})
            if (not meta.get('uid') or meta.get('deletionTimestamp')
                    or annotations.get('saferus.io/creation-nonce')!=intent['nonce']
                    or annotations.get('saferus.io/baseline-manifest')!=digest
                    or (kind in owners and owners[kind]!=meta['uid'])):
                raise ValueError('Console resource ownership changed')
            owners[kind]=meta['uid'];private_write(path,owners)
        self.kubectl('rollout','status','deployment/saferus-console','-n',self.namespace,'--timeout=180s',timeout=200)
