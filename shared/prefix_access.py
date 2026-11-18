"""Validated request-to-prefix authorization and deterministic IAM scope names."""
import hashlib,json,re
from shared.errors import Problem


def resolve(cfg,body,allowed):
    bucket,prefix,operations=body.get('bucket'),body.get('prefix'),body.get('operations')
    if not cfg.prefix_enabled:raise Problem(503,'PREFIX_ACCESS_DISABLED')
    if not isinstance(prefix,str) or not re.fullmatch(r'[A-Za-z0-9_-]+(?:[./A-Za-z0-9_-]*[A-Za-z0-9_-])?/',prefix) or any(x in ('','.','..') for x in prefix[:-1].split('/')) or len(prefix)>240:
        raise Problem(400,'INVALID_PREFIX')
    if not isinstance(operations,list) or not operations or len(operations)!=len(set(operations)) or any(x not in ('GET','LIST','PUT') for x in operations):raise Problem(400,'INVALID_OPERATIONS')
    candidates=[(n,p) for n,p in cfg.profiles.items() if n in allowed and p['bucket']==bucket and set(operations)<=set(p['operations']) and any(prefix.startswith(root) for root in p.get('allowed_prefixes',['']))]
    if not candidates:raise Problem(403,'PREFIX_ACCESS_FORBIDDEN')
    name,_=min(candidates,key=lambda x:(len(x[1]['operations']),x[0]))
    return {**body,'scope_profile':name,'operations':sorted(operations)}


def scope_name(cfg,grant):
    digest=hashlib.sha256(json.dumps([grant['bucket'],grant['prefix'],json.loads(grant['operations'])],separators=(',',':')).encode()).hexdigest()[:32]
    return cfg.deployment_id+'-prefix-'+digest


def statements(cfg,grant,group):
    bucket=grant['bucket'];prefix=grant['prefix'];ops=json.loads(grant['operations'])
    # Both values are selected/validated before this function. Never accept policy text.
    if not re.fullmatch(r'[A-Za-z0-9_-]+',bucket):raise ValueError('INVALID_BUCKET_CONFIGURATION')
    permissions=[]
    if 'GET' in ops:permissions.append('OBJECT_READ')
    if 'PUT' in ops:permissions+=['OBJECT_CREATE','OBJECT_OVERWRITE']
    base=f"Allow group id {group} to "
    result=[]
    if permissions:result.append(base+'{'+', '.join(permissions)+'} in compartment id '+cfg.compartment_id+f" where all {{target.bucket.name = '{bucket}', target.object.name = '{prefix}*'}}")
    if 'LIST' in ops:
        # Listing is bucket-wide authorization; a client Prefix filter is not a security boundary.
        result.append(base+f"{{OBJECT_INSPECT}} in compartment id {cfg.compartment_id} where target.bucket.name = '{bucket}'")
    return result
