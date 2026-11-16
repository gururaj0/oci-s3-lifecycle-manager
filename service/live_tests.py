"""Bounded live-fixture diagnostics, never an arbitrary object/data proxy."""
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from shared.errors import Problem


def run_test(engine, owner, lease_id, body):
    lease = engine.owned(lease_id, owner)
    if body.access_key_id != lease['key_id']:
        raise Problem(403, 'NOT_OWNER')
    engine.rate(owner)
    profile = engine.cfg.profiles[lease['profile']]
    bucket = profile['bucket']
    endpoint = 'https://' + engine.cfg.namespace + '.compat.objectstorage.' + engine.cfg.region + '.oci.customer-oci.com'
    client = boto3.client('s3', endpoint_url=endpoint, region_name=engine.cfg.region,
        aws_access_key_id=body.access_key_id, aws_secret_access_key=body.secret_access_key,
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                      connect_timeout=5, read_timeout=15, retries={'max_attempts': 0}))
    operation = body.operation
    expected = lease['state'] != 'REVOKED' and operation in profile['operations']
    result = {'mode': 'oci', 'operation': operation, 'expected_access': 'ALLOW' if expected else 'DENY'}
    try:
        if operation == 'GET':
            response = client.get_object(Bucket=bucket, Key='quarterly/revenue.csv')
            try:
                data = response['Body'].read(4096)
                result['fixture_verified'] = data.startswith(b'quarter,revenue\n')
                result['bytes_checked'] = len(data)
            finally:
                response['Body'].close()
        elif operation == 'LIST':
            response = client.list_objects_v2(Bucket=bucket, MaxKeys=5)
            result['objects_returned'] = len(response.get('Contents', []))
        elif operation == 'PUT':
            response = client.put_object(Bucket=bucket, Key='diagnostics/' + lease_id + '.txt',
                                         Body=b'LCM dedicated diagnostic fixture\n')
        elif operation == 'DELETE':
            response = client.delete_object(Bucket=bucket, Key='diagnostics/delete-denied.txt')
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
        result['observed_access'] = 'DENY' if status == 403 or (status == 404 and code in ('AccessDenied', 'NotAuthorizedOrNotFound')) else 'ERROR'
    finally:
        client.close()
    result['matches_expectation'] = result['observed_access'] == result['expected_access'] and result.get('fixture_verified', True)
    result['note'] = 'This is an actual OCI S3 result. New-key propagation and delayed deletion can affect results; a lease timestamp alone does not enforce denial.'
    with engine.db.tx() as connection:
        engine.db.audit(connection, lease_id, 'S3_TEST', detail=json.dumps({k: result[k] for k in
            ('operation', 'http_status', 'expected_access', 'observed_access', 'matches_expectation')}))
    return result
