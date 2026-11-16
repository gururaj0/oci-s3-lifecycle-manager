'use strict';
let csrf='',sessionExpires=0;
let identity='',response=null,setup=null,leases=[],refreshing=false,issuanceVersion=0;
const by=id=>document.getElementById(id);
const node=(tag,text)=>{const n=document.createElement(tag);n.textContent=text;return n};
async function call(path,options={}){
 const target=identity==='session'&&path.startsWith('/v1/')?'/connector'+path:path;
 const r=await fetch(target,{...options,headers:{'Content-Type':'application/json',...(identity&&identity!=='session'?{Authorization:'Bearer '+identity}:{}),...(csrf?{'X-LCM-CSRF':csrf}:{}),...options.headers}});
 const text=await r.text(),data=text?JSON.parse(text):null;
 if(!r.ok)throw new Error(data?.error?.code||'DEPENDENCY_UNAVAILABLE');return data;
}
function profile(){const p=setup?.profiles[by('live-profile').value];if(p){by('live-bucket').textContent=p.bucket;by('live-operations').textContent=p.operations.join(' · ')}}
function renderLeases(){
 by('live-rows').replaceChildren();
 leases.forEach(l=>{
  const row=node('tr',''),name=node('td',l.lease_id.slice(0,8));name.append(node('small',l.scope_profile));row.append(name);
  const user=setup?.service_users.find(u=>u.user_id===l.service_user_id);row.append(node('td',user?.name||l.service_user_id||'—'));
  const state=node('td',l.status.replaceAll('_',' '));
  if(l.status==='ACTIVE'){const seconds=Math.max(0,Math.ceil((Date.parse(l.expires_at)-Date.now())/1000));state.append(node('small',seconds?Math.floor(seconds/60)+'m '+String(seconds%60).padStart(2,'0')+'s remaining':'Cleanup due · access may persist'))}
  else if(l.deleted_at)state.append(node('small','IAM deletion '+new Date(l.deleted_at).toLocaleTimeString()));row.append(state);
  const td=node('td','');if(l.status!=='REVOKED'){const button=node('button','Revoke');button.className='subtle';button.onclick=async()=>{button.disabled=true;try{await call('/v1/leases/'+l.lease_id,{method:'DELETE'});await refresh()}catch(e){by('live-status').textContent=e.message}finally{button.disabled=false}};td.append(button)}row.append(td);by('live-rows').append(row);
 });
 if(!leases.length){const row=node('tr',''),cell=node('td','No leases yet. Create credentials to begin testing.');cell.colSpan=4;row.append(cell);by('live-rows').append(row)}
}
async function inventory(){
 if(!identity)return;setup=await call('/v1/test-setup');by('live-user-rows').replaceChildren();
 setup.service_users.forEach(u=>{const row=node('tr',''),name=node('td',u.name);name.title=u.user_id;row.append(name,node('td',u.profile),node('td',String(u.customer_secret_key_count)));by('live-user-rows').append(row)});
 by('live-fixtures').textContent='Read fixture: '+setup.read_object+' · Foreign bucket: '+setup.foreign_bucket+' / '+setup.foreign_object+' · Delete-denial fixture: '+setup.delete_test_object;
 by('live-inventory-time').textContent='OCI inventory checked '+new Date().toLocaleTimeString();profile();renderLeases();
}
async function refresh(){
 if(!identity||refreshing)return;refreshing=true;
 try{
  const [data,metrics,audit]=await Promise.all([call('/v1/leases'),call('/v1/metrics'),call('/v1/audit')]);leases=data.leases;renderLeases();
  if(response){
   const current=leases.find(l=>l.lease_id===response.lease_id);
   if(current){
    response.status=current.status;
    if(['REVOKE_PENDING','REVOKED'].includes(current.status)){
     response=null;by('live-secret').value='';by('live-access').textContent='';by('live-credential-body').hidden=true;
     by('live-issuance-progress').textContent='Lease revoked or revocation in progress. These credentials must no longer be used.';
    }else by('live-issuance-progress').textContent=current.status==='ACTIVE'?'Credentials available. Expiration cleanup and secure retry storage are ready.':'Credentials available · cleanup setup pending. Background failure will revoke this lease.';
   }
  }
  by('live-active').textContent=metrics.states.ACTIVE||0;
  by('live-pool').textContent=metrics.pools.reduce((n,p)=>n+p.allocated,0)+' / '+metrics.pools.reduce((n,p)=>n+p.capacity,0);
  by('live-overdue').textContent=metrics.overdue;by('live-heartbeat').textContent=metrics.healthy?'Healthy':'Attention';
  by('live-heartbeat-time').textContent=metrics.heartbeat_at?'Last check '+new Date(metrics.heartbeat_at).toLocaleTimeString():'No heartbeat';
  by('live-events').replaceChildren();audit.events.forEach(a=>{const row=node('div','');row.className='event';row.append(node('time',new Date(a.at*1000).toLocaleTimeString()),node('span',a.action.replaceAll('_',' ')),node('small',a.lease_id.slice(0,8)));by('live-events').append(row)});
  if(!audit.events.length)by('live-events').append(node('p','No lease activity yet.'));
 }catch(e){by('live-status').textContent=e.message}finally{refreshing=false}
}
async function connectIdentity(){try{const config=await call('/v1/config');if(config.mode!=='oci')throw new Error('LIVE_OCI_REQUIRED');by('live-profile').replaceChildren();Object.keys(config.profiles).forEach(p=>{const option=node('option',p);option.value=p;by('live-profile').append(option)});await inventory();by('live-create').disabled=false;by('live-status').textContent='Verified identity connected. '+Object.keys(config.profiles).length+' approved profiles.';await refresh();by('auto-bucket').replaceChildren();[...new Set(Object.values(config.profiles).map(p=>p.bucket))].forEach(bucket=>{const option=node('option',bucket);option.value=bucket;by('auto-bucket').append(option)});by('auto-run').disabled=false;by('auto-operation').value='LIST';await automaticAccess()}catch(e){identity='';by('live-create').disabled=true;by('live-status').textContent=e.message}}
by('connect').onsubmit=async e=>{e.preventDefault();identity=by('bearer').value;by('bearer').value='';await connectIdentity()};
by('live-issue').onsubmit=async e=>{
 e.preventDefault();by('live-clear').click();const version=++issuanceVersion;
 const requestId=crypto.randomUUID(),started=Date.now();let pending=true,polling=false;
 const phases={PREPARING:'Reserving a service user and creating credentials…',CONFIRMING_SCHEDULE:'Credentials created. Confirming expiration cleanup…',PREPARING_REPLAY:'Cleanup scheduled. Preparing secure retry storage…',ACTIVE:'Credentials ready. Receiving the response…',REVOKE_PENDING:'Issuance did not complete. Cleanup is in progress.',REVOKED:'This request was revoked. No credentials will be displayed.'};
 by('live-create').disabled=true;by('live-result-panel').hidden=false;by('live-credential-body').hidden=true;
 by('live-issuance-progress').textContent='Submitting your credential request…';by('live-issuance-time').textContent='0 seconds elapsed';
 by('live-status').textContent='Issuing credentials. Progress is shown above Your leases.';
 by('live-result-panel').scrollIntoView({block:'start'});
 const timer=setInterval(()=>{if(pending&&version===issuanceVersion)by('live-issuance-time').textContent=Math.floor((Date.now()-started)/1000)+' seconds elapsed';},1000);
 const poll=setInterval(async()=>{
  if(!pending||polling||version!==issuanceVersion)return;polling=true;
  try{const progress=await call('/v1/lease-requests/'+requestId);if(pending&&version===issuanceVersion)by('live-issuance-progress').textContent=phases[progress.phase]||'Processing credential request…';}
  catch(error){/* The request may not be reserved yet; never infer success from polling failure. */}
  finally{polling=false}
 },2000);
 try{
  const issued=await call('/v1/leases',{method:'POST',headers:{'Idempotency-Key':requestId},body:JSON.stringify({scope_profile:by('live-profile').value,ttl_seconds:Number(by('live-ttl').value),delivery_mode:'immediate'})});
  if(version!==issuanceVersion||!identity)return;
  pending=false;response=issued;
  by('live-access').textContent=response.access_key_id;by('live-secret').value=response.secret_access_key;by('live-secret').type='password';
  by('live-endpoint').textContent=response.endpoint;by('live-response').textContent=JSON.stringify({lease_id:response.lease_id,scope_profile:response.scope_profile,expires_at:response.expires_at,expiry_mode:response.expiry_mode},null,2);
  by('live-probe-result').textContent='Run before and after revocation. New keys may take several minutes to reach S3.';
  by('live-issuance-progress').textContent=response.status==='ACTIVE'?'Credentials available. Expiration cleanup is scheduled.':'Credentials available · cleanup setup pending. Background failure will revoke this lease.';
  by('live-issuance-time').textContent='Credentials returned in '+Math.floor((Date.now()-started)/1000)+' seconds';by('live-credential-body').hidden=false;
  by('live-status').textContent='Real OCI credentials are ready above Your leases.';await refresh();await inventory();
 }catch(error){
  if(version===issuanceVersion){by('live-status').textContent=error.message;by('live-issuance-progress').textContent='Request did not complete: '+error.message+'. Check Your leases before requesting another lease.';by('live-issuance-time').textContent=Math.floor((Date.now()-started)/1000)+' seconds elapsed';}
 }finally{pending=false;clearInterval(timer);clearInterval(poll);by('live-create').disabled=!identity}
};
by('live-probe').onclick=async()=>{if(!response)return;by('live-probe').disabled=true;by('live-probe-result').textContent='Running actual OCI S3 diagnostic…';try{const result=await call('/v1/leases/'+response.lease_id+'/tests',{method:'POST',body:JSON.stringify({access_key_id:response.access_key_id,secret_access_key:response.secret_access_key,operation:by('live-probe-op').value})});by('live-probe-result').textContent=JSON.stringify(result,null,2);await refresh()}catch(e){by('live-probe-result').textContent=e.message}finally{by('live-probe').disabled=false}};
by('live-show').onclick=()=>by('live-secret').type=by('live-secret').type==='password'?'text':'password';
by('live-clear').onclick=()=>{issuanceVersion++;response=null;by('live-credential-body').hidden=true;by('live-issuance-progress').textContent='';by('live-issuance-time').textContent='';by('live-secret').value='';by('live-access').textContent='';by('live-endpoint').textContent='';by('live-response').textContent='';by('live-probe-result').textContent='';by('live-result-panel').hidden=true};
by('live-profile').onchange=profile;by('live-refresh').onclick=async()=>{try{await refresh();await inventory()}catch(e){by('live-status').textContent=e.message}};by('live-setup-refresh').onclick=async()=>{try{await inventory()}catch(e){by('live-status').textContent=e.message}};
setInterval(refresh,5000);setInterval(()=>{if(identity)renderLeases()},1000);
fetch('/health/ready').then(r=>r.json()).then(data=>by('live-health').textContent=data.status+' · '+data.mode).catch(()=>by('live-health').textContent='Unavailable');

async function loadSession(){
 try {
  const r=await fetch('/auth/session');if(!r.ok)return;const session=await r.json();
  if(!session.enabled)return;
  by('domain-signin').hidden=false;by('connect').hidden=true;by('domain-signin').append(by('live-status'));
  if(session.authenticated){
   identity='session';csrf=session.csrf;sessionExpires=session.expires_at;
   by('domain-login').hidden=true;by('domain-logout').hidden=false;
   by('domain-session').textContent='Signed in as '+session.login_subject+' · Connector '+session.connector_subject+' · JWT issuer '+session.issuer+' · Session ends '+new Date(session.expires_at*1000).toLocaleTimeString();
   await connectIdentity();
  }else if(new URLSearchParams(location.search).get('sign_in')==='failed'){
   by('domain-session').textContent='Sign-in failed or this domain user is not approved. Sign in with the configured operator account.';
  }
  if(location.search)history.replaceState(null,'','/');
 }catch(e){by('live-status').textContent='Unable to check sign-in status.'}
}
by('domain-logout').onclick=async()=>{try{await call('/auth/logout',{method:'POST'});location.assign('/')}catch(e){by('domain-session').textContent=e.message}};
setInterval(()=>{if(identity==='session'&&Date.now()/1000>=sessionExpires){identity='';csrf='';response=null;by('live-clear').click();by('live-create').disabled=true;by('domain-login').hidden=false;by('domain-logout').hidden=true;by('auto-run').disabled=true;by('auto-result').textContent='Session expired. Sign in again.';by('domain-session').textContent='Session expired. Sign in again to continue.'}},1000);
loadSession();

async function automaticAccess(){
 if(!identity)return;by('auto-run').disabled=true;by('auto-result').textContent='Running authorized bucket operation…';
 try{
  const operation=by('auto-operation').value,body={bucket:by('auto-bucket').value,operation};
  if(operation!=='LIST')body.key=by('auto-key').value;
  if(operation==='PUT')body.content_base64=btoa(String.fromCharCode(...new TextEncoder().encode(by('auto-content').value)));
  const result=await call('/v1/bucket-operations',{method:'POST',body:JSON.stringify(body)});
  if(result.content_base64){result.text_preview=new TextDecoder().decode(Uint8Array.from(atob(result.content_base64),c=>c.charCodeAt(0))).slice(0,4000);delete result.content_base64}
  by('auto-result').textContent=JSON.stringify(result,null,2);
 }catch(e){by('auto-result').textContent=e.message}finally{by('auto-run').disabled=!identity}
}
by('auto-form').onsubmit=e=>{e.preventDefault();automaticAccess()};
by('auto-operation').onchange=()=>{if(by('auto-operation').value==='PUT')by('auto-key').value='automation/demo-'+crypto.randomUUID()+'.txt';else if(by('auto-operation').value==='GET')by('auto-key').value='quarterly/revenue.csv'};
