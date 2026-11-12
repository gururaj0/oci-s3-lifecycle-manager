"""Real-browser verification; screenshots never include credential response material."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

p=argparse.ArgumentParser();p.add_argument('url');p.add_argument('--out',default='docs');args=p.parse_args()
out=Path(args.out);out.mkdir(exist_ok=True)
errors=[]
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1512,'height':1180},device_scale_factor=1)
    page.on('pageerror',lambda error: errors.append(str(error)))
    response=page.goto(args.url,wait_until='networkidle')
    assert response.status==200
    page.get_by_text('Demo ready',exact=False).wait_for(timeout=20000)
    page.screenshot(path=str(out/'demo-desktop.png'),full_page=True)
    page.wait_for_timeout(1500)
    page.locator('#profile').select_option('sales-write')
    page.locator('#ttl').select_option('300')
    page.locator('#request-id').fill('browser-acceptance')
    page.locator('#issue').click()
    page.locator('#credential-panel').wait_for(state='visible',timeout=20000)
    page.wait_for_timeout(1500)
    assert page.locator('#secret').get_attribute('type')=='password'
    page.wait_for_timeout(1500)
    page.locator('#probe-op').select_option('GET');page.locator('#probe').click()
    expect(page.locator('#probe-result')).to_contain_text('HTTP 200',timeout=15000)
    page.wait_for_timeout(1500)
    page.locator('#probe-op').select_option('FOREIGN');page.locator('#probe').click()
    expect(page.locator('#probe-result')).to_contain_text('HTTP 403',timeout=15000)
    # Revoke only this browser session's lease, and test the retained synthetic credential.
    page.wait_for_timeout(1500)
    page.get_by_role('button',name='Revoke',exact=True).first.click()
    page.wait_for_timeout(1500)
    page.wait_for_timeout(1500)
    page.locator('#probe-op').select_option('GET');page.locator('#probe').click()
    expect(page.locator('#probe-result')).to_contain_text('HTTP 403',timeout=15000)
    page.locator('#hide-credentials').click()
    page.locator('body').evaluate('(e)=>window.scrollTo(0,0)')
    page.wait_for_timeout(5500)
    assert not page.locator('#toast').is_visible() or 'Failed' not in page.locator('#toast').inner_text()
    page.screenshot(path=str(out/'demo-desktop-tested.png'),full_page=True)
    page.set_viewport_size({'width':390,'height':844})
    page.locator('body').evaluate('(e)=>window.scrollTo(0,0)')
    page.screenshot(path=str(out/'demo-mobile.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
    assert not errors,errors
    browser.close()
(out/'browser-evidence.json').write_text(json.dumps({'url':args.url,'mode':'mock','desktop':True,'mobile_390px_no_overflow':True,
    'create_lease':True,'secret_masked':True,'signed_s3_read':True,'foreign_bucket_denied':True,'revoked_s3_denied':True,'console_errors':errors},indent=2)+'\n')
print('Browser checks passed: desktop/mobile, issuance, masked credentials, S3 access/denials and revocation; no JS errors.')
