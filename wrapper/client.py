"""Reference wrapper adapter. No AWS session token; renewal creates a distinct lease.
This does not prove compatibility with the actual Unity wrapper/query engine.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
import uuid
import httpx

@dataclass(repr=False)
class LeasedCredentials:
    lease_id: str
    access_key_id: str
    secret_access_key: str
    endpoint: str
    region: str
    expires_at: str

    def boto3_arguments(self):
        return {'aws_access_key_id':self.access_key_id,'aws_secret_access_key':self.secret_access_key,
                'endpoint_url':self.endpoint,'region_name':self.region}

class LeaseClient:
    def __init__(self,base_url,token_provider):
        if not base_url.startswith('https://') and not base_url.startswith('http://127.0.0.1:'):
            raise ValueError('HTTPS required outside loopback')
        self.http=httpx.Client(base_url=base_url.rstrip('/'),timeout=20)
        self.token_provider=token_provider

    def create(self,profile,ttl_seconds=900,idempotency_key=None,client_request_id=None):
        idem=idempotency_key or str(uuid.uuid4())
        r=self.http.post('/v1/leases',headers={'Authorization':'Bearer '+self.token_provider(),
            'Idempotency-Key':idem},json={'scope_profile':profile,'ttl_seconds':ttl_seconds,
                                         'client_request_id':client_request_id})
        if r.status_code not in (200,201):
            # Do not attach raw responses that might later contain credential payloads.
            raise RuntimeError('Lease request failed: '+str(r.status_code)+' '+r.json().get('error',{}).get('code','UNKNOWN'))
        b=r.json()
        return LeasedCredentials(**{k:b[k] for k in LeasedCredentials.__dataclass_fields__})

    def revoke(self,lease):
        r=self.http.delete('/v1/leases/'+lease.lease_id,headers={'Authorization':'Bearer '+self.token_provider()})
        if r.status_code not in (202,204):
            raise RuntimeError('Revocation failed: '+str(r.status_code))

    def close(self):
        self.http.close()
