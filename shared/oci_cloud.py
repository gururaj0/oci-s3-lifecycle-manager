"""Live workload-principal OCI adapters. No user API key is accepted by the runtime."""
import base64
from datetime import datetime,timedelta,timezone
import json
import math
import time
import uuid
import oci

class OCICloud:
    def __init__(self,cfg,ledger,clients=None):
        self.cfg,self.db=cfg,ledger
        signer=oci.auth.signers.get_resource_principals_signer() if cfg.workload=='function' else oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        opts={'region':cfg.region}
        self.identity=oci.identity.IdentityClient(opts,signer=signer,timeout=(5,20))
        self.vault=oci.vault.VaultsClient(opts,signer=signer,timeout=(5,20))
        self.secrets=oci.secrets.SecretsClient(opts,signer=signer,timeout=(5,20))
        self.scheduler=oci.resource_scheduler.ScheduleClient(opts,signer=signer,timeout=(5,20))
        self.monitoring=oci.monitoring.MonitoringClient(opts,signer=signer,
            service_endpoint='https://telemetry-ingestion.'+cfg.region+'.oraclecloud.com',timeout=(5,10))
        self.managed={u for users in cfg.service_users.values() for u in users}

    def fault(self,_):pass

    def publish_metrics(self,metrics):
        now=datetime.now(timezone.utc)
        dimensions={'deployment':self.cfg.deployment_id,'workload':self.cfg.workload}
        values={'CleanupHeartbeat':1,'OverdueKeys':metrics['overdue'],
            'QuarantinedUsers':sum(x['quarantined'] for x in metrics['pools']),
            'AllocatedUsers':sum(x['allocated'] for x in metrics['pools']),
            'CleanupHealthy':int(metrics['healthy'])}
        values.update({'Leases'+k:int(v) for k,v in metrics['states'].items()})
        with self.db.connect() as c:
            for action in ('DELIVERED','DELETE_RETRY','COMPENSATION_REQUIRED','QUARANTINED_ORPHAN','QUARANTINED_IDENTITY_DRIFT'):
                values['Audit'+action]=c.execute('SELECT COUNT(*) FROM audit WHERE action=?',(action,)).fetchone()[0]
            delay=c.execute("SELECT MAX(deleted_at-expires_at) FROM leases WHERE state='REVOKED' AND revoke_intent=0").fetchone()[0]
            if delay is not None:values['RevocationDelaySeconds']=max(0,delay)
        data=[oci.monitoring.models.MetricDataDetails(namespace='lcm_live',compartment_id=self.cfg.compartment_id,
            name=name,dimensions=dimensions,datapoints=[oci.monitoring.models.Datapoint(timestamp=now,value=float(value))]) for name,value in values.items()]
        result=self.monitoring.post_metric_data(oci.monitoring.models.PostMetricDataDetails(metric_data=data)).data
        if result.failed_metrics_count:raise RuntimeError('METRIC_PUBLICATION_FAILED')

    def _user(self,user):
        if user not in self.managed:raise PermissionError('UNMANAGED_USER')

    def list_keys(self,user):
        self._user(user)
        result=oci.pagination.list_call_get_all_results(self.identity.list_customer_secret_keys,user).data
        return [{'id':x.id,'user_id':user,'display_name':x.display_name,'created':x.time_created.timestamp()} for x in result]

    def validate_user(self,user):
        self._user(user)
        profile=next(p for p,users in self.cfg.service_users.items() if user in users)
        value=self.identity.get_user(user).data
        caps=oci.util.to_dict(value.capabilities)
        if value.lifecycle_state!='ACTIVE' or not caps.get('can_use_customer_secret_keys') or any(v for k,v in caps.items() if k!='can_use_customer_secret_keys'):
            raise PermissionError('IDENTITY_CAPABILITY_DRIFT')
        groups=oci.pagination.list_call_get_all_results(self.identity.list_user_group_memberships,
            self.cfg.tenancy_id,user_id=user).data
        if {g.group_id for g in groups}!={self.cfg.profiles[profile]['group_id']}:
            raise PermissionError('IDENTITY_MEMBERSHIP_DRIFT')

    def create_key(self,user,display_name):
        self._user(user)
        if not display_name.startswith(self.cfg.deployment_id+'/'):raise PermissionError('DISPLAY_NAME_OWNERSHIP')
        value=self.identity.create_customer_secret_key(oci.identity.models.CreateCustomerSecretKeyDetails(display_name=display_name),user,
            retry_strategy=oci.retry.NoneRetryStrategy()).data
        return {'id':value.id,'secret':value.key,'created':value.time_created.timestamp()}

    def delete_key(self,user,key):
        self._user(user)
        try:self.identity.delete_customer_secret_key(user,key,retry_strategy=oci.retry.NoneRetryStrategy())
        except oci.exceptions.ServiceError as exc:
            if exc.status!=404:raise
        # A 404 is never enough. List must succeed and confirm actual absence.
        return not any(k['id']==key for k in self.list_keys(user))

    def store_response(self,payload,expires):
        name=self.cfg.deployment_id+'-replay-'+payload['lease_id']
        details=oci.vault.models.CreateSecretDetails(compartment_id=self.cfg.secret_compartment_id,
            vault_id=self.cfg.vault_id,key_id=self.cfg.vault_key_id,secret_name=name,
            secret_content=oci.vault.models.Base64SecretContentDetails(content_type='BASE64',
                content=base64.b64encode(json.dumps(payload).encode()).decode()),
            metadata={'expires_at':str(expires),'lease_id':payload['lease_id'],'deployment':self.cfg.deployment_id},
            freeform_tags={'application':'oci-s3-lcm','deployment':self.cfg.deployment_id})
        try:
            secret=self.vault.create_secret(details,retry_strategy=oci.retry.NoneRetryStrategy()).data
        except (oci.exceptions.RequestException,oci.exceptions.ServiceError):
            # A lost reply must not orphan an indefinitely retrievable response.
            for summary in oci.pagination.list_call_get_all_results(self.vault.list_secrets,self.cfg.secret_compartment_id).data:
                if summary.secret_name==name:self.remove_response(summary.id)
            raise
        with self.db.tx() as c:
            c.execute('INSERT INTO replay_refs(id,expires,removed) VALUES(?,?,0)',(secret.id,expires))
        for _ in range(120):
            current=self.vault.get_secret(secret.id).data
            if current.lifecycle_state=='ACTIVE':return secret.id
            if current.lifecycle_state in ('FAILED','PENDING_DELETION','DELETED') or time.time()>=expires:
                raise RuntimeError('REPLAY_SECRET_NOT_READY')
            time.sleep(0.5)
        raise TimeoutError('REPLAY_SECRET_ACTIVATION_TIMEOUT')

    def get_response(self,ref):
        if not ref:return None
        with self.db.connect() as c:
            r=c.execute('SELECT expires,removed FROM replay_refs WHERE id=?',(ref,)).fetchone()
        if not r or r['removed'] or r['expires']<=time.time():return None
        try:
            data=self.secrets.get_secret_bundle(ref).data
            return json.loads(base64.b64decode(data.secret_bundle_content.content))
        except oci.exceptions.ServiceError as exc:
            if exc.status in (404,409):return None
            raise

    def remove_response(self,ref):
        if not ref:return
        secret=self.vault.get_secret(ref).data
        for _ in range(40):
            if secret.lifecycle_state not in ('CREATING','UPDATING'):break
            time.sleep(0.5);secret=self.vault.get_secret(ref).data
        if secret.compartment_id!=self.cfg.secret_compartment_id or secret.vault_id!=self.cfg.vault_id or (secret.freeform_tags or {}).get('deployment')!=self.cfg.deployment_id:
            raise PermissionError('SECRET_OWNERSHIP')
        if secret.lifecycle_state not in ('SCHEDULING_DELETION','PENDING_DELETION','DELETING','DELETED'):
            self.vault.schedule_secret_deletion(ref,oci.vault.models.ScheduleSecretDeletionDetails(
                time_of_deletion=datetime.now(timezone.utc)+timedelta(days=1,minutes=5)))
        for _ in range(40):
            state=self.vault.get_secret(ref).data.lifecycle_state
            if state in ('PENDING_DELETION','DELETING','DELETED'):return
            time.sleep(0.5)
        raise TimeoutError('SECRET_DELETION_CONFIRMATION_PENDING')
        # Pending deletion blocks bundle retrieval immediately; physical deletion follows retention.
        # Called while a lease transaction may hold the writer lock, so it must not nest db.tx.

    def purge(self):
        with self.db.connect() as c:
            refs=[r['id'] for r in c.execute('SELECT id FROM replay_refs WHERE expires<=? AND removed=0',(time.time(),))]
        for ref in refs:
            self.remove_response(ref)
            with self.db.tx() as c:c.execute('UPDATE replay_refs SET removed=1 WHERE id=?',(ref,))
        # Find a response created before a crash that happened before replay_ref persistence.
        for summary in oci.pagination.list_call_get_all_results(self.vault.list_secrets,self.cfg.secret_compartment_id).data:
            if (summary.freeform_tags or {}).get('deployment')!=self.cfg.deployment_id or '-replay-' not in summary.secret_name:continue
            if summary.lifecycle_state in ('SCHEDULING_DELETION','PENDING_DELETION','DELETING','DELETED'):continue
            secret=self.vault.get_secret(summary.id).data
            if float(secret.metadata.get('expires_at','0'))<=time.time():self.remove_response(secret.id)

    def ensure_schedule(self,lease_id,generation,expires):
        if not self.cfg.resource_tags:raise PermissionError('SCHEDULE_OWNERSHIP_TAG_REQUIRED')
        name=f'{self.cfg.deployment_id}-{lease_id}-{generation}'
        matches=[s for s in oci.pagination.list_call_get_all_results(self.scheduler.list_schedules,
            compartment_id=self.cfg.scheduler_compartment_id).data if s.display_name==name and s.lifecycle_state in ('ACTIVE','CREATING','UPDATING')
            and (s.freeform_tags or {}).get('deployment')==self.cfg.deployment_id]
        if matches:
            chosen=sorted(matches,key=lambda s:s.id)[0]
            for duplicate in matches:
                if duplicate.id!=chosen.id:self.remove_schedule(duplicate.id)
            return self._confirm_schedule(chosen.id,lease_id,generation,expires)
        due=datetime.fromtimestamp(math.ceil(expires/60)*60,timezone.utc)
        body={'lease_id':lease_id,'generation':generation}
        details=oci.resource_scheduler.models.CreateScheduleDetails(compartment_id=self.cfg.scheduler_compartment_id,
            display_name=name,action='START_RESOURCE',recurrence_type='ICAL',recurrence_details='FREQ=DAILY;COUNT=1',
            time_starts=due,local_time_zone='UTC',resources=[oci.resource_scheduler.models.Resource(
                id=self.cfg.functions['delete'],parameters=[oci.resource_scheduler.models.BodyParameter(value=body)])],
            freeform_tags={'deployment':self.cfg.deployment_id,'lease_id':lease_id,'generation':str(generation)},defined_tags=self.cfg.resource_tags)
        try:
            schedule_id=self.scheduler.create_schedule(details,retry_strategy=oci.retry.NoneRetryStrategy()).data.id
        except (oci.exceptions.RequestException,oci.exceptions.ServiceError):
            matches=[s for s in oci.pagination.list_call_get_all_results(self.scheduler.list_schedules,
                compartment_id=self.cfg.scheduler_compartment_id).data if s.display_name==name and s.lifecycle_state in ('ACTIVE','CREATING','UPDATING')
                and (s.freeform_tags or {}).get('deployment')==self.cfg.deployment_id]
            if len(matches)==1:return self._confirm_schedule(matches[0].id,lease_id,generation,expires)
            raise
        return self._confirm_schedule(schedule_id,lease_id,generation,expires)

    def _confirm_schedule(self,schedule_id,lease_id,generation,expires):
        for _ in range(180):
            value=self.scheduler.get_schedule(schedule_id).data
            if value.lifecycle_state=='ACTIVE':
                expected=datetime.fromtimestamp(math.ceil(expires/60)*60,timezone.utc)
                resources=value.resources or []
                body={'lease_id':lease_id,'generation':generation}
                actual=resources[0].parameters[0].value if resources and resources[0].parameters else None
                if isinstance(actual,str):actual=json.loads(actual)
                if (value.time_starts!=expected or value.recurrence_details!='FREQ=DAILY;COUNT=1'
                    or value.action!='START_RESOURCE' or len(resources)!=1
                    or resources[0].id!=self.cfg.functions['delete']
                    or len(resources[0].parameters or [])!=1 or actual!=body):
                    raise PermissionError('SCHEDULE_CONFIGURATION_DRIFT')
                return schedule_id
            if value.lifecycle_state in ('FAILED','DELETED','DELETING'):
                raise RuntimeError('SCHEDULE_NOT_ACTIVE')
            time.sleep(.5)
        raise TimeoutError('SCHEDULE_ACTIVATION_PENDING')

    def remove_schedule(self,schedule_id):
        try:
            schedule=self.scheduler.get_schedule(schedule_id).data
            if schedule.compartment_id!=self.cfg.scheduler_compartment_id or (schedule.freeform_tags or {}).get('deployment')!=self.cfg.deployment_id or any(schedule.defined_tags.get(ns,{}).get(k)!=v for ns,tags in self.cfg.resource_tags.items() for k,v in tags.items()):
                raise PermissionError('SCHEDULE_OWNERSHIP')
            self.scheduler.delete_schedule(schedule_id)
        except oci.exceptions.ServiceError as exc:
            if exc.status!=404:raise
