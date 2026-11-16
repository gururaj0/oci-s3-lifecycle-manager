"""Read-only live UI checks. No credential issuance or token/secret screenshots."""
import json,subprocess,sys
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'.local/live-config.json').read_text())
token=subprocess.run([sys.executable,str(root/'scripts/live_demo_token.py')],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1100})
 page.on('pageerror',lambda e:errors.append(type(e).__name__))
 r=page.goto(cfg['public_url'],wait_until='networkidle');assert r.status==200
 page.locator('#bearer').fill(token)
 page.locator('#connect button').click()
 expect(page.locator('#live-status')).to_contain_text('Verified identity connected',timeout=20000)
 assert page.locator('#bearer').input_value()==''
 assert page.locator('#live-result-panel').is_hidden()
 page.screenshot(path=str(root/'docs/live-desktop.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844})
 assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
 page.screenshot(path=str(root/'docs/live-mobile.png'),full_page=True)
 assert not errors
 browser.close()
out={'mode':'live-oci','jwt_connection':True,'token_input_cleared':True,'credential_panel_hidden':True,
 'desktop':True,'mobile_390px_no_overflow':True,'script_errors':errors,'customer_keys_created':False}
(root/'docs/live-browser-evidence.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
