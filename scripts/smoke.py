"""Opt-in HTTP integration evidence. All credentials stay in memory; output is redacted.
The --expiry test waits the PRD minimum 300 seconds and observes actual signed-S3 denial.
"""
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import statistics
import time
import uuid
import httpx
from botocore.auth import S3SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials


def now():return datetime.now(timezone.utc).isoformat()

def run(base,expiry=False,performance=0):
    client=httpx.Client(base_url=base.rstrip('/'),timeout=30,event_hooks={'request':[lambda request:time.sleep(0.22)]})
    info=client.get('/demo/info');info.raise_for_status()
    assert info.json()['mode']=='mock' and info.json()['production_ready'] is False
    token=client.post('/demo/session').json()['token']
    headers={'Authorization':'Bearer '+token}
    evidence={'mode':'mock','production_ready':False,'endpoint':base,'started_at':now(),'checks':{}}
    def create(profile='sales-read',ttl=300,idem=None):
        return client.post('/v1/leases',headers=dict(headers,**{'Idempotency-Key':idem or str(uuid.uuid4())}),
                           json={'scope_profile':profile,'ttl_seconds':ttl})
    def signed(b,method='GET',bucket='sales-demo',name='quarterly/revenue.csv',secret=None,data=b''):
        url=base.rstrip('/')+'/mock-s3/'+bucket+('/'+name if name else '?list-type=2')
        r=AWSRequest(method=method,url=url,data=data)
        S3SigV4Auth(Credentials(b['access_key_id'],secret or b['secret_access_key']),'s3',b['region']).add_auth(r)
        return client.request(method,url,headers=dict(r.headers),content=data)
    leases=[]
    try:
        assert client.get('/health/ready').json()['status']=='READY'
        evidence['checks']['readiness']='PASS'
        assert client.post('/v1/leases',json={'scope_profile':'sales-read'}).status_code==401
        idem=str(uuid.uuid4());r=create(idem=idem);assert r.status_code==201,r.text
        b=r.json();leases.append(b['lease_id'])
        assert b['endpoint']==base.rstrip('/')+'/mock-s3','Returned endpoint must use pinned public gateway URL'
        assert create(idem=idem).json()==b
        assert create(ttl=301,idem=idem).status_code==409
        assert r.headers['cache-control']=='no-store'
        evidence['checks']['idempotency']='PASS'
        assert 'secret_access_key' not in client.get('/v1/leases/'+b['lease_id'],headers=headers).text
        assert 'session_token' not in b
        other_token=client.post('/demo/session').json()['token']
        assert client.get('/v1/leases/'+b['lease_id'],headers={'Authorization':'Bearer '+other_token}).status_code==403
        evidence['checks']['auth_and_owner']='PASS'
        for method,bucket,name in [('GET','sales-demo','quarterly/revenue.csv'),('GET','sales-demo','')]:
            response=signed(b,method,bucket,name)
            assert response.status_code==200,(response.status_code,response.text)
        assert signed(b,secret='wrong').status_code==403
        assert signed(b,bucket='foreign-bucket').status_code==403
        assert signed(b,'PUT',data=b'test').status_code==403
        assert signed(b,'DELETE').status_code==403
        evidence['checks']['raw_sigv4_read_scope']='PASS_MOCK'
        second=create();assert second.status_code==201;leases.append(second.json()['lease_id'])
        assert create().status_code==429
        evidence['checks']['pool_capacity']='PASS'
        for lid in list(leases):
            assert client.delete('/v1/leases/'+lid,headers=headers).status_code in (202,204)
            assert client.delete('/v1/leases/'+lid,headers=headers).status_code==204
            leases.remove(lid)
        assert signed(b).status_code==403
        evidence['checks']['early_revoke_s3_denial']='PASS_MOCK'
        write=create('sales-write');assert write.status_code==201
        w=write.json();leases.append(w['lease_id'])
        assert signed(w,'PUT',name='integration-write.txt',data=b'integration demo\n').status_code==200
        assert signed(w,'GET',name='integration-write.txt').status_code==200
        assert signed(w,'DELETE',name='integration-write.txt').status_code==403
        assert client.delete('/v1/leases/'+w['lease_id'],headers=headers).status_code in (202,204)
        leases.remove(w['lease_id'])
        evidence['checks']['raw_sigv4_write_scope']='PASS_MOCK'
        if performance:
            # Separate actors avoid the configured per-subject rate limit. Tests are sequential.
            samples=[]
            for i in range(performance):
                if i%30==0:
                    token=client.post('/demo/session').json()['token'];headers={'Authorization':'Bearer '+token}
                start=time.monotonic();r=create();latency=time.monotonic()-start
                assert r.status_code==201,r.text
                lid=r.json()['lease_id'];leases.append(lid);samples.append(latency)
                assert client.delete('/v1/leases/'+lid,headers=headers).status_code in (202,204)
                leases.remove(lid)
            ordered=sorted(samples)
            evidence['issuance_latency']={'requests':performance,'p50_seconds':statistics.median(samples),'p95_seconds':ordered[max(0,int(len(ordered)*.95)-1)],'max_seconds':max(samples),'mode':'mock'}
        if expiry:
            token=client.post('/demo/session').json()['token'];headers={'Authorization':'Bearer '+token}
            r=create(ttl=300);assert r.status_code==201,r.text
            b=r.json();leases.append(b['lease_id'])
            assert b['endpoint']==base.rstrip('/')+'/mock-s3','Returned endpoint must use pinned public gateway URL'
            deadline=datetime.fromisoformat(b['expires_at'].replace('Z','+00:00')).timestamp()
            evidence['expiry']={'lease_id':b['lease_id'],'expires_at':b['expires_at'],'ttl_seconds':300,'mode':'mock'}
            assert signed(b).status_code==200
            print(json.dumps({'event':'expiry_test_started','lease_id':b['lease_id'],'expires_at':b['expires_at']}),flush=True)
            next_notice=time.time()+45
            last_allowed=None;denied=None
            while time.time()<deadline+120:
                response=signed(b);observed=time.time()
                if response.status_code==403:
                    assert observed>=deadline,'EARLY_REVOCATION'
                    denied=observed;break
                assert response.status_code==200,(response.status_code,response.text)
                last_allowed=observed
                if time.time()>next_notice:
                    print(json.dumps({'event':'expiry_wait','remaining_seconds':round(deadline-time.time())}),flush=True);next_notice=time.time()+45
                time.sleep(min(2,max(.1,deadline-time.time())))
            assert denied is not None,'No observed denial within 120 seconds of deadline'
            evidence['expiry'].update({'last_allowed_at':datetime.fromtimestamp(last_allowed,timezone.utc).isoformat(),
                'observed_denial_at':datetime.fromtimestamp(denied,timezone.utc).isoformat(),'observed_delay_seconds':denied-deadline,'no_early_revocation':True})
            evidence['checks']['five_minute_expiry']='PASS_MOCK'
            status=client.get('/v1/leases/'+b['lease_id'],headers=headers).json()
            assert status['status']=='REVOKED'
            # The replay secret is unavailable after the 60-second window.
            evidence['checks']['status_after_expiry']='REVOKED'
            leases.remove(b['lease_id'])
        evidence['finished_at']=now()
        return evidence
    finally:
        for lid in leases:
            client.delete('/v1/leases/'+lid,headers=headers)
        client.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('base_url');p.add_argument('--expiry',action='store_true');p.add_argument('--performance',type=int,default=0);p.add_argument('--output',default='.local/smoke-evidence.json');args=p.parse_args()
    result=run(args.base_url,args.expiry,args.performance)
    Path(args.output).write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
