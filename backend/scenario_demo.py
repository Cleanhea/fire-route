"""Local authenticated ten-floor viewer backed by actual CSE records."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import queue
import signal
import sys
from threading import Event,Lock,Thread
import time

from backend.auth import AuthStore
from backend.config import Settings
from backend.evidence import Evidence
from backend.http_api import api_server
from backend.scenario_core import ScenarioSession
from backend.views import latest,unknown_view
from onem2m.client import ProtocolError,TransportError,strict_json
from simulator.commands import utc_time
from simulator.scenario_frames import ROOT,ScenarioFrame,read_trace,trace_digest


class ScenarioApp:
    def __init__(self,settings,trace):
        self.settings,self.trace = settings,trace
        self.context = None
        self.lock = Lock();self.jobs = queue.Queue(maxsize=1);self.stop = Event()
        self.busy,self.paused,self.state = True,True,'INITIALIZING'
        self.pending_raw=None
        self.remaining=self.advanced=0
        self.recovery_deadline=None
        self.resume_after_recovery=False
        self.audit_evidence = Evidence(settings.evidence_root)
        from mec.risk.evacuation import Navigation
        self.building = Navigation().document()

    def audit(self,method,path,status,role):
        self.audit_evidence.append('http.jsonl',{'method':method,'path':path,'http_status':status,'role':role})

    def status(self):
        with self.lock:
            return {'state':self.state,'incident_id':self.context.incident_id if self.context else None,
                'busy':self.busy,'paused':self.paused,'profile_id':'fire_evac_10f_v1',
                'data_origin':'SYNTHETIC','MEC':'UNAVAILABLE','model_provenance':'ASSUMED_NOT_CALIBRATED'}

    def enqueue(self,action):
        with self.lock:
            if self.busy:raise RuntimeError('Busy')
            if self.state in {'RECOVERING','DEGRADED'} and action!='reset':
                raise RuntimeError('Pending recovery or explicit reset required')
            self.busy = True
            self.jobs.put_nowait(action)
            return 'scenario-'+str(time.monotonic_ns())

    def view(self,incident,role,*,diagnostics=False):
        if role not in {'resident','responder'} or diagnostics and role!='responder':
            raise PermissionError('Responder required')
        with self.lock:context,busy,state = self.context,self.busy,self.state
        if context is None or incident != context.incident_id:raise LookupError('Unknown incident')
        if state=='RECOVERING':raise TransportError('Source communication recovery pending')
        if state=='DEGRADED':return unknown_view(incident,cse='UNKNOWN')
        client = context.clients[role]
        route = latest(client,context.resources,'controller/routes')
        status = latest(client,context.resources,'controller/status')
        result = unknown_view(incident,cse='LIVE')
        keys = ['event_id','incident_id','route_version','simulation_second','policy_sha256']
        if route.get('incident_id')!=incident or any(route.get(k)!=status.get(k) for k in keys):
            return {**result,'guidance_status':'PENDING'}
        if type(route.get('route_version')) is not int or route['route_version']<1:
            raise ProtocolError('Invalid selected-scenario version')
        if (type(route.get('simulation_second')) is not int or route['route_version']!=route['simulation_second']+1
                or route['policy_sha256']!=context.model.navigation.policy_hash):
            raise ProtocolError('Selected policy/clock differs')
        delivery=status['delivery'];count=delivery['ack_count']
        if (type(count) is not int or not 0<=count<=3 or delivery['state'] not in {'ALL_ACKED','INCOMPLETE'}
                or (delivery['state']=='ALL_ACKED')!=(count==3)):
            raise ProtocolError('Selected delivery state/count differs')
        age = (datetime.now(timezone.utc)-utc_time(status['observed_at_utc'])).total_seconds()
        result.update(route_version=route['route_version'],simulation_second=route['simulation_second'],
            observed_at_utc=status['observed_at_utc'],model_provenance='ASSUMED_NOT_CALIBRATED',
            guidance_status=route['status'],delivery={**status['delivery'],'expected_ack_count':3},
            fixed_origin=route['source_node'])
        if route['status']=='ROUTE_AVAILABLE':
            path = route['path']
            if not path or path[0]!=route['source_node'] or path[-1]!=route['target_exit'] or route['target_exit'] not in context.model.navigation.exits:
                raise ProtocolError('Invalid selected approved route')
            if any(b not in context.model.navigation.adjacency.get(a,{}) for a,b in zip(path,path[1:])):
                raise ProtocolError('Approved route contains an unknown edge')
            if any(n in route['blocked_nodes'] for n in path):
                raise ProtocolError('Approved route crosses a declared blocked node')
            result['route'] = {k:route[k] for k in ['source_node','target_exit','path','routing_cost','cost_unit']}
        elif route['status']!='NO_VERIFIED_ROUTE':
            raise ProtocolError('Invalid selected guidance state')
        # An unrelated/malformed latest telemetry CIN must not prevent expiry of
        # the last committed observation or expose old people as current.
        fresh=-self.settings.max_clock_skew<=age<self.settings.sensor_ttl
        if diagnostics and fresh:
            detail = latest(client,context.resources,'controller/diagnostics')
            occupancy = latest(client,context.resources,'occupancy/observations')
            if any(detail.get(k)!=route.get(k) or occupancy.get(k)!=route.get(k) for k in keys):
                return {**result,'guidance_status':'PENDING','route':None}
            frame = latest(client,context.resources,'sensor/telemetry')
            ScenarioFrame.parse(frame,context.hash)
            if frame.get('event_id') != route['event_id']:
                return {**result,'guidance_status':'PENDING','route':None}
            population=detail['population']
            if (any(type(population[k]) is not int or population[k]<0 for k in ['total','evacuated','remaining'])
                    or population['total']!=population['evacuated']+population['remaining']
                    or not isinstance(detail['residents'],list) or len(detail['residents'])!=population['total']):
                raise ProtocolError('Selected population conservation differs')
            result.update(population=detail['population'],residents=detail['residents'],
                occupants=occupancy['zones'],hazards={'blocked_nodes':route['blocked_nodes'],'zones':frame['zones']})
        if age >= self.settings.sensor_ttl or age < -self.settings.max_clock_skew:
            result.update(guidance_status='STALE' if age>=0 else 'UNKNOWN',route=None)
            for key in ['population','residents','occupants']:
                result.pop(key,None)
        with self.lock:
            changed = self.busy or context is not self.context or context.model.last['sequence']!=route['route_version']
        if busy or changed:
            return {**unknown_view(incident,status='PENDING',cse='LIVE'),'simulation_second':route['simulation_second']}
        return result

    def snapshot(self,incident,role):
        """Historical CSE read-back; never grant live guidance or advance the model."""
        if role!='responder':raise PermissionError('Responder required')
        with self.lock:context=self.context
        if context is None or context.incident_id!=incident:raise LookupError('Unknown incident')
        client=context.clients['responder']
        status=latest(client,context.resources,'controller/status')
        version=status.get('route_version')
        if type(version) is not int or version<1 or status.get('incident_id')!=incident:
            raise ProtocolError('Invalid committed snapshot version/incident')
        def named(key,name):
            parent=context.resources['containers'][key]['path']
            cin=client.resource(client.retrieve(parent+'/'+name),4)
            data=strict_json(cin['con']) if isinstance(cin['con'],str) else cin['con']
            if not isinstance(data,dict) or type(data.get('schema_version')) is not int or data['schema_version']!=1:
                raise ProtocolError('Invalid historical CIN schema')
            return data
        route=named('controller/routes','route-'+str(version))
        detail=named('controller/diagnostics','moving-'+str(version))
        occupancy=named('occupancy/observations','occupancy-'+str(version))
        frame=named('sensor/telemetry','frame-'+str(version))
        keys=['event_id','incident_id','route_version','simulation_second','policy_sha256']
        if any(data.get(k)!=status.get(k) for data in [route,detail,occupancy] for k in keys):
            raise ProtocolError('Historical snapshot correlation differs')
        if status.get('simulation_second')!=version-1 or status.get('policy_sha256')!=context.model.navigation.policy_hash:
            raise ProtocolError('Historical clock/policy differs')
        ScenarioFrame.parse(frame,context.hash)
        if any(frame.get(k)!=status.get(k) for k in ['event_id','incident_id','simulation_second']) or frame['sequence']!=version:
            raise ProtocolError('Historical source frame differs')
        pop=detail['population'];people=detail['residents']
        if (any(type(pop[k]) is not int or pop[k]<0 for k in ['total','remaining','evacuated'])
                or pop['total']!=pop['remaining']+pop['evacuated'] or not isinstance(people,list) or len(people)!=pop['total']):
            raise ProtocolError('Historical population differs')
        ids=[person['resident_id'] for person in people]
        if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):
            raise ProtocolError('Historical resident IDs differ')
        stamp=status['observed_at_utc'];utc_time(stamp)
        with self.lock:
            if context is not self.context:raise LookupError('Incident changed during snapshot read')
        return {'schema_version':1,'snapshot_kind':'HISTORICAL','is_live':False,'guidance_status':'HISTORICAL',
            'route':None,'incident_id':incident,'event_id':status['event_id'],'route_version':version,
            'simulation_second':version-1,'policy_sha256':status['policy_sha256'],
            'snapshot_at_utc':stamp,'read_at_utc':datetime.now(timezone.utc).isoformat(),
            'data_origin':'SYNTHETIC','model_provenance':'ASSUMED_NOT_CALIBRATED',
            'population':pop,'residents':people,'occupants':occupancy['zones'],
            'hazards':{'blocked_nodes':route['blocked_nodes'],'zones':frame['zones']}}

    def reset(self):
        with self.lock:old,self.context = self.context,None
        if old:old.close()
        context = ScenarioSession(self.settings)
        try:
            context.start()
            context.evidence.write('replay-source.json',{'kind':'SYNTHETIC_SOURCE_REPLAY',
                'trace_sha256':trace_digest(self.trace),'source_bundle_sha256':context.hash})
            metadata = json.loads((context.evidence.path/'meta.json').read_text())
            metadata['test'] = 'P1_SELECTED_APP'
            context.evidence.write('meta.json',metadata)
            trace = iter(read_trace(self.trace,context.hash))
            context.accept_raw(next(trace));context.publish()
        except Exception:
            context.close();raise
        with self.lock:
            self.context,self.iterator,self.paused = context,trace,True
            self.pending_raw=None;self.remaining=self.advanced=0
            self.recovery_deadline=None;self.resume_after_recovery=False

    def advance(self,count=1,*,recovering=False):
        """Preserve the source cursor/batch across bounded input transport loss."""
        if recovering:
            if self.recovery_deadline is None or time.monotonic()>=self.recovery_deadline:
                raise ProtocolError('Source recovery deadline exceeded; explicit reset required')
        else:
            if self.pending_raw is not None or self.remaining:
                raise ProtocolError('Pending source input requires recovery or explicit reset')
            self.remaining=count;self.advanced=0
            self.resume_after_recovery=not self.paused
        exhausted=False
        while self.remaining and not self.stop.is_set():
            if self.pending_raw is None:self.pending_raw=next(self.iterator,None)
            if self.pending_raw is None:
                self.remaining=0;exhausted=True;break
            try:self.context.accept_raw(self.pending_raw)
            except TransportError:
                if self.recovery_deadline is None:
                    # Reuse the configured bounded CSE readiness budget; every
                    # attempt also retains its configured request deadline.
                    self.recovery_deadline=time.monotonic()+self.settings.startup_timeout
                    self.audit_evidence.event(hop='source_recovery_started',incident_id=self.context.incident_id,
                        simulation_second=self.pending_raw['simulation_second'],budget_seconds=self.settings.startup_timeout)
                self.paused=True
                return 'RECOVERING'
            self.pending_raw=None;self.remaining-=1;self.advanced+=1
        if self.stop.is_set():return 'STOPPING'
        # Dispatch/partial fan-out errors deliberately remain fail-closed. They
        # require a separate outbox protocol, not a blind retry of publish().
        if self.advanced:self.context.publish()
        if self.recovery_deadline is not None:
            self.audit_evidence.event(hop='source_recovery_completed',incident_id=self.context.incident_id,
                route_version=self.context.model.last['sequence'])
        self.paused=exhausted or not self.resume_after_recovery
        self.recovery_deadline=None;self.advanced=0
        return 'TRACE_EXHAUSTED' if exhausted else 'READY'

    def run(self,initial_action='reset'):
        action = initial_action
        while not self.stop.is_set():
            try:
                state='READY'
                if action=='reset':self.reset()
                elif action=='pause':self.paused=True
                elif action=='resume':self.paused=False
                elif action=='recover_pending':state=self.advance(recovering=True)
                else:
                    if action=='stop_display_c' and self.context.delivery.processes['display-c'].is_alive():
                        self.context.delivery.stop_device('display-c')
                    state=self.advance(30 if action=='advance_30' else 1)
                with self.lock:self.state=state
            except Exception as exc:
                self.paused = True
                self.audit_evidence.event(hop='selected_action_failed',action=action,error=type(exc).__name__)
                with self.lock:self.state='DEGRADED'
            finally:
                with self.lock:self.busy=False
            deadline = time.monotonic()+.5
            while not self.stop.is_set():
                try:action=self.jobs.get(timeout=.1);break
                except queue.Empty:
                    if (self.state=='RECOVERING' or not self.paused) and time.monotonic()>=deadline:
                        action='recover_pending' if self.state=='RECOVERING' else 'step'
                        with self.lock:self.busy=True
                        break
        if self.context:self.context.close()


def main():
    if sys.version_info<(3,11):
        print(json.dumps({'state':'BLOCKED','reason':'Use the Python 3.12 scenario-app profile'}));return 2
    settings = Settings.load()
    app = ScenarioApp(settings,Path(os.getenv('SCENARIO_TRACE','.runtime/fire-evac-trace.jsonl')))
    auth = AuthStore.load(Path(os.getenv('APP_AUTH_FILE','.runtime/auth.json')))
    origin = os.getenv('APP_PUBLIC_ORIGIN','http://127.0.0.1:3001')
    static_root = ROOT/'frontend/scenario'
    if not static_root.is_dir():static_root=Path(sys.prefix)/'share/fire-route/frontend/scenario'
    from backend.tls import server_context
    tls=server_context(origin,os.getenv('APP_TLS_CERT'),os.getenv('APP_TLS_KEY'))
    server = api_server(os.getenv('APP_BIND_HOST','127.0.0.1'),int(os.getenv('APP_PORT','3000')),
        app,auth,origin,static_root,actions={'step','advance_30','pause','resume','reset','stop_display_c'},snapshots=True,tls_context=tls)
    worker = Thread(target=app.run,daemon=True);worker.start()
    def stop(signum,frame):
        app.stop.set();Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    print(json.dumps({'state':'APP_LISTENING','origin':origin,'profile':'fire_evac_10f_v1','MEC':'UNAVAILABLE'}),flush=True)
    try:server.serve_forever()
    finally:app.stop.set();server.server_close();worker.join(timeout=25)
    return 0


if __name__=='__main__':raise SystemExit(main())
