"""Read-only M0 capability report. Not a live credential adapter.
No keys, schedules, users or IAM policies are created by this module.
"""
def sdk_report():
    import oci
    identity=oci.identity.IdentityClient
    return {'sdk_version':oci.__version__,
            'credential_operations':{name:hasattr(identity,name) for name in
                ('create_customer_secret_key','list_customer_secret_keys','delete_customer_secret_key')},
            'scheduler_create_fields':list(oci.resource_scheduler.models.CreateScheduleDetails().swagger_types),
            'scheduler_resource_fields':list(oci.resource_scheduler.models.Resource().swagger_types),
            'validated_live':False,'production_ready':False}

if __name__=='__main__':
    import json
    print(json.dumps(sdk_report(),indent=2))
