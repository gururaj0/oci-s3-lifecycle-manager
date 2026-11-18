"""Provisioner hardening for dedicated prefix users (provider omits DB/OAuth capabilities)."""
import json
from pathlib import Path
import oci

ROOT=Path(__file__).resolve().parents[1]

def main():
    settings=json.loads((ROOT/'.local/live-variables.json').read_text())
    state=json.loads((ROOT/'.local/live.tfstate').read_text())
    cfg=oci.config.from_file()
    iam=oci.identity.IdentityClient(cfg)
    compartment=iam.get_compartment(settings['compartment_ocid']).data
    assert compartment.name=='Demo' and compartment.id==settings['compartment_ocid']
    users=[i['attributes'] for r in state['resources'] if r['type']=='oci_identity_user' and r['name']=='prefix_pool' for i in r['instances']]
    assert len(users)==4
    for user in users:
        current=iam.get_user(user['id']).data
        assert current.name==user['name'] and '-prefix-user-' in current.name
        iam.update_user_capabilities(user['id'],oci.identity.models.UpdateUserCapabilitiesDetails(
            can_use_console_password=False,can_use_api_keys=False,can_use_auth_tokens=False,
            can_use_smtp_credentials=False,can_use_db_credentials=False,
            can_use_customer_secret_keys=True,can_use_o_auth2_client_credentials=False))
        caps=oci.util.to_dict(iam.get_user(user['id']).data.capabilities)
        assert caps['can_use_customer_secret_keys'] and not any(v for k,v in caps.items() if k!='can_use_customer_secret_keys')
    print('Four dedicated prefix users hardened; only Customer Secret Keys enabled.')

if __name__=='__main__':main()
