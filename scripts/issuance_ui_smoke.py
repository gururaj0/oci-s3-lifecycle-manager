"""Exercise pending/ready/dismissed issuance using local assets and synthetic responses."""
import asyncio,json,mimetypes,time
from pathlib import Path
from playwright.async_api import async_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
BASE='https://issuance.test'
async def main():
 finish=asyncio.Event();requested=asyncio.Event();phase='CONFIRMING_SCHEDULE';idem=None;errors=[];lease_state=None
 profile={'sales-read':{'bucket':'sales-demo','operations':['LIST','GET']}}
 async with async_playwright() as p:
  browser=await p.chromium.launch(channel='chrome');page=await browser.new_page(viewport={'width':1280,'height':1000})
  page.on('pageerror',lambda e:errors.append(type(e).__name__))
  async def route(r):
   nonlocal idem
   path=r.request.url.split(BASE)[1]
   if path=='/' or path.startswith('/static/'):
    target=ROOT/'service/static'/('live.html' if path=='/' else path.removeprefix('/static/'))
    await r.fulfill(body=target.read_bytes(),content_type=mimetypes.guess_type(target)[0] or 'text/plain');return
   if path=='/auth/session':data={'enabled':True,'authenticated':True,'csrf':'fixture','expires_at':time.time()+600,'login_subject':'fixture','connector_subject':'fixture','issuer':'fixture'}
   elif path=='/health/ready':data={'status':'READY','mode':'oci'}
   elif path.endswith('/config'):data={'mode':'oci','profiles':profile,'prefix_enabled':True}
   elif path.endswith('/test-setup'):data={'profiles':profile,'service_users':[],'read_object':'fixture','foreign_bucket':'foreign','foreign_object':'fixture','delete_test_object':'fixture'}
   elif path.endswith('/metrics'):data={'states':{},'pools':[],'overdue':0,'healthy':True,'heartbeat_at':None}
   elif path.endswith('/audit'):data={'events':[]}
   elif path.endswith('/bucket-operations'):data={'operation':'LIST','objects':[]}
   elif '/lease-requests/' in path:
    assert path.endswith(idem);data={'request_id':idem,'phase':phase,'lease':{'lease_id':'fixture','status':'SCHEDULE_PENDING'}}
   elif path.endswith('/leases') and r.request.method=='POST':
    idem=r.request.headers['idempotency-key'];requested.set();await finish.wait()
    assert r.request.post_data_json['delivery_mode']=='immediate'
    assert r.request.post_data_json['prefix']=='quarterly/' and r.request.post_data_json['bucket']=='sales-demo'
    assert 'scope_profile' not in r.request.post_data_json
    data={'status':'SCHEDULE_PENDING','lease_id':'fixture-lease','access_key_id':'fixture-access','secret_access_key':'fixture-secret','endpoint':'https://s3.test','scope_profile':'sales-read','expires_at':'2030-01-01T00:00:00Z','expiry_mode':'managed_key_deletion'}
   elif path.endswith('/leases'):data={'leases':([{'lease_id':'fixture-lease','scope_profile':'sales-read','status':lease_state,'phase':phase,'expires_at':'2030-01-01T00:00:00Z'}] if lease_state else [])}
   else:raise AssertionError(path)
   await r.fulfill(content_type='application/json',body=json.dumps(data))
  await page.route(BASE+'/**',route)
  await page.goto(BASE,wait_until='networkidle');await expect(page.locator('#live-create')).to_be_enabled()
  await page.locator('#live-create').click();await requested.wait()
  await expect(page.locator('#live-result-panel')).to_be_visible()
  await expect(page.locator('#live-credential-body')).to_be_hidden()
  await expect(page.locator('#live-secret')).to_have_value('')
  assert await page.locator('#live-result-panel').evaluate("e => Boolean(e.compareDocumentPosition(document.querySelector('.content-grid')) & Node.DOCUMENT_POSITION_FOLLOWING)")
  await expect(page.locator('#live-issuance-progress')).to_contain_text('Confirming expiration',timeout=10000)
  phase='PREPARING_REPLAY'
  await expect(page.locator('#live-issuance-progress')).to_contain_text('secure retry storage',timeout=10000)
  assert 'seconds elapsed' in await page.locator('#live-issuance-time').inner_text()
  for width in [1280,390]:
   await page.set_viewport_size({'width':width,'height':1000});assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   await page.locator('#live-result-panel').scroll_into_view_if_needed();await page.screenshot(path=str(ROOT/'docs'/('issuance-progress-'+str(width)+'.png')))
  finish.set();await expect(page.locator('#live-credential-body')).to_be_visible()
  await expect(page.locator('#live-secret')).to_have_value('fixture-secret')
  await expect(page.locator('#live-secret')).to_have_attribute('type','password')
  await expect(page.locator('#live-issuance-progress')).to_contain_text('cleanup setup pending')
  lease_state='SCHEDULE_PENDING';phase='CONFIRMING_SCHEDULE'
  await page.locator('#live-refresh').click()
  await expect(page.locator('#live-rows')).to_contain_text('Key recorded · Confirming expiration cleanup')
  await expect(page.locator('#live-issuance-progress')).to_contain_text('Confirming expiration cleanup')
  await expect(page.locator('#live-secret')).to_have_value('fixture-secret')
  phase='PREPARING_REPLAY'
  await page.locator('#live-refresh').click()
  await expect(page.locator('#live-rows')).to_contain_text('Expiration scheduled · Preparing secure retry storage in Vault')
  await expect(page.locator('#live-issuance-progress')).to_contain_text('Preparing secure retry storage in Vault')
  await expect(page.locator('#live-secret')).to_have_value('fixture-secret')
  lease_state='ACTIVE';phase='ACTIVE'
  await page.locator('#live-refresh').click()
  await expect(page.locator('#live-issuance-progress')).to_contain_text('secure retry storage are ready')
  lease_state='REVOKE_PENDING'
  await page.locator('#live-refresh').click()
  await expect(page.locator('#live-credential-body')).to_be_hidden()
  await expect(page.locator('#live-secret')).to_have_value('')
  lease_state=None
  # A dismissed pending request must not redisplay credentials when its response arrives.
  finish.clear();requested.clear();await expect(page.locator('#live-create')).to_be_enabled()
  await page.locator('#live-create').click();await requested.wait();await page.locator('#live-clear').click()
  finish.set();await expect(page.locator('#live-create')).to_be_enabled()
  await expect(page.locator('#live-result-panel')).to_be_hidden();await expect(page.locator('#live-secret')).to_have_value('')
  assert not errors;await browser.close()
 result={'passed':True,'synthetic_responses':True,'panel_visible_before_create_response':True,'panel_above_lease_table':True,'committed_progress_stages_render':True,'elapsed_time_shown':True,'secret_shown_while_setup_pending':True,'background_completion_shown':True,'pending_stages_in_lease_status_and_credentials_panel':True,'revocation_clears_secret':True,'secret_masked_on_success':True,'dismissed_request_cannot_redisplay_secret':True,'desktop_mobile_layout':True,'prefix_request_controls':True,'script_errors':errors}
 (ROOT/'docs/issuance-ui-evidence.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
asyncio.run(main())
