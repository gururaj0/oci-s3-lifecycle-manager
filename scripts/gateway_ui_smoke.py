"""Browser checks for the split UI; does not automate user credentials."""
import json
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/live-config.json').read_text());url=cfg['oauth']['public_url']
checks={}
with sync_playwright() as p:
 b=p.chromium.launch(channel='chrome');page=b.new_page(viewport={'width':1440,'height':1000});errors=[]
 page.on('pageerror',lambda e:errors.append(type(e).__name__))
 page.goto(url,wait_until='networkidle');page.locator('#domain-login').wait_for(state='visible')
 assert not page.locator('#connect').is_visible()
 page.screenshot(path=str(ROOT/'docs/gateway-signin-desktop.png'))
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.screenshot(path=str(ROOT/'docs/gateway-signin-mobile.png'))
 checks['signin_ui_and_mobile_layout']=True
 page.locator('#domain-login').click();page.wait_for_timeout(1500)
 assert 'identity.oraclecloud.com' in page.url and 'Sign In' in page.title()
 checks['real_identity_domain_redirect']=True
 assert not errors;checks['no_browser_errors']=True
 b.close()
r=requests.get(url+'/openapi.json',timeout=30);r.raise_for_status()
assert '/v1/leases' in r.json()['paths']
checks['public_api_reference_preserved']=True
r=requests.get(url+'/auth/login',allow_redirects=False,timeout=30)
assert r.status_code==303 and all(s in r.headers['set-cookie'] for s in ('HttpOnly','Secure','SameSite=lax'))
checks['secure_login_cookie']=True
assert requests.get(url+'/auth/callback?state=invalid&code=invalid',allow_redirects=False,timeout=30).headers['location']=='/?sign_in=failed'
checks['invalid_callback_rejected']=True
r=requests.get(url+'/health/ready',timeout=30);assert r.status_code==200 and r.json()['status']=='READY'
checks['live_readiness']=True
out={'checks':checks,'passed':all(checks.values()),'interactive_sign_in':'pending user confirmation after gateway split'}
(ROOT/'docs/gateway-ui-evidence.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out,indent=2))
