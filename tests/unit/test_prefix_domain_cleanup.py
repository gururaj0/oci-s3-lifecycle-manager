from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from shared.prefix_cloud import PrefixCloud
from shared.prefix_access import scope_name

@pytest.mark.parametrize('mismatch',[False,True])
def test_domain_cleanup_targets_only_lease_member(monkeypatch,mismatch):
    cloud=PrefixCloud()
    cloud.cfg=NS(deployment_id='test',region='us-ashburn-1',oauth={'token_endpoint':'https://example.invalid/oauth2/v1/token'})
    grant={'bucket':'sales','prefix':'finance/','operations':'["GET"]','group_id':'ocid.group.test'}
    group=NS(id=grant['group_id'],name=scope_name(cloud.cfg,grant),freeform_tags={'deployment':'test'})
    cloud.identity=Mock();cloud.identity.get_group.return_value.data=group
    cloud.identity.get_user.return_value.data.name='lease-user'
    client=Mock()
    client.list_groups.return_value.data.resources=[NS(id='group-scim-id',ocid=group.id)]
    client.list_users.return_value.data.resources=[NS(id='123abc',ocid='foreign-user' if mismatch else 'ocid.user.test')]
    monkeypatch.setattr('oci.identity_domains.IdentityDomainsClient',lambda *args,**kwargs:client)
    if mismatch:
        with pytest.raises(PermissionError,match='IDENTITY_MISMATCH'):cloud._remove_domain_membership(grant,'ocid.user.test')
        client.patch_group.assert_not_called()
    else:
        cloud._remove_domain_membership(grant,'ocid.user.test')
        args,kw=client.patch_group.call_args
        assert args==('group-scim-id',)
        operations=kw['patch_op'].operations
        assert len(operations)==1 and operations[0].op=='REMOVE'
        assert operations[0].path=='members[value eq "123abc"]'
