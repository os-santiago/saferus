"""Exercise the real local console. Trust only its known certificate SPKI."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from playwright.sync_api import sync_playwright, expect
from tools.demo import Demo


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',type=Path,default=Path.home()/'.local/state/saferus/demo')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--provider',default='podman',choices=['podman','docker'])
    args=parser.parse_args()
    if not (args.state/'ownership.json').exists():raise ValueError('No installed demo')
    d=Demo(args);d.cluster()
    public=subprocess.check_output(['openssl','x509','-in',str(d.state/'console.crt'),'-pubkey','-noout'])
    der=subprocess.check_output(['openssl','pkey','-pubin','-outform','DER'],input=public)
    pin=base64.b64encode(hashlib.sha256(der).digest()).decode()
    args.output.mkdir(mode=0o700,parents=True,exist_ok=True)
    with d.api('deployment/saferus-console',8443) as api, sync_playwright() as playwright:
        origin=api.origin.removesuffix('/v1/')
        browser=playwright.chromium.launch(args=['--ignore-certificate-errors-spki-list='+pin])
        try:
            for name,width,height in [('desktop',1280,900),('mobile',390,844)]:
                context=browser.new_context(viewport={'width':width,'height':height})
                context.route('**/*',lambda route: route.continue_() if route.request.url.startswith(origin+'/') else route.abort())
                page=context.new_page()
                page.goto(origin,wait_until='networkidle')
                expect(page.get_by_role('heading',name='Verify workload access')).to_be_visible()
                page.get_by_role('button',name='Run verification').click()
                expect(page.get_by_role('status')).to_contain_text('Verified.',timeout=15000)
                expect(page.locator('#algorithm')).to_have_text('AES-256-GCM')
                expect(page.locator('#ttl')).to_have_text('60 seconds')
                if not page.evaluate('document.documentElement.scrollWidth <= innerWidth'):
                    raise ValueError('Horizontal overflow in '+name)
                page.screenshot(path=str(args.output/(name+'.png')),full_page=True)
                context.close()
        finally:
            browser.close()
    report={'status':'passed','desktop':True,'mobile':True,'realWorkloadAPI':True,
        'tlsTrust':'explicit locally installed server certificate SPKI; no blanket HTTPS bypass',
        'autonomousAISDLCE2E':False}
    (args.output/'browser.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report))


if __name__=='__main__':main()
