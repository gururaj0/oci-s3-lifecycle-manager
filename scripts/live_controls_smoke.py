"""Exercise live diagnostic controls. Revoke only the lease created by this test."""
import json
from pathlib import Path
import subprocess
import sys
import time
import httpx
from playwright.sync_api import sync_playwright, expect

root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'.local/live-config.json').read_text())
token=subprocess.run([sys.executable,str(root/'scripts/live_demo_token.py')],cwd=root,capture_output=True,text=True,check=True).stdout.strip()
result={'mode':'oci','credentials_logged':False,'checks':{},'operations':{},'propagation_observations':[]}
lease=None
with httpx.Client(base_url=cfg['public_url'],headers={'Authorization':'Bearer '+token},timeout=150) as api:
 with sync_playwright() as p:
  browser=p.chromium.launch();page=browser.new_page(viewport={'width':1440,'height':1200});errors=[]
  page.on('pageerror',lambda e:errors.append(type(e).__name__))
  try:
   page.goto(cfg['public_url'],wait_until='networkidle')
   page.locator('#bearer').fill(token);page.locator('#connect button').click()
   expect(page.locator('#live-status')).to_contain_text('Verified identity connected',timeout=30000)
   expect(page.locator('#live-user-rows tr')).to_have_count(4)
   expect(page.locator('#live-fixtures')).to_contain_text('quarterly/revenue.csv')
   expect(page.locator('#live-heartbeat')).to_have_text('Healthy',timeout=15000)
   result['checks']['inventory_metrics_and_fixtures']=True
   page.locator('#live-profile').select_option('sales-write');page.locator('#live-ttl').select_option('900')
   with page.expect_response(lambda r:r.url.endswith('/v1/leases') and r.request.method=='POST',timeout=150000) as pending:page.locator('#live-create').click()
   r=pending.value;assert r.status==201,'No free test lease or issuance failed';lease=r.json()
   expect(page.locator('#live-result-panel')).to_be_visible()
   def probe(operation):
    page.locator('#live-probe-op').select_option(operation)
    with page.expect_response(lambda r:r.url.endswith('/tests') and r.request.method=='POST',timeout=45000) as pending:page.locator('#live-probe').click()
    response=pending.value;assert response.status==200,'Diagnostic dependency failure'
    body=response.json();expect(page.locator('#live-probe')).to_be_enabled()
    return body
   deadline=time.monotonic()+300
   for operation in ('GET','LIST','PUT','DELETE','FOREIGN'):
    while True:
     data=probe(operation)
     result['propagation_observations'].append({k:data[k] for k in ('operation','http_status','matches_expectation')})
     if data['matches_expectation']:break
     # OCI can alternate 200/403 during new-key propagation. Preserve observations;
     # retry positive checks only. Unexpected negative-scope access never gets hidden.
     assert operation in ('GET','LIST','PUT') and time.monotonic()<deadline,operation+' expectation mismatch'
     time.sleep(5)
    result['operations'][operation]={k:data[k] for k in ('http_status','expected_access','observed_access','matches_expectation')}
    assert data['matches_expectation'],operation+' expectation mismatch'
    assert lease['secret_access_key'] not in json.dumps(data) and '124000' not in json.dumps(data)
   result['checks']['all_five_native_operations']=True
   row=page.locator('#live-rows tr').filter(has_text=lease['lease_id'][:8]);row.get_by_role('button',name='Revoke',exact=True).click()
   for attempt in range(60):
    if api.get('/v1/leases/'+lease['lease_id']).json()['status']=='REVOKED':break
    time.sleep(2)
   else:raise AssertionError('Test lease revocation unconfirmed')
   data=probe('GET');result['after_revocation_observation']={k:data[k] for k in ('http_status','expected_access','observed_access','matches_expectation')}
   result['checks']['repeat_probe_after_revocation']=True
   metadata=[api.get(path).text for path in ('/v1/leases','/v1/metrics','/v1/audit','/v1/test-setup')]
   assert all(lease['secret_access_key'] not in x and lease['access_key_id'] not in x for x in metadata)
   result['checks']['metadata_and_audit_redacted']=True
   expect(page.locator('#live-events')).to_contain_text('S3 TEST',timeout=15000)
   page.locator('#live-clear').click();assert page.locator('#live-secret').input_value()==''
   page.screenshot(path=str(root/'docs/live-desktop.png'),full_page=True)
   page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   page.screenshot(path=str(root/'docs/live-mobile.png'),full_page=True)
   result['checks']['mobile_no_overflow_and_secret_fields_cleared']=True
   assert not errors;result['checks']['no_browser_script_errors']=True
  finally:
   if lease:api.delete('/v1/leases/'+lease['lease_id'])
   browser.close()
result['passed']=all(result['checks'].values())
(root/'docs/live-controls-evidence.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))
