"""Single immutable Function image; deployment selects setup/delete/reconcile by role."""
import io
import json
import os
import logging
from fdk import response
from shared.runtime import engine

def handler(ctx,data:io.BytesIO=None):
    # FDK defaults can enable dependency DEBUG output, including HTTP URLs with
    # key identifiers. Keep SDK/transport logs at WARNING for every invocation.
    logging.getLogger().setLevel(logging.WARNING)
    for name in ('oci','urllib3','urllib3.connectionpool','requests','oracledb'):
        logging.getLogger(name).setLevel(logging.WARNING)
    for target in logging.getLogger().handlers:target.setLevel(logging.WARNING)
    try:
        return invoke(ctx,data)
    except Exception as exc:
        # SDK exception messages can contain payloads. Log only stable classification.
        code=getattr(exc,'code',None)
        print(json.dumps({'event':'worker_failed','class':type(exc).__name__,'code':code}),flush=True)
        return response.Response(ctx,response_data=json.dumps({'error':'WORKER_DEPENDENCY_FAILED'}),status_code=500)

def invoke(ctx,data):
    body=json.loads(data.getvalue()) if data else {}
    role=os.environ['LCM_FUNCTION_ROLE']
    if role!='reconcile' and not (role=='setup' and 'eventType' in body) and set(body)!={'lease_id','generation'}:
        return response.Response(ctx,response_data=json.dumps({'error':'INVALID_INVOCATION'}),status_code=400)
    print(json.dumps({'event':'worker_started','role':role}),flush=True)
    e=engine()
    if role=='setup' and 'eventType' in body:
        result=e.event(body)
    elif role in ('setup','delete'):
        if set(body)!={'lease_id','generation'}:raise ValueError('Invalid invocation contract')
        result=e.setup(body['lease_id'],body['generation']) if role=='setup' else e.delete(body['lease_id'],body['generation'])
    elif role=='reconcile':
        if body:raise ValueError('Reconciler accepts an empty body')
        e.reconcile();result=True
    else:raise ValueError('Invalid role')
    return response.Response(ctx,response_data=json.dumps({'result':result}),headers={'Content-Type':'application/json'})
