"""Host-local operator commands. Run as the service user; never via public unauthenticated HTTP."""
import argparse
import json
import time
from pathlib import Path
from shared.runtime import engine

parser=argparse.ArgumentParser()
parser.add_argument('action',choices=['status','pause','resume','drain','reconcile','repair-schedule','reconcile-user','export-audit'])
parser.add_argument('--lease-id')
parser.add_argument('--profile')
parser.add_argument('--user-id')
args=parser.parse_args()
e=engine()
if args.action=='status':
    print(json.dumps(e.metrics(),indent=2))
elif args.action in ('pause','resume'):
    e.pause(args.action=='pause')
    print('PAUSED' if args.action=='pause' else 'RESUMED')
elif args.action=='drain':
    e.pause()
    with e.db.connect() as c:
        rows=[dict(r) for r in c.execute("SELECT * FROM leases WHERE state!='REVOKED'") if not args.profile or r['profile']==args.profile]
    for r in rows:
        e.revoke(r['id'],{'issuer':r['issuer'],'subject':r['subject']})
    e.reconcile()
    with e.db.connect() as c:
        outstanding=c.execute("SELECT COUNT(*) FROM leases WHERE state!='REVOKED'").fetchone()[0]
        users=[r['id'] for r in c.execute('SELECT id FROM users')]
    key_count=sum(len(e.cloud.list_keys(u)) for u in users)
    print(json.dumps({'outstanding_leases':outstanding,'remaining_managed_user_keys':key_count,'safe_to_remove':outstanding==0 and key_count==0}))
    if outstanding or key_count:raise SystemExit(1)
elif args.action=='reconcile':
    e.reconcile();print('COMPLETE')
elif args.action=='repair-schedule':
    r=e.db.lease(args.lease_id)
    if not r:raise SystemExit('Lease not found')
    print(e.setup(r['id'],r['generation']))
elif args.action=='reconcile-user':
    if not args.user_id:raise SystemExit('--user-id is required')
    with e.db.connect() as c:
        if not c.execute('SELECT 1 FROM users WHERE id=?',(args.user_id,)).fetchone():raise SystemExit('User outside managed pool')
    e.reconcile();print('Reconciled; quarantines require independent operator review before release.')
elif args.action=='export-audit':
    with e.db.connect() as c:
        for r in c.execute('SELECT * FROM audit ORDER BY seq'):print(json.dumps(dict(r)))
