"""Server-side example. pip install requests boto3
Set LCM_CLIENT_ID and LCM_CLIENT_SECRET from your secret manager.
Set LCM_IDEMPOTENCY_KEY to a persisted UUID for this intended lease.
Requires an onboarded OAuth client AND backend profile mapping. Never log secrets.
"""
import os
import time
import uuid
import requests
import boto3
from botocore.config import Config

BASE = 'https://axcl6ctgxzrva4pjnkhxflibum.apigateway.us-ashburn-1.oci.customer-oci.com'
TOKEN_URL = 'https://idcs-1ee8a5696b814af291e90b1adc2fe166.identity.oraclecloud.com/oauth2/v1/token'
PROFILE = 'sales-read'

class Connector:
    def __init__(self):
        self.http = requests.Session()
        self.token = None
        self.refresh_at = 0

    def access_token(self):
        if time.monotonic() >= self.refresh_at:
            r = self.http.post(TOKEN_URL,
                auth=(os.environ['LCM_CLIENT_ID'], os.environ['LCM_CLIENT_SECRET']),
                data={'grant_type':'client_credentials', 'scope':BASE+'/lifecycle/'+PROFILE}, timeout=30)
            if r.status_code != 200:
                raise RuntimeError('OAuth token request failed: HTTP '+str(r.status_code))
            data = r.json()
            self.token = data['access_token']
            self.refresh_at = time.monotonic() + max(0, int(data['expires_in']) - 30)
        return self.token

    def call(self, method, path, **kwargs):
        headers = kwargs.pop('headers', {})
        for attempt in range(2):
            r = self.http.request(method, BASE+path,
                headers={**headers,'Authorization':'Bearer '+self.access_token()}, timeout=150, **kwargs)
            if r.status_code != 401 or attempt:
                return r
            self.refresh_at = 0

    def close(self):
        self.token = None
        self.http.close()


def main():
    idem = str(uuid.UUID(os.environ['LCM_IDEMPOTENCY_KEY']))
    connector = Connector()
    lease = None
    s3 = None
    try:
        response = connector.call('GET', '/v1/config')
        if response.status_code != 200:
            raise RuntimeError('Profile discovery failed: HTTP '+str(response.status_code))
        profile = response.json()['profiles'][PROFILE]
        # On transport timeout, retry this request with the SAME persisted UUID/body.
        # A new UUID would represent a different lease. See the guide's retry table.
        response = connector.call('POST', '/v1/leases', headers={'Idempotency-Key':idem},
            json={'scope_profile':PROFILE,'ttl_seconds':900,'client_request_id':'unity-example'})
        if response.status_code not in (200,201):
            raise RuntimeError('Lease request failed: HTTP '+str(response.status_code))
        lease = response.json()
        s3 = boto3.client('s3', endpoint_url=lease['endpoint'], region_name=lease['region'],
            aws_access_key_id=lease['access_key_id'], aws_secret_access_key=lease['secret_access_key'],
            config=Config(signature_version='s3v4',s3={'addressing_style':'path'}))
        # Initial S3 access can fail during propagation; production callers need bounded retries.
        s3.list_objects_v2(Bucket=profile['bucket'],MaxKeys=1)
        print('S3 request completed; no credentials displayed.')
    finally:
        try:
            if s3 is not None:
                s3.close()
            if lease is not None:
                path='/v1/leases/'+lease['lease_id']
                r=connector.call('DELETE',path)
                if r.status_code not in (202,204):
                    raise RuntimeError('Revocation request failed: HTTP '+str(r.status_code))
                for _ in range(30):
                    r=connector.call('GET',path)
                    if r.status_code==200 and r.json()['status']=='REVOKED':
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError('Cleanup confirmation timed out; inspect lease status with operator')
        finally:
            if lease is not None:
                lease.clear()
            connector.close()

if __name__=='__main__':
    main()
