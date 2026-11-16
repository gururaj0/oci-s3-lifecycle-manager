from types import SimpleNamespace as N
from unittest.mock import Mock
import pytest
import oci
from shared.oci_cloud import OCICloud
from shared.oracle_ledger import Row,Connection


def error(status):return oci.exceptions.ServiceError(status,'Test',{},'redacted')

def cloud():
    c=OCICloud.__new__(OCICloud);c.managed={'managed'};c.cfg=N(deployment_id='deploy')
    c.identity=Mock();return c


def test_live_foreign_user_rejected_before_network():
    c=cloud()
    with pytest.raises(PermissionError):c.create_key('foreign','deploy/lease')
    c.identity.create_customer_secret_key.assert_not_called()


def test_delete_404_is_not_proof_of_absence():
    c=cloud();c.identity.delete_customer_secret_key.side_effect=error(404)
    c.list_keys=Mock(side_effect=error(403))
    with pytest.raises(oci.exceptions.ServiceError):c.delete_key('managed','key')
    c.list_keys=Mock(return_value=[{'id':'key'}])
    assert c.delete_key('managed','key') is False
    c.list_keys=Mock(return_value=[])
    assert c.delete_key('managed','key') is True


def test_live_create_does_not_enable_automatic_retry():
    c=cloud();c.identity.create_customer_secret_key.return_value=N(data=N(id='access',key='secret',time_created=N(timestamp=lambda:12)))
    value=c.create_key('managed','deploy/lease')
    assert value=={'id':'access','secret':'secret','created':12}
    assert isinstance(c.identity.create_customer_secret_key.call_args.kwargs['retry_strategy'],oci.retry.NoneRetryStrategy)


def test_oracle_rows_support_named_and_positional_domain_reads():
    r=Row(['id','state'],('lease','ACTIVE'))
    assert dict(r)=={'id':'lease','state':'ACTIVE'} and r[0]=='lease' and r['state']=='ACTIVE'


def test_oracle_limit_and_bind_translation():
    raw=Mock();cur=raw.cursor.return_value;cur.description=[('ID',)];cur.fetchone.return_value=('u1',)
    c=Connection(raw);r=c.execute('SELECT id FROM users WHERE profile=? ORDER BY id LIMIT 1',('sales-read',)).fetchone()
    assert r['id']=='u1'
    sql,values=cur.execute.call_args.args
    assert sql=='SELECT id FROM lcm_users WHERE profile=:1 ORDER BY id FETCH FIRST 1 ROWS ONLY'
    assert values==('sales-read',)


def test_schedule_checks_normalized_json_and_rejects_drift():
    from datetime import datetime,timezone
    c=cloud();c.cfg=N(functions={'delete':'required-function'})
    c.scheduler=Mock()
    value=N(lifecycle_state='ACTIVE',time_starts=datetime.fromtimestamp(120,timezone.utc),
        recurrence_details='FREQ=DAILY;COUNT=1',action='START_RESOURCE',
        resources=[N(id='required-function',parameters=[N(value='{"lease_id":"lease","generation":1}')])])
    c.scheduler.get_schedule.return_value=N(data=value)
    assert c._confirm_schedule('owned','lease',1,119)=='owned'
    value.resources[0].parameters[0].value='{"lease_id":"other","generation":1}'
    with pytest.raises(PermissionError):c._confirm_schedule('owned','lease',1,119)


def test_identity_drift_rejects_extra_group_and_unrelated_capabilities():
    c=cloud();c.cfg=N(service_users={'read':['managed']},tenancy_id='tenancy',profiles={'read':{'group_id':'only-group'}})
    caps=N(attribute_map={'can_use_customer_secret_keys':'canUseCustomerSecretKeys','can_use_api_keys':'canUseApiKeys'},
        swagger_types={'can_use_customer_secret_keys':'bool','can_use_api_keys':'bool'},can_use_customer_secret_keys=True,can_use_api_keys=False)
    c.identity.get_user.return_value=N(data=N(lifecycle_state='ACTIVE',capabilities=caps))
    c.identity.list_user_group_memberships.return_value=oci.response.Response(200,{},[N(group_id='only-group')],None)
    c.identity.list_user_group_memberships.__name__='list_user_group_memberships'
    c.validate_user('managed')
    caps.can_use_api_keys=True
    with pytest.raises(PermissionError):c.validate_user('managed')
    caps.can_use_api_keys=False
    c.identity.list_user_group_memberships.return_value.data.append(N(group_id='extra'))
    with pytest.raises(PermissionError):c.validate_user('managed')
