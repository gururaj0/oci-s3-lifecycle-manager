import io
import json
from shared.runtime import engine

def handler(ctx,data:io.BytesIO=None):
    engine().reconcile()
    return json.dumps({'status':'COMPLETE'})
