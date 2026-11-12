'use strict';
let token='', credentials=null, leases=[], busy=false;
const $=id=>document.getElementById(id);
const text=(id,value)=>$(id).textContent=value;
function toast(msg){text('toast',msg);$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,5000)}
async function api(path,options={}){
 const r=await fetch(path,{...options,headers:{'Content-Type':'application/json',...(token?{'Authorization':'Bearer '+token}:{}),...(options.headers||{})}});
 if(r.status===204)return null;
 const raw=await r.text();const data=raw?JSON.parse(raw):null;if(!r.ok){const err=new Error(data?.error?.code||'SERVICE_UNAVAILABLE');err.code=data?.error?.code;throw err}return data;
}
function el(tag,value,cls){const node=document.createElement(tag);if(value!==undefined)node.textContent=value;if(cls)node.className=cls;return node}
function stateClass(state){return state==='ACTIVE'?'active':state==='REVOKED'?'revoked':'pending'}
function renderLeases(){
 const tbody=$('lease-rows');tbody.replaceChildren();
 if(!leases.length){const tr=el('tr'),td=el('td','No leases yet. Request your first credential lease.','empty');td.colSpan=4;tr.append(td);tbody.append(tr);return}
 leases.forEach(l=>{
 const tr=el('tr'),name=el('td');name.append(el('div',l.scope_profile),el('small',l.lease_id.slice(0,8)+'…','mono'));tr.append(name);
 const state=el('td');state.append(el('span',l.status.replaceAll('_',' '),'badge '+stateClass(l.status)));tr.append(state);
 const remain=el('td');if(l.status==='ACTIVE'){
 const seconds=Math.max(0,Math.ceil((Date.parse(l.expires_at)-Date.now())/1000));remain.append(el('div',seconds?`${Math.floor(seconds/60)}m ${String(seconds%60).padStart(2,'0')}s`:'Cleanup due','countdown'));
 const bar=el('div',undefined,'progress'),fill=el('span');fill.style.width=Math.min(100,seconds/l.requested_ttl*100)+'%';bar.append(fill);remain.append(bar);
 }else remain.append(el('small',l.deleted_at?'Revoked '+new Date(l.deleted_at).toLocaleTimeString():'Awaiting cleanup'));tr.append(remain);
 const action=el('td');if(l.status!=='REVOKED'){const button=el('button','Revoke','subtle revoke');button.onclick=async()=>{button.disabled=true;try{await api('/v1/leases/'+l.lease_id,{method:'DELETE'});toast('Revocation requested. The key is removed after confirmed deletion.');await refresh()}catch(err){toast(err.message)}finally{button.disabled=false}};action.append(button)}tr.append(action);tbody.append(tr);
 });
}
async function refresh(){
 try{
 const [list,metrics,audit,ready]=await Promise.all([api('/v1/leases'),api('/demo/metrics'),api('/demo/audit'),fetch('/health/ready').then(r=>r.json())]);leases=list.leases;renderLeases();
 text('active',metrics.states.ACTIVE||0);text('pool',metrics.pools.reduce((sum,p)=>sum+p.allocated,0));text('overdue',metrics.overdue);text('heartbeat',metrics.healthy?'Healthy':'Attention');text('heartbeat-time',metrics.heartbeat_at?'Last check '+new Date(metrics.heartbeat_at).toLocaleTimeString():'No heartbeat');
 text('health',ready.status==='READY'?'● Demo ready':'● Cleanup pending');$('health').className='badge '+(ready.status==='READY'?'active':'pending');
 const events=$('events');events.replaceChildren();if(!audit.events.length)events.append(el('div','Lease activity will appear here.','empty'));
 audit.events.forEach(a=>{const row=el('div',undefined,'event');row.append(el('time',new Date(a.at*1000).toLocaleTimeString()),el('span',a.action.toLowerCase().replaceAll('_',' '),'event-text'),el('small',a.lease_id.slice(0,8)));events.append(row)});
 }catch(err){text('health','Connection unavailable');$('health').className='badge pending'}
}
$('profile').onchange=()=>{const div=$('scope-ops');div.replaceChildren(el('span','List objects'),el('span','Read objects'));if($('profile').value==='sales-write')div.append(el('span','Write objects'))};
$('lease-form').onsubmit=async event=>{
 event.preventDefault();if(busy)return;busy=true;$('issue').disabled=true;text('form-error','');
 try{
 const result=await api('/v1/leases',{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({scope_profile:$('profile').value,ttl_seconds:Number($('ttl').value),client_request_id:$('request-id').value||null})});
 credentials=result;text('access',result.access_key_id);$('secret').value=result.secret_access_key;text('endpoint',result.endpoint);$('credential-panel').hidden=false;$('secret').type='password';text('show-secret','Show');text('probe-result','Run a request before and after revocation.');toast('Synthetic lease active. Deletion schedule confirmed.');await refresh();$('credential-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
 }catch(err){text('form-error',({POOL_EXHAUSTED:'Both users in this profile are leased. Revoke a lease or wait for expiry.',CLEANUP_UNHEALTHY:'Cleanup is unhealthy. New issuance is paused.',RATE_LIMITED:'Request rate exceeded. Try again in 30 seconds.'})[err.code]||err.message)}finally{busy=false;$('issue').disabled=false}
};
$('refresh').onclick=refresh;
$('show-secret').onclick=()=>{const show=$('secret').type==='password';$('secret').type=show?'text':'password';text('show-secret',show?'Hide':'Show')};
$('hide-credentials').onclick=()=>{credentials=null;$('secret').value='';text('access','');text('endpoint','');$('credential-panel').hidden=true};
$('probe').onclick=async()=>{
 if(!credentials)return;const button=$('probe');button.disabled=true;
 try{const selected=$('probe-op').value;const response=await api('/demo/probe',{method:'POST',body:JSON.stringify({lease_id:credentials.lease_id,access_key_id:credentials.access_key_id,secret_access_key:credentials.secret_access_key,operation:selected==='FOREIGN'?'GET':selected,bucket:selected==='FOREIGN'?'private-finance':'sales-demo',object_name:selected==='PUT'?'demo-write.txt':'quarterly/revenue.csv'})});text('probe-result',`HTTP ${response.status_code} · ${response.allowed?'Access allowed':'Access denied'}\n\n${response.body}`)}catch(err){text('probe-result',err.message)}finally{button.disabled=false}
};
(async()=>{try{const session=await api('/demo/session',{method:'POST'});token=session.token;await refresh();setInterval(refresh,5000);setInterval(renderLeases,1000)}catch(err){text('health','Session unavailable');toast(err.message)}})();
