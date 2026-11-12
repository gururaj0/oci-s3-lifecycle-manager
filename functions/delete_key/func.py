import io
import json
from shared.runtime import engine

def handler(ctx,data:io.BytesIO=None):
    body=json.loads(data.getvalue()) if data else {}
    if set(body)!={'lease_id','generation'}:
        raise ValueError('Only lease_id and generation are accepted')
    return json.dumps({'revoked':engine().delete(body['lease_id'],body['generation'])})
