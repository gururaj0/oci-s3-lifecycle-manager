"""On-demand, deployment-owned prefix groups/policies and temporary membership."""
import oci
import json,re
from shared.prefix_access import scope_name,statements

class PrefixCloud:
    def _grant(self,lease):
        with self.db.connect() as c:return dict(c.execute('SELECT * FROM prefix_grants WHERE lease_id=?',(lease,)).fetchone())

    def ensure_prefix_membership(self,lease,user):
        self._user(user);grant=self._grant(lease);name=scope_name(self.cfg,grant)
        tags={'application':'oci-s3-lcm','deployment':self.cfg.deployment_id,'purpose':'prefix-access'}
        groups=oci.pagination.list_call_get_all_results(self.identity.list_groups,self.cfg.tenancy_id).data
        group=next((g for g in groups if g.name==name),None)
        if group is None:
            if sum(g.name.startswith(self.cfg.deployment_id+'-prefix-') for g in groups)>=self.cfg.prefix_scope_limit:raise RuntimeError('PREFIX_SCOPE_LIMIT')
            try:group=self.identity.create_group(oci.identity.models.CreateGroupDetails(compartment_id=self.cfg.tenancy_id,name=name,description='LCM on-demand prefix access',freeform_tags=tags),retry_strategy=oci.retry.NoneRetryStrategy()).data
            except oci.exceptions.ServiceError as exc:
                if exc.status!=409:raise
                groups=oci.pagination.list_call_get_all_results(self.identity.list_groups,self.cfg.tenancy_id).data
                group=next(g for g in groups if g.name==name)
        if any(group.freeform_tags.get(k)!=v for k,v in tags.items()):raise PermissionError('PREFIX_GROUP_OWNERSHIP')
        desired=statements(self.cfg,grant,group.id)
        policies=oci.pagination.list_call_get_all_results(self.identity.list_policies,self.cfg.compartment_id).data
        policy=next((p for p in policies if p.name==name),None)
        if policy is None:
            try:policy=self.identity.create_policy(oci.identity.models.CreatePolicyDetails(compartment_id=self.cfg.compartment_id,name=name,description='LCM prefix-scoped object permissions; LIST exposes bucket names',statements=desired,freeform_tags=tags),retry_strategy=oci.retry.NoneRetryStrategy()).data
            except oci.exceptions.ServiceError as exc:
                if exc.status!=409:raise
                policies=oci.pagination.list_call_get_all_results(self.identity.list_policies,self.cfg.compartment_id).data
                policy=next(p for p in policies if p.name==name)
        if set(policy.statements)!=set(desired) or any(policy.freeform_tags.get(k)!=v for k,v in tags.items()):raise PermissionError('PREFIX_POLICY_DRIFT')
        with self.db.tx() as c:
            row=c.execute('SELECT state FROM leases WHERE id=?',(lease,)).fetchone()
            if row['state']!='PREPARING':raise RuntimeError('LEASE_NO_LONGER_PREPARING')
            c.execute('UPDATE prefix_grants SET group_id=?,policy_id=? WHERE lease_id=?',(group.id,policy.id,lease))
        with self.db.tx() as c:
            row=c.execute('SELECT state FROM leases WHERE id=?',(lease,)).fetchone()
            if row['state']!='PREPARING':raise RuntimeError('LEASE_NO_LONGER_PREPARING')
            # Serialize membership grant against revoke and stale-create recovery.
            memberships=oci.pagination.list_call_get_all_results(self.identity.list_user_group_memberships,self.cfg.tenancy_id,user_id=user).data
            if any(m.group_id!=group.id for m in memberships):raise PermissionError('IDENTITY_MEMBERSHIP_DRIFT')
            member=next((m for m in memberships if m.group_id==group.id),None)
            if member is None:
                member=self.identity.add_user_to_group(oci.identity.models.AddUserToGroupDetails(user_id=user,group_id=group.id),retry_strategy=oci.retry.NoneRetryStrategy()).data
            c.execute('UPDATE prefix_grants SET membership_id=? WHERE lease_id=?',(member.id,lease))
        # A lost add reply is recovered by listing membership during compensation.

    def remove_prefix_membership(self,grant,user):
        self._user(user)
        members=oci.pagination.list_call_get_all_results(self.identity.list_user_group_memberships,self.cfg.tenancy_id,user_id=user).data
        # The group ID was durably recorded before attempting membership creation.
        if any(member.group_id==grant['group_id'] for member in members):
            self._remove_domain_membership(grant,user)
        remaining=oci.pagination.list_call_get_all_results(self.identity.list_user_group_memberships,self.cfg.tenancy_id,user_id=user).data
        if remaining:raise RuntimeError('PREFIX_MEMBERSHIP_NOT_REMOVED')

    def _remove_domain_membership(self,grant,user):
        # IAM's legacy RemoveUserFromGroup proxy can reject the domain PATCH even
        # with the resource-scoped permissions. Use the domain API explicitly.
        group=self.identity.get_group(grant['group_id']).data
        if group.name!=scope_name(self.cfg,grant) or group.freeform_tags.get('deployment')!=self.cfg.deployment_id:
            raise PermissionError('PREFIX_GROUP_OWNERSHIP')
        endpoint=self.cfg.oauth['token_endpoint'].split('/oauth2/')[0]
        client=oci.identity_domains.IdentityDomainsClient({'region':self.cfg.region},
            signer=self.identity.base_client.signer,service_endpoint=endpoint,timeout=(5,20))
        groups=client.list_groups(filter='displayName eq '+json.dumps(group.name),attributes='id,ocid').data.resources
        name=self.identity.get_user(user).data.name
        users=client.list_users(filter='userName eq '+json.dumps(name),attributes='id,ocid').data.resources
        if len(groups)!=1 or len(users)!=1 or groups[0].ocid!=group.id or users[0].ocid!=user:
            raise PermissionError('PREFIX_DOMAIN_IDENTITY_MISMATCH')
        if not re.fullmatch(r'[a-fA-F0-9-]+',users[0].id):raise PermissionError('INVALID_DOMAIN_USER_ID')
        client.patch_group(groups[0].id,patch_op=oci.identity_domains.models.PatchOp(
            schemas=['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
            operations=[oci.identity_domains.models.Operations(op='REMOVE',path='members[value eq "'+users[0].id+'"]')]),
            retry_strategy=oci.retry.NoneRetryStrategy())
