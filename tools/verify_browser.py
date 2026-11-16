"""Exercise the real console with normal X.509 validation and isolated CA trust."""
import argparse
import json
from pathlib import Path
import subprocess
import shutil
import tempfile

from playwright.sync_api import sync_playwright, expect
from tools.demo import Demo


def trusted_context(playwright, ca, profile):
    certutil=shutil.which('certutil')
    if certutil is None:raise ValueError('Browser verification requires NSS certutil (nss-tools or libnss3-tools)')
    subprocess.run([certutil,'-N','-d','sql:'+str(profile),'--empty-password'],check=True,capture_output=True,timeout=10)
    subprocess.run([certutil,'-A','-d','sql:'+str(profile),'-n','saferus-demo-ca','-t','C,,','-i',str(ca)],check=True,capture_output=True,timeout=10)
    return playwright.firefox.launch_persistent_context(str(profile),ignore_https_errors=False,
        firefox_user_prefs={'security.enterprise_roots.enabled':False,
            'security.certerrors.mitm.auto_enable_enterprise_roots':False})


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path)
    parser.add_argument('--ai-sdlc-state',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--provider',default='podman',choices=['podman','docker'])
    args=parser.parse_args()
    if args.state is None:
        args.state=Path.home()/'.local/state/saferus'/('ai-sdlc-production' if args.ai_sdlc_state else 'demo')
    if args.ai_sdlc_state:
        from tools.test_environment import Environment
        if not (args.state/'environment-ownership.json').exists():raise ValueError('No installed test environment')
        d=Environment(args)
    else:
        if not (args.state/'ownership.json').exists():raise ValueError('No installed demo')
        d=Demo(args)
    d.cluster()
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    with d.api('deployment/saferus-console',8443) as api, sync_playwright() as playwright, tempfile.TemporaryDirectory(prefix="saferus-browser-") as profile:
        origin=api.origin.removesuffix('/v1/')
        context=trusted_context(playwright,d.state/'ca.crt',Path(profile))
        try:
            context.route('**/*',lambda route: route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
            for name,width,height in [('desktop',1280,900),('mobile',390,844)]:
                page=context.new_page()
                page.set_viewport_size({'width':width,'height':height})
                page.goto(origin,wait_until='networkidle')
                expect(page.get_by_role('heading',name='Verify workload access')).to_be_visible()
                page.get_by_role('button',name='Run verification').click()
                expect(page.get_by_role('status')).to_contain_text('Verified.',timeout=15000)
                expect(page.locator('#algorithm')).to_have_text('AES-256-GCM')
                expect(page.locator('#ttl')).to_have_text('60 seconds')
                if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                    raise ValueError('Horizontal overflow in '+name)
                page.screenshot(path=str(args.output/(name+'.png')),full_page=True)
                page.close()
        finally:
            context.close()
    report={'status':'passed','desktop':True,'mobile':True,'realWorkloadAPI':True,
        'tlsTrust':'normal Firefox X.509 verification; only demo CA added to a disposable NSS profile',
        'autonomousAISDLCE2E':False}
    (args.output/'browser.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
