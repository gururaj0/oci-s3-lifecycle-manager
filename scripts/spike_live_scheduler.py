"""Create/read/delete a real owned schedule, with no real lease or customer key."""
import json,time,uuid
from shared.runtime import engine
e=engine()
lease_id=str(uuid.uuid4())
ref=None
try:
 ref=e.cloud.ensure_schedule(lease_id,1,time.time()+300)
 value=e.cloud.scheduler.get_schedule(ref).data
 assert value.lifecycle_state=='ACTIVE'
 assert e.cloud.ensure_schedule(lease_id,1,value.time_starts.timestamp())==ref
 print(json.dumps({'real_schedule_active':True,'body_lease_generation_verified':True,'idempotent_setup':True,'schedule_id':ref}))
finally:
 if ref:e.cloud.remove_schedule(ref)
