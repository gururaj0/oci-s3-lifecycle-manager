"""Browser interaction check with a synthetic session and fixture API responses.
Does not claim to complete an operator's interactive OCI sign-in.
"""
import json,time
from pathlib import Path
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
base=json.loads((ROOT/'.local/live-config.json').read_text())['oauth']['public_url']
profiles={'sales-read':{'bucket':'lcm-live-v2-sales','operations':['LIST','GET']},'sales-write':{'bucket':'lcm-live-v2-sales','operations':['LIST','GET','PUT']}}
seen=[];errors=[]
with sync_playwright() as p:
 browser=p.chromium.launch(channel='chrome');page=browser.new_page(viewport={'width':1280,'height':1000})
 page.on('pageerror',lambda e:errors.append(type(e).__name__))
 def respond(route):
  path=route.request.url.split(base)[1]
  if path=='/auth/session':data={'enabled':True,'authenticated':True,'csrf':'fixture-csrf','expires_at':time.time()+600,'login_subject':'fixture-operator','connector_subject':'fixture-connector','issuer':'fixture-issuer'}
  elif path.endswith('/config'):data={'mode':'oci','profiles':profiles}
  elif path.endswith('/test-setup'):data={'profiles':profiles,'service_users':[],'read_object':'quarterly/revenue.csv','foreign_bucket':'foreign','foreign_object':'negative.txt','delete_test_object':'delete.txt'}
  elif path.endswith('/metrics'):data={'states':{},'pools':[],'overdue':0,'healthy':True,'heartbeat_at':None}
  elif path.endswith('/audit'):data={'events':[]}
  elif path.endswith('/leases'):
   assert route.request.method=='GET','UI unexpectedly minted a lease'
   data={'leases':[]}
  elif path.endswith('/bucket-operations'):
   body=route.request.post_data_json;seen.append(body)
   assert route.request.headers.get('x-lcm-csrf')=='fixture-csrf'
   assert 'scope_profile' not in body and 'ttl_seconds' not in body
   data={'bucket':body['bucket'],'operation':body['operation'],'selected_profile':'sales-read','credential_mode':'oci_workload_identity','lease_created':False,'objects':[{'key':'quarterly/revenue.csv','size':30,'etag':'fixture'}]}
  else:raise AssertionError('Unexpected UI endpoint: '+path)
  route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
 page.route(base+'/auth/session',respond);page.route(base+'/connector/**',respond)
 page.goto(base,wait_until='networkidle')
 expect(page.locator('#auto-result')).to_contain_text('oci_workload_identity')
 assert len(seen)==1 and seen[0]=={'bucket':'lcm-live-v2-sales','operation':'LIST'}
 page.locator('#auto-operation').select_option('GET');page.locator('#auto-run').click()
 expect(page.locator('#auto-result')).to_contain_text('GET')
 assert seen[-1]['key']=='quarterly/revenue.csv'
 for width in [1280,390]:
  page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
  page.locator('#auto-form').scroll_into_view_if_needed();page.screenshot(path=str(ROOT/'docs'/('automatic-ui-'+str(width)+'.png')))
 assert not errors;browser.close()
out={'passed':True,'fixture_session_only':True,'interactive_oci_signin_not_automated':True,'automatic_list_after_session':True,'get_control_submits_bucket_and_key':True,'no_credential_lease_requested':True,'csrf_forwarded':True,'desktop_mobile_layout':True,'script_errors':errors}
(ROOT/'docs/automatic-ui-evidence.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
