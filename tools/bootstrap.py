"""Fetch digest-pinned Linux amd64 clients without privileged package changes."""
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import platform
import stat
import tarfile
import tempfile
import urllib.request
import urllib.parse

LOCK=json.loads(Path(__file__).with_name('install-tools.lock.json').read_text())
HOSTS={'github.com','release-assets.githubusercontent.com','objects.githubusercontent.com','get.helm.sh','dl.k8s.io','cdn.dl.k8s.io'}


def trusted_url(value):
    parsed=urllib.parse.urlparse(value)
    return parsed.scheme=='https' and parsed.hostname in HOSTS and parsed.port in (None,443) and parsed.username is None and parsed.password is None


class DownloadRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections=3
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not trusted_url(newurl):raise ValueError('Untrusted client download redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def bounded_process(target, args, timeout=120):
    """A parent deadline also interrupts a child blocked inside a socket read."""
    child=multiprocessing.get_context('spawn').Process(target=target,args=args)
    child.start()
    try:
        child.join(timeout)
        if child.is_alive():
            raise ValueError('Client download deadline exceeded')
        if child.exitcode!=0:
            raise ValueError('Client download failed')
    finally:
        if child.is_alive():
            child.terminate();child.join(2)
        if child.is_alive():
            child.kill();child.join(2)
        if not child.is_alive():child.close()


def download_child(entry, archive):
    try:
        if not trusted_url(entry['url']):raise ValueError('Untrusted client download URL')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),DownloadRedirect())
        size=0
        with opener.open(entry['url'],timeout=20) as response,Path(archive).open('xb') as stream:
            length=response.headers.get('Content-Length')
            if length is not None and (not length.isdigit() or int(length)>96*1024*1024):
                raise ValueError('Client response exceeds download budget')
            while chunk:=response.read(1024*1024):
                size+=len(chunk)
                if size>96*1024*1024:raise ValueError('Client download budget exceeded')
                stream.write(chunk)
    except Exception:
        # The parent exposes only the fixed failure class, never response bodies.
        raise SystemExit(1) from None


def ensure(name):
    if platform.system()!='Linux' or platform.machine() not in ('x86_64','amd64'):
        raise ValueError('Automatic client bootstrap supports Linux amd64; provide SAFERUS_* tools on other platforms')
    cache=Path(os.environ.get('XDG_CACHE_HOME',Path.home()/'.cache'))/'saferus/tools'
    cache.mkdir(mode=0o700,parents=True,exist_ok=True)
    if cache.is_symlink() or cache.stat().st_uid!=os.getuid() or cache.stat().st_mode & 0o022:
        raise ValueError('Unsafe tool cache directory')
    entry=LOCK[name];target=cache/(name+'-'+entry['version'])
    if target.is_symlink():raise ValueError('Tool cache contains a symlink')
    if target.exists():
        if not stat.S_ISREG(target.stat().st_mode) or hashlib.sha256(target.read_bytes()).hexdigest()!=entry['binarySha256']:
            raise ValueError('Cached client digest mismatch')
        return str(target)
    with tempfile.TemporaryDirectory(dir=cache) as directory:
        archive=Path(directory)/'download'
        bounded_process(download_child,(entry,str(archive)))
        if archive.stat().st_size>96*1024*1024 or hashlib.sha256(archive.read_bytes()).hexdigest()!=entry['sha256']:
            raise ValueError('Downloaded client digest mismatch')
        if 'member' in entry:
            with tarfile.open(archive) as tar:
                member=tar.getmember(entry['member'])
                if not member.isfile() or member.size>96*1024*1024:raise ValueError('Unexpected archive member')
                with tar.extractfile(member) as stream:
                    raw=stream.read(96*1024*1024+1)
        else:raw=archive.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=entry['binarySha256']:raise ValueError('Client binary digest mismatch')
        binary=Path(directory)/'binary';binary.write_bytes(raw);binary.chmod(0o700)
        os.replace(binary,target)
    return str(target)
