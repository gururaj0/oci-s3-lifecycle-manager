"""Bounded authenticated bucket operations using the VM workload identity.
Profile authorization precedes any OCI call. No lease or S3 key is minted.
"""
import base64
import binascii
import hashlib
import json
from typing import Literal
import oci
from pydantic import BaseModel, ConfigDict, Field, model_validator
from shared.errors import Problem

MAX_BYTES = 512 * 1024

class BucketOperation(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    bucket: str = Field(min_length=1,max_length=256)
    operation: Literal['LIST','GET','PUT']
    key: str | None = Field(default=None,min_length=1,max_length=1024)
    prefix: str = Field(default='',max_length=1024)
    start: str | None = Field(default=None,max_length=1024)
    limit: int = Field(default=100,ge=1,le=100)
    content_base64: str | None = Field(default=None,max_length=4*((MAX_BYTES+2)//3))
    if_match: str | None = Field(default=None,min_length=1,max_length=256)

    @model_validator(mode='after')
    def valid_operation(self):
        if self.operation=='LIST':
            if self.key is not None or self.content_base64 is not None or self.if_match is not None:
                raise ValueError('LIST accepts only bucket, prefix, start and limit')
        else:
            if self.key is None or self.prefix or self.start is not None or self.limit!=100:
                raise ValueError('Object operations require key; listing parameters are not supported')
            if self.operation=='GET' and (self.content_base64 is not None or self.if_match is not None):
                raise ValueError('GET does not accept upload parameters')
            if self.operation=='PUT':
                if self.content_base64 is None:raise ValueError('PUT requires content_base64')
                try:data=base64.b64decode(self.content_base64,validate=True)
                except (binascii.Error,ValueError):raise ValueError('Invalid base64') from None
                if len(data)>MAX_BYTES:raise ValueError('Object exceeds bounded request limit')
        if self.if_match and ('\r' in self.if_match or '\n' in self.if_match):
            raise ValueError('Invalid conditional header')
        return self


def object_client(engine):
    return oci.object_storage.ObjectStorageClient(
        {'region':engine.cfg.region},signer=engine.cloud.identity.base_client.signer,
        timeout=(5,20),retry_strategy=oci.retry.NoneRetryStrategy())


def execute(engine, owner, body, allowed, correlation):
    candidates=[(name,p) for name,p in engine.cfg.profiles.items()
        if name in allowed and p['bucket']==body.bucket and body.operation in p['operations']]
    if not candidates:raise Problem(403,'BUCKET_OPERATION_FORBIDDEN')
    # Deterministic least-privilege match among profiles already authorized for this identity.
    name,profile=min(candidates,key=lambda item:(len(set(item[1]['operations'])),item[0]))
    engine.rate(owner)
    detail={'issuer':owner['issuer'],'subject':owner['subject'],'profile':name,
            'operation':body.operation,'bucket':body.bucket,
            'key_hash':hashlib.sha256(body.key.encode()).hexdigest() if body.key else None}
    # Fail closed if the intent cannot be recorded before a data operation.
    with engine.db.tx() as c:engine.db.audit(c,None,'BUCKET_OPERATION_REQUESTED',correlation,json.dumps(detail))
    client=None;outcome='FAILED'
    result={'bucket':body.bucket,'operation':body.operation,'selected_profile':name,
            'credential_mode':'oci_workload_identity','lease_created':False}
    try:
        client=object_client(engine)
        common={'namespace_name':engine.cfg.namespace,'bucket_name':body.bucket}
        if body.operation=='LIST':
            args={'prefix':body.prefix,'limit':body.limit,'fields':'name,size,etag'}
            if body.start is not None:args['start']=body.start
            response=client.list_objects(**common,**args)
            result.update(objects=[{'key':o.name,'size':o.size,'etag':o.etag} for o in response.data.objects],next_start=response.data.next_start_with)
        elif body.operation=='GET':
            response=client.get_object(**common,object_name=body.key)
            try:
                if int(response.headers.get('content-length','0'))>MAX_BYTES:raise Problem(413,'OBJECT_TOO_LARGE')
                data=response.data.raw.read(MAX_BYTES+1)
                if len(data)>MAX_BYTES:raise Problem(413,'OBJECT_TOO_LARGE')
                result.update(key=body.key,content_base64=base64.b64encode(data).decode(),size=len(data),etag=response.headers.get('etag'))
            finally:response.data.close()
        else:
            data=base64.b64decode(body.content_base64,validate=True)
            condition={'if_match':body.if_match} if body.if_match else {'if_none_match':'*'}
            response=client.put_object(**common,object_name=body.key,put_object_body=data,
                content_length=len(data),content_type='application/octet-stream',**condition)
            result.update(key=body.key,size=len(data),etag=response.headers.get('etag'))
        outcome='SUCCEEDED'
        return result
    except oci.exceptions.ServiceError as exc:
        if exc.status in (401,403):raise Problem(403,'OCI_ACCESS_DENIED') from None
        if exc.status==404:raise Problem(404,'OBJECT_OR_BUCKET_NOT_FOUND') from None
        if exc.status==412:raise Problem(412,'OBJECT_PRECONDITION_FAILED') from None
        raise Problem(503,'OBJECT_STORAGE_UNAVAILABLE') from None
    except Problem:raise
    except Exception:raise Problem(503,'OBJECT_STORAGE_UNAVAILABLE') from None
    finally:
        if client is not None:
            try:client.base_client.session.close()
            except Exception:pass
        # An audit outage after a successful PUT must not turn success into an ambiguous retry.
        try:
            with engine.db.tx() as c:engine.db.audit(c,None,'BUCKET_OPERATION_'+outcome,correlation,json.dumps(detail))
        except Exception:
            import logging
            logging.getLogger('lcm').error('BUCKET_OPERATION_AUDIT_FAILED correlation=%s',correlation)
