"""Real positive/negative schedule-principal invocation. No customer keys are created."""
from datetime import datetime,timezone
import json,math,time,uuid
from pathlib import Path
import oci
root=Path(__file__).resolve().parents[1]
cfg=json.loads((root/'.local/live-config.json').read_text())
client=oci.resource_scheduler.ScheduleClient(oci.config.from_file())
created=[];results={}
due=datetime.fromtimestamp(math.ceil((time.time()+120)/60)*60,timezone.utc)
try:
 for role in ('delete','sentinel'):
  item=client.create_schedule(oci.resource_scheduler.models.CreateScheduleDetails(
    compartment_id=cfg['compartment_id'],display_name=cfg['deployment_id']+'-capability-'+role+'-'+str(uuid.uuid4()),
    action='START_RESOURCE',recurrence_type='ICAL',recurrence_details='FREQ=DAILY;COUNT=1',time_starts=due,
    local_time_zone='UTC',defined_tags=cfg['resource_tags'],freeform_tags={'deployment':cfg['deployment_id'],'purpose':'capability-probe'},
    resources=[oci.resource_scheduler.models.Resource(id=cfg['functions'][role],parameters=[oci.resource_scheduler.models.BodyParameter(value={'lease_id':str(uuid.uuid4()),'generation':1})])])).data
  created.append((role,item.id))
 deadline=due.timestamp()+240
 while time.time()<deadline:
  results={role:client.get_schedule(ref).data.last_run_status for role,ref in created}
  if all(v in ('SUCCEEDED','FAILED') for v in results.values()):break
  time.sleep(5)
 assert results.get('delete')=='SUCCEEDED','Approved schedule invocation did not succeed: '+str(results)
 assert results.get('sentinel')=='FAILED','Unapproved schedule invocation was not denied: '+str(results)
 print(json.dumps({'real_schedule_principal_allowed':True,'unapproved_function_denied':True,'results':results,'customer_keys_created':False},indent=2))
finally:
 for role,ref in created:client.delete_schedule(ref)
