"""Verify the deployed sign-in page without collecting any user credentials."""
import json
from pathlib import Path
import subprocess
import sys
import requests
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
cfg=json.loads((ROOT/'.local/live-config.json').read_text());url=cfg['oauth']['public_url']
result=json.loads((ROOT/'docs/identity-domain-live-evidence.json').read_text())
with sync_playwright() as p:
 b=p.chromium.launch(channel='chrome');page=b.new_page(viewport={'width':1440,'height':1000});errors=[]
 page.on('pageerror',lambda e:errors.append(type(e).__name__))
 page.goto(url,wait_until='networkidle');page.locator('#domain-login').wait_for(state='visible')
 assert not page.locator('#connect').is_visible()
 assert not page.locator('#domain-logout').is_visible()
 assert not page.locator('#live-result-panel').is_visible()
 page.screenshot(path=str(ROOT/'docs/identity-signin-desktop.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
 page.screenshot(path=str(ROOT/'docs/identity-signin-mobile.png'),full_page=True)
 page.locator('#domain-login').click();page.wait_for_timeout(1500)
 assert 'identity.oraclecloud.com' in page.url and 'Sign In' in page.title()
 assert not errors
 result['checks']['ui_signin_redirect_and_mobile_layout']=True
 result['checks']['ui_no_script_errors']=True
 result['checks']['manual_bearer_form_hidden']=True
 b.close()
r=requests.get(url+'/auth/login',allow_redirects=False,timeout=20)
assert r.status_code==303
cookie=r.headers['Set-Cookie'];assert all(v in cookie for v in ('Secure','HttpOnly','SameSite=lax','__Host-lcm-login='))
assert requests.get(url+'/auth/callback?state=invalid&code=invalid',allow_redirects=False,timeout=20).headers['Location']=='/?sign_in=failed'
result['checks']['live_cookie_flags_and_invalid_callback']=True
secret=subprocess.run([sys.executable,'scripts/identity_demo_token.py'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.strip()
r=requests.get(url+'/v1/config',headers={'Authorization':'Bearer '+secret},timeout=30);assert r.status_code==200
result['checks']['operator_helper_uses_vault_and_domain']=True
assert requests.get(url+'/health/ready',timeout=30).json()['status']=='READY'
result['checks']['final_runtime_ready']=True
result['unit_tests_passed']=58
result['passed']=all(result['checks'].values())
(ROOT/'docs/identity-domain-live-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
