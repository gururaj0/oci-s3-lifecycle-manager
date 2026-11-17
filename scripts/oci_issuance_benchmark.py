"""Measure OCI-client issuance; SSH, OAuth, finalization and cleanup are outside timing.

Run locally with --samples 5. Remote credentials remain in memory and only redacted
measurement events/evidence return over the existing DemoBastion SSH route.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone


def remote(samples):
    import base64
    import oci
    import requests
    cfg=json.loads(Path('/etc/lcm-live/config.json').read_text())
    assert cfg['mode']=='oci' and cfg['region']=='us-ashburn-1'
    assert cfg['compartment_id']=='ocid1.compartment.oc1..aaaaaaaav6gvasmb4eyn6fnuuaoabal6mdfzuu4qxf3ll5kidwj24jxcn3dq'
    oauth=cfg['oauth'];base=oauth['public_url'].rstrip('/')
    signer=oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
    vault=oci.secrets.SecretsClient({},signer=signer)
    bundle=vault.get_secret_bundle(oauth['secret_id']).data
    client_secret=json.loads(base64.b64decode(bundle.secret_bundle_content.content))['connector_client_secret']
    session=requests.Session();results=[];failures=[]
    def emit(value):print(json.dumps(value),flush=True)
    def expect(r,statuses):
        if r.status_code not in statuses:raise RuntimeError('HTTP_'+str(r.status_code))
        return r
    started_at=datetime.now(timezone.utc).isoformat()
    emit({'event':'started','samples':samples,'source':'existing private OCI demo VM','region':cfg['region']})
    for number in range(1,samples+1):
        lease_id=None;request_id=str(uuid.uuid4());row={'sample':number};issued=False;headers=None
        try:
            # Token acquisition is deliberately before the monotonic clock starts.
            token_response=requests.post(oauth['token_endpoint'],auth=(oauth['connector_client_id'],client_secret),data={'grant_type':'client_credentials','scope':oauth['scope']},timeout=30)
            expect(token_response,(200,));token=token_response.json()['access_token'];del token_response
            headers={'Authorization':'Bearer '+token,'Idempotency-Key':request_id}
            body={'scope_profile':'sales-read','ttl_seconds':300,'delivery_mode':'immediate','client_request_id':'oci-timing-'+request_id}
            start=time.perf_counter()
            response=session.post(base+'/v1/leases',headers=headers,json=body,timeout=150)
            elapsed=time.perf_counter()-start
            expect(response,(201,))
            credential=response.json();lease_id=credential['lease_id']
            assert credential.get('access_key_id') and credential.get('secret_access_key')
            row.update(credential_response_seconds=round(elapsed,4),http_status=response.status_code,delivery_status=credential['status'])
            credential.clear();response.close();del response,credential
            issued=True;emit({'event':'credentials_received',**row})
            # Allow normal finalization, rather than revoking while it is still running.
            for _ in range(70):
                r=expect(session.get(base+'/v1/leases/'+lease_id,headers=headers,timeout=30),(200,))
                status=r.json()['status'];r.close()
                if status=='ACTIVE':break
                if status in ('REVOKED','REVOKE_PENDING'):raise RuntimeError('FINALIZATION_REVOKED')
                time.sleep(2)
            else:raise RuntimeError('FINALIZATION_TIMEOUT')
            row['finalization_observed_seconds']=round(time.perf_counter()-start,4)
        except Exception as exc:
            # Never print SDK exception messages, tokens or response bodies.
            failures.append({'sample':number,'error':str(exc) if isinstance(exc,RuntimeError) and str(exc).startswith(('HTTP_','FINALIZATION_')) else type(exc).__name__})
        finally:
            # An uncertain response may still have created a lease. Recover only
            # this run's owner-bound request before attempting cleanup.
            if not lease_id and headers:
                try:
                    for _ in range(15):
                        r=session.get(base+'/v1/lease-requests/'+request_id,headers=headers,timeout=30)
                        if r.status_code==200:lease_id=r.json()['lease']['lease_id'];break
                        if r.status_code!=404:break
                        time.sleep(2)
                except Exception:pass
            if lease_id:
                try:
                    expect(session.delete(base+'/v1/leases/'+lease_id,headers=headers,timeout=120),(202,204))
                    for _ in range(40):
                        r=expect(session.get(base+'/v1/leases/'+lease_id,headers=headers,timeout=30),(200,))
                        if r.json()['status']=='REVOKED':break
                        time.sleep(2)
                    else:raise RuntimeError('CLEANUP_INCOMPLETE')
                    row['test_lease_revoked']=True
                except Exception as exc:
                    row['test_lease_revoked']=False
                    failures.append({'sample':number,'error':'CLEANUP_'+type(exc).__name__,'lease_id':lease_id})
            if issued:results.append(row)
            emit({'event':'sample_complete',**row})
        if failures:break  # Do not consume more pool slots on an unresolved failure.
    session.close()
    timings=[r['credential_response_seconds'] for r in results]
    summary={'event':'result','started_at_utc':started_at,'source':'existing private OCI demo VM (also hosts demo backend)','region':cfg['region'],'route':'public OCI API Gateway HTTPS endpoint','http_client':'Python requests HTTP/1.1 Session; reused connections where available','requested_samples':samples,'samples':results,'failures':failures,'excluded':['SSH setup','OAuth token acquisition','background finalization','revocation'],'includes':['client-to-gateway network','gateway authentication','backend authorization and reservation','IAM key creation','ADB recording','credential response transfer'],'isolated_backend_or_iam_timings_measured':False,'p95_method':'nearest rank; small sample preliminary only','passed':len(results)==samples and not failures and all(r.get('test_lease_revoked') for r in results)}
    if timings:summary['credential_response_seconds']={'median':round(statistics.median(timings),4),'p95':sorted(timings)[math.ceil(.95*len(timings))-1],'min':min(timings),'max':max(timings)}
    emit(summary)
    return 0 if summary['passed'] else 1


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--remote',action='store_true');parser.add_argument('--samples',type=int,default=5);args=parser.parse_args()
    if not 1<=args.samples<=20:parser.error('samples must be 1..20')
    if args.remote:return remote(args.samples)
    root=Path(__file__).resolve().parents[1]
    command=['ssh','-F','.local/ssh-config','lcm-live','sudo /opt/lcm-live/venv/bin/python - --remote --samples '+str(args.samples)]
    process=subprocess.Popen(command,cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    process.stdin.write(Path(__file__).read_text());process.stdin.close()
    found=False
    for line in process.stdout:
        print(line,end='',flush=True)
        value=json.loads(line)
        if value.get('event')=='result':
            (root/'docs/oci-issuance-benchmark.json').write_text(json.dumps(value,indent=2)+'\n');found=True
    code=process.wait()
    if code or not found:raise SystemExit('OCI benchmark incomplete; inspect redacted results and reconcile test leases before retrying')
    return 0

if __name__=='__main__':raise SystemExit(main())
