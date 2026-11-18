"""Minimal, signed, path-style S3 fake: list/get/put only. This is not OCI scope evidence."""
import hashlib
import hmac
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote, parse_qsl, urlsplit
from xml.sax.saxutils import escape
from fastapi import Response


def xml_error(code, status=403):
    return Response(f'<Error><Code>{code}</Code></Error>', status_code=status, media_type='application/xml')

async def handle_s3(request, engine, bucket, name):
    header = request.headers.get('authorization','')
    match = re.fullmatch(r'AWS4-HMAC-SHA256 Credential=([^/]+)/([^,]+), SignedHeaders=([^,]+), Signature=([a-f0-9]{64})',header)
    if not match:
        return xml_error('SignatureDoesNotMatch')
    access, scope, signed, signature = match.groups()
    parts = scope.split('/')
    if len(parts)!=4 or parts[1]!=engine.cfg.region or parts[2:]!=['s3','aws4_request']:
        return xml_error('AuthorizationHeaderMalformed')
    credential = engine.cloud.credential(access)
    if not credential:
        return xml_error('InvalidAccessKeyId')
    stamp = request.headers.get('x-amz-date','')
    try:
        date = datetime.strptime(stamp,'%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc)
        if abs(time.time()-date.timestamp())>900 or parts[0]!=stamp[:8]:
            return xml_error('RequestTimeTooSkewed')
    except ValueError:
        return xml_error('AccessDenied')
    names = signed.split(';')
    if names != sorted(set(names)) or not {'host','x-amz-date'}.issubset(names):
        return xml_error('SignatureDoesNotMatch')
    def header_value(k):
        value=request.headers.get(k,'')
        if k=='host' and engine.cfg.public_url and value!='mock-internal':
            # API Gateway rewrites Host to its backend. Use only the operator-pinned
            # public hostname, never an untrusted forwarded header, for SigV4.
            value=urlsplit(engine.cfg.public_url).netloc
        return ' '.join(value.split())
    canonical_headers = ''.join(k+':'+header_value(k)+'\n' for k in names)
    body = await request.body()
    payload_hash = request.headers.get('x-amz-content-sha256',hashlib.sha256(body).hexdigest())
    if payload_hash!='UNSIGNED-PAYLOAD' and payload_hash!=hashlib.sha256(body).hexdigest():
        return xml_error('XAmzContentSHA256Mismatch')
    query = '&'.join(quote(k,safe='-_.~')+'='+quote(v,safe='-_.~') for k,v in sorted(parse_qsl(request.url.query,keep_blank_values=True)))
    path = request.scope.get('raw_path',request.url.path.encode()).decode().split('?')[0]
    canonical = '\n'.join([request.method,path,query,canonical_headers,signed,payload_hash])
    to_sign = '\n'.join(['AWS4-HMAC-SHA256',stamp,scope,hashlib.sha256(canonical.encode()).hexdigest()])
    key = ('AWS4'+credential['secret']).encode()
    for value in parts:
        key = hmac.new(key,value.encode(),hashlib.sha256).digest()
    expected = hmac.new(key,to_sign.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected,signature):
        return xml_error('SignatureDoesNotMatch')
    with engine.db.connect() as c:
        lease = c.execute('SELECT * FROM leases WHERE key_id=?',(access,)).fetchone()
    # The fake behaves like OCI: expires_at itself does NOT deny S3. Actual deletion does.
    if not lease:
        return xml_error('AccessDenied')
    profile = engine.cfg.profiles[lease['profile']]
    operation = 'LIST' if not name and request.method=='GET' else request.method
    grant=engine.prefix_grant(lease['id']) if engine.cfg.prefix_enabled else None
    if grant:
        import json
        if engine.cloud.prefix_memberships.get(lease['user_id'])!=grant['group_id'] or operation not in json.loads(grant['operations']) or (operation!='LIST' and not name.startswith(grant['prefix'])):return xml_error('AccessDenied')
    if bucket!=profile['bucket'] or operation not in profile['operations']:
        return xml_error('AccessDenied')
    if any(k in request.query_params for k in ['uploads','uploadId']):
        return xml_error('NotImplemented',501)
    if operation=='LIST':
        with engine.db.connect() as c:
            rows=c.execute('SELECT name,length(content) size FROM objects WHERE bucket=? ORDER BY name',(bucket,)).fetchall()
        prefix=request.query_params.get('prefix','')
        contents=''.join(f'<Contents><Key>{escape(r["name"])}</Key><Size>{r["size"]}</Size></Contents>' for r in rows if r['name'].startswith(prefix))
        return Response(f'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>{escape(bucket)}</Name><IsTruncated>false</IsTruncated>{contents}</ListBucketResult>', media_type='application/xml')
    if operation=='GET':
        with engine.db.connect() as c:
            row=c.execute('SELECT content FROM objects WHERE bucket=? AND name=?',(bucket,name)).fetchone()
        return Response(row[0],media_type='application/octet-stream') if row else xml_error('NoSuchKey',404)
    if operation=='PUT':
        with engine.db.tx() as c:
            c.execute('INSERT INTO objects VALUES(?,?,?) ON CONFLICT(bucket,name) DO UPDATE SET content=excluded.content',(bucket,name,body))
        return Response(status_code=200,headers={'ETag':'"'+hashlib.md5(body).hexdigest()+'"'})
    return xml_error('AccessDenied')
