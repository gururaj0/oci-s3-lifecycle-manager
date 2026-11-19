"""Bounded live-fixture diagnostics, never an arbitrary object/data proxy."""
import json
import time
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.errors import Problem


def record_test(engine,lease_id,detail):
    with engine.db.tx(lock_key='diagnostic_audit_lock') as connection:
        engine.db.audit(connection,lease_id,'S3_TEST',detail=detail)


def run_test(engine, owner, lease_id, body, defer_audit=None):
    started=time.monotonic()
    lease = engine.owned(lease_id, owner)
    if body.access_key_id != lease['key_id']:
        raise Problem(403, 'NOT_OWNER')
    engine.rate(owner)
    profile = engine.cfg.profiles[lease['profile']]
    grant=engine.prefix_grant(lease_id) if engine.cfg.prefix_enabled else None
    bucket = grant['bucket'] if grant else profile['bucket']
    prefix=grant['prefix'] if grant else ''
    operations=json.loads(grant['operations']) if grant else profile['operations']
    probe_key=prefix+'lcm-probe-'+lease_id+'.txt' if grant else 'diagnostics/'+lease_id+'.txt'
    read_key=('quarterly/revenue.csv' if not grant or prefix=='quarterly/' else probe_key)
    selected=getattr(body,'object_name',None)
    if selected is not None:
        if not grant or not selected.startswith(prefix) or selected==prefix:
            raise Problem(400,'OBJECT_OUTSIDE_LEASE_PREFIX')
        read_key=selected
    if grant and body.operation=='GET' and not selected and prefix!='quarterly/':
        raise Problem(400,'OBJECT_NAME_REQUIRED')
    endpoint = 'https://' + engine.cfg.namespace + '.compat.objectstorage.' + engine.cfg.region + '.oci.customer-oci.com'
    client = boto3.client('s3', endpoint_url=endpoint, region_name=engine.cfg.region,
        aws_access_key_id=body.access_key_id, aws_secret_access_key=body.secret_access_key,
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                      connect_timeout=5, read_timeout=15, retries={'max_attempts': 0}))
    operation = body.operation
    expected = lease['state'] != 'REVOKED' and operation in operations
    result = {'mode': 'oci', 'operation': operation, 'expected_access': 'ALLOW' if expected else 'DENY','bucket':bucket,'prefix':prefix}
    s3_started=time.monotonic()
    try:
        if operation == 'GET':
            result['object_name']=read_key
            response = client.get_object(Bucket=bucket, Key=read_key)
            try:
                data = response['Body'].read(4096)
                if not selected:result['fixture_verified'] = data.startswith(b'quarter,revenue\n') if read_key=='quarterly/revenue.csv' else data==b'LCM dedicated diagnostic fixture\n'
                result['bytes_checked'] = len(data)
            finally:
                response['Body'].close()
        elif operation == 'LIST':
            response = client.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=5)
            result['objects_returned'] = len(response.get('Contents', []))
            result['object_names'] = [item['Key'] for item in response.get('Contents', [])]
        elif operation == 'PUT':
            result['object_name']=probe_key
            response = client.put_object(Bucket=bucket, Key=probe_key,
                                         Body=b'LCM dedicated diagnostic fixture\n')
        elif operation == 'DELETE':
            response = client.delete_object(Bucket=bucket, Key=probe_key if grant else 'diagnostics/delete-denied.txt')
        else:  # FOREIGN is a fixed existing fixture outside both profiles.
            response = client.get_object(Bucket=engine.cfg.deployment_id + '-foreign', Key='negative-scope.txt')
            response['Body'].close()
        result['http_status'] = response['ResponseMetadata']['HTTPStatusCode']
        result['observed_access'] = 'ALLOW'
    except ClientError as error:
        status = error.response['ResponseMetadata']['HTTPStatusCode']
        result['http_status'] = status
        # A missing object is not proof of an authorization denial.
        code = error.response.get('Error', {}).get('Code', '')
        result['s3_error_code']=code
        result['observed_access'] = 'DENY' if status == 403 or (status == 404 and code in ('AccessDenied', 'NotAuthorizedOrNotFound')) else 'ERROR'
    finally:
        result['s3_request_seconds']=round(time.monotonic()-s3_started,4)
        client.close()
    result['matches_expectation'] = result['observed_access'] == result['expected_access'] and result.get('fixture_verified', True)
    result['note'] = 'This is an actual OCI S3 result. New-key propagation and delayed deletion can affect results; a lease timestamp alone does not enforce denial.'
    result['backend_test_seconds']=round(time.monotonic()-started,4)
    result['lease_age_seconds']=round(time.time()-lease['key_created_at'],3) if lease['key_created_at'] else None
    detail=json.dumps({k:result[k] for k in ('operation','http_status','expected_access','observed_access','matches_expectation','s3_request_seconds','backend_test_seconds','lease_age_seconds','s3_error_code') if k in result})
    if defer_audit:defer_audit(record_test,engine,lease_id,detail)
    else:record_test(engine,lease_id,detail)
    return result
