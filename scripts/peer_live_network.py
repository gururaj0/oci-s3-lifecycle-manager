"""Add only the LCM peering route to the existing DemoBastion subnet route table.
Preserves all current routes with etag concurrency; cleanup removes only this owned entry.
"""
import json
from pathlib import Path
import oci
from deploy_oci import ROOT
config=oci.config.from_file();network=oci.core.VirtualNetworkClient(config)
settings=json.loads((ROOT/'.local/live-variables.json').read_text())
peers=network.list_local_peering_gateways(settings['compartment_ocid'],vcn_id=settings['vcn_id']).data
matching=[p for p in peers if p.display_name==settings.get('prefix','lcm-live-v2')+'-from-demo' and p.lifecycle_state=='AVAILABLE']
if len(matching)!=1:raise SystemExit('Expected exactly one available LCM Demo peering gateway')
peer=matching[0].id
route_id='ocid1.routetable.oc1.iad.aaaaaaaat5s3oo32uehrwqyxwoh2d5fykdzk6pqv45umpkeyjfzo2by4k63a'
for attempt in range(3):
 response=network.get_route_table(route_id);rules=response.data.route_rules
 matches=[x for x in rules if x.destination=='10.73.0.0/16']
 if matches:
  if any(x.network_entity_id!=peer for x in matches):raise SystemExit('Conflicting peering route; no route was changed')
  break
 rules.append(oci.core.models.RouteRule(destination='10.73.0.0/16',destination_type='CIDR_BLOCK',network_entity_id=peer,description='LCM live v2: DemoBastion to dedicated private VCN'))
 try:
  network.update_route_table(route_id,oci.core.models.UpdateRouteTableDetails(route_rules=rules),if_match=response.headers['etag']);break
 except oci.exceptions.ServiceError as exc:
  if exc.status!=412 or attempt==2:raise
print(json.dumps({'route_table_id':route_id,'destination':'10.73.0.0/16','peering_gateway_id':peer,'existing_routes_preserved':True}))
