"""Documentation-only OpenAPI enrichment; does not alter request handling."""
from fastapi.openapi.utils import get_openapi


def install_reference(app, cfg):
    def schema():
        if app.openapi_schema:
            return app.openapi_schema
        spec = get_openapi(title=app.title, version=app.version,
            description=('Lease OCI S3 credentials using an Identity Domains service access JWT. '
                         'See /unity for onboarding and examples. Credentials use managed key deletion, '
                         'not native STS expiry. Never log credential responses.'), routes=app.routes)
        spec['servers'] = [{'url': cfg.public_url.rstrip('/') or '/'}]
        spec['externalDocs'] = {'description': 'Unity connector integration guide', 'url': (cfg.public_url.rstrip('/') or 'http://127.0.0.1:8080') + '/unity'}
        schemas = spec.setdefault('components', {}).setdefault('schemas', {})
        schemas['LeaseCredentials'] = {
            'type': 'object', 'required': ['lease_id','status','access_key_id','secret_access_key','endpoint','region','scope_profile','expires_at','expiry_mode'],
            'properties': {**{k:{'type':'string'} for k in ['access_key_id','secret_access_key','region','scope_profile']},
                'lease_id':{'type':'string','format':'uuid'}, 'status':{'type':'string','enum':['ACTIVE']},
                'endpoint':{'type':'string','format':'uri'}, 'expires_at':{'type':'string','format':'date-time'},
                'expiry_mode':{'type':'string','enum':['managed_key_deletion']}}}
        schemas['LeaseMetadata'] = {'type':'object','required':['lease_id','status','scope_profile'], 'properties':{
            'lease_id':{'type':'string','format':'uuid'},
            'status':{'type':'string','enum':['PREPARING','SCHEDULE_PENDING','ACTIVE','REVOKE_PENDING','REVOKED']},
            'scope_profile':{'type':'string'},'service_user_id':{'type':'string'},
            **{k:{'type':'integer'} for k in ['profile_version','requested_ttl','generation']},
            **{k:{'type':['string','null'],'format':'date-time'} for k in ['created_at','key_created_at','expires_at','delivered_at','deleted_at']},
            'last_error':{'type':['string','null']}}}
        schemas['ApiError'] = {'type':'object','required':['error'],'properties':{'error':{'type':'object','required':['code','correlation_id'],'properties':{
            'code':{'type':'string'},'correlation_id':{'type':'string'},'lease_id':{'type':'string','format':'uuid'}}}}}
        schemas['Profile']={'type':'object','properties':{'bucket':{'type':'string'},'operations':{'type':'array','items':{'type':'string'}},'version':{'type':'integer'},'group_id':{'type':'string'}}}
        schemas['Configuration']={'type':'object','properties':{
            'mode':{'type':'string'},'region':{'type':'string'},'expiry_mode':{'type':'string'},'replay_seconds':{'type':'integer'},
            'ttl':{'type':'object','properties':{k:{'type':'integer'} for k in ['default','min','max']}},
            'profiles':{'type':'object','additionalProperties':{'$ref':'#/components/schemas/Profile'}},
            'identity':{'type':'object','properties':{'issuer':{'type':'string'},'subject':{'type':'string'}}}}}
        def response(name, description):
            return {'description':description,'content':{'application/json':{'schema':{'$ref':'#/components/schemas/'+name}}}}
        for path, item in spec['paths'].items():
            for method, op in item.items():
                if method not in ('get','post','delete'):continue
                op['tags']=['Automated bucket access' if path=='/v1/bucket-operations' else 'Lease lifecycle' if path.startswith(('/v1/leases','/v1/lease-requests')) and not path.endswith('/tests') else 'Discovery' if path=='/v1/config' else 'Demo diagnostics' if path.startswith('/v1/') else 'Health']
                op['responses'].pop('422',None)
                if path.startswith('/v1/'):
                    for code,desc in [('400','Invalid input or idempotency key'),('403','Identity, scope, profile or ownership not authorized'),('404','Resource not found'),('409','Idempotency conflict, issuance pending or credentials no longer replayable'),('429','Rate limit or pool exhausted; honor Retry-After'),('503','Dependency, cleanup health or issuance failure')]:
                        op['responses'][code]=response('ApiError',desc)
                    op['responses']['401']={'description':'Missing, expired or invalid service JWT. Gateway errors may use a gateway-specific body.'}
        if '/v1/bucket-operations' in spec['paths']:
            operation=spec['paths']['/v1/bucket-operations']['post']
            operation['summary']='Access an authorized bucket automatically'
            operation['description']='Bearer HTTP API. Selects an already-authorized profile and uses the backend OCI workload identity; no S3 credentials or leases are created. LIST is limited to 100 objects with start pagination. GET/PUT are limited to 512 KiB. PUT creates only unless if_match supplies the current ETag. No DELETE, multipart, streaming or native S3 interface.'
            operation['requestBody']['content']['application/json']['example']={'bucket':'lcm-live-v2-sales','operation':'LIST','limit':20}
            operation['responses']['200']={'description':'Authorized operation completed; no credentials in response','content':{'application/json':{'schema':{'type':'object','required':['bucket','operation','selected_profile','credential_mode','lease_created'],'properties':{
                'bucket':{'type':'string'},'operation':{'type':'string','enum':['LIST','GET','PUT']},'selected_profile':{'type':'string'},
                'credential_mode':{'type':'string','enum':['oci_workload_identity']},'lease_created':{'type':'boolean','const':False},
                'key':{'type':'string'},'etag':{'type':['string','null']},'size':{'type':'integer'},'content_base64':{'type':'string','contentEncoding':'base64'},
                'next_start':{'type':['string','null']},'objects':{'type':'array','items':{'type':'object','properties':{'key':{'type':'string'},'size':{'type':'integer'},'etag':{'type':['string','null']}}}}}}}}}
            operation['responses']['412']=response('ApiError','Object exists or ETag no longer matches; inspect before retrying PUT')
            operation['responses']['413']=response('ApiError','Request or object exceeds size limit')
        schemas['LeaseProgress']={'type':'object','required':['request_id','phase','lease'],'properties':{
            'request_id':{'type':'string','format':'uuid'},
            'phase':{'type':'string','enum':['PREPARING','CONFIRMING_SCHEDULE','PREPARING_REPLAY','ACTIVE','REVOKE_PENDING','REVOKED']},
            'lease':{'$ref':'#/components/schemas/LeaseMetadata'}}}
        progress=spec['paths']['/v1/lease-requests/{request_id}']['get']
        progress['parameters'][0]['schema']['format']='uuid'
        progress['summary']='Read issuance progress without exposing credentials'
        progress['description']='Use the UUID Idempotency-Key of the create request. Owner and profile authorization apply. A 404 can mean reservation has not committed yet. Only the create response returns credentials; ACTIVE progress does not include the secret.'
        progress['responses']['200']=response('LeaseProgress','Current committed issuance stage and safe lease metadata')
        post=spec['paths']['/v1/leases']['post']
        post['responses']['422']=response('ApiError','Unsupported profile')
        post['summary']='Create a lease or replay the same request'
        post['description']='One UUID Idempotency-Key per intended lease. Reuse it with the identical body on retries. TTL is measured from IAM key creation. Secret response replay is bounded to 60 seconds.'
        post['parameters']=[{'name':'Idempotency-Key','in':'header','required':True,'schema':{'type':'string','format':'uuid'},'description':'Owner-bound UUID; preserve across retries.'}]
        post['responses']['201']=response('LeaseCredentials','New credential lease created')
        post['responses']['200']=response('LeaseCredentials','Existing credentials replayed within the bounded window')
        post['requestBody']['content']['application/json']['example']={'scope_profile':'sales-read','ttl_seconds':900,'client_request_id':'unity-query-123'}
        schemas['CreateLease']['properties']['ttl_seconds'].update(minimum=cfg.ttl_min,maximum=cfg.ttl_max,description='Seconds from key creation; this is not a native STS expiration.')
        spec['paths']['/v1/config']['get']['responses']['200']=response('Configuration','Profiles authorized for this connector identity and token scopes')
        spec['paths']['/v1/leases/{lease_id}']['get']['responses']['200']=response('LeaseMetadata','Owner-bound lease metadata; no credential secret')
        delete=spec['paths']['/v1/leases/{lease_id}']['delete']
        delete['responses'].pop('200',None)
        delete['responses']['202']={'description':'Revocation requested. Poll GET until status is REVOKED.'}
        delete['responses']['204']={'description':'Lease was already revoked; empty response.'}
        spec['paths']['/v1/leases']['get']['responses']['200']={'description':'Up to 100 most recent owner-bound, authorized leases','content':{'application/json':{'schema':{'type':'object','properties':{'leases':{'type':'array','items':{'$ref':'#/components/schemas/LeaseMetadata'}}}}}}}
        app.openapi_schema=spec
        return spec
    app.openapi=schema
