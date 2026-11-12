import io
import json
from shared.runtime import engine

def handler(ctx,data:io.BytesIO=None):
    body=json.loads(data.getvalue()) if data else {}
    e=engine()
    if 'eventType' in body:
        result=e.event(body)
    else:
        if set(body)!={'lease_id','generation'}:
            raise ValueError('Only lease_id and generation are accepted')
        result=e.setup(body['lease_id'],body['generation'])
    return json.dumps({'result':result})
