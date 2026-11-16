"""Consumer of real project-IPE request/result CINs and verified SUB callbacks."""
from datetime import datetime, timedelta, timezone
import json
from threading import Thread
import time
from uuid import uuid4

from backend.notifications import NotificationService, NotificationStore, notification_server
from backend.p0 import probe, subscription_refs
from mec.interworking.worker import Message, digest
from onem2m.client import CSEError, ProtocolError, strict_json
from simulator.commands import utc_time


class VerifiedRemoteSnapshot:
    def __init__(self,local,result):self.local,self.result=local,result
    def __getattr__(self,name):return getattr(self.local,name)
    def snapshot(self):return self.result


def decode_result(data):
    if not isinstance(data,dict) or data.get('kind')!='FireRouteRiskResponse' or not isinstance(data.get('event_id'),str):
        raise ValueError('Invalid project IPE response')
    return Message(data['event_id'],data)


def verified_result(reply,frame,local):
    now=datetime.now(timezone.utc)
    stamp=utc_time(frame['timestamp_utc'])
    fields={'event_id','incident_id','route_version','simulation_second','timestamp_utc','policy_sha256','fixed_route','population'}
    if (reply.get('state')!='ACCEPTED' or reply.get('source_event_id')!=frame['event_id']
            or reply.get('incident_id')!=frame['incident_id'] or reply.get('sequence')!=frame['sequence']
            or reply.get('result_sha256')!=digest(local)
            or not isinstance(reply.get('analysis'),dict)
            or set(reply['analysis'])!=fields
            or any(reply['analysis'].get(k)!=local[k] for k in reply['analysis'])
            or reply.get('route')!=local['fixed_route']
            or reply.get('guidance_status')!=local['fixed_route']['status']
            or not -5<=(now-stamp).total_seconds()<30
            or not now<utc_time(reply['valid_until_utc'])<=stamp+timedelta(seconds=30)):
        raise ProtocolError('No fresh correlated MEC risk result; guidance unavailable')
    return {**local,**reply['analysis']}


class Requester:
    def __init__(self,admin,connection,evidence,callback,port):
        self.admin,self.connection,self.evidence=admin,connection,evidence
        self.callback,self.port=callback,port;self.server=self.thread=None;self.subscription=None

    def start(self):
        cb=probe(self.admin,'fire-route-dev')
        self.producer=self.admin.for_actor(self.connection['actors']['producer']['ae_id'],'mec-request-producer')
        self.reader=self.admin.for_actor(self.connection['actors']['reader']['ae_id'],'mec-result-reader')
        self.requests=self.connection['containers']['requests']['path']
        self.results=self.connection['containers']['results']['path']
        self.inbox=NotificationStore(self.evidence.path/'mec-results.sqlite3')
        service=NotificationService(self.inbox,cb['csi'],'4',frozenset(),self.evidence,decoder=decode_result)
        self.server=notification_server('0.0.0.0',self.port,service)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        name='observer-'+uuid4().hex[:12]
        owner='owner:'+uuid4().hex
        expiry=(datetime.now(timezone.utc)+timedelta(hours=1)).strftime('%Y%m%dT%H%M%S')
        sub=self.reader.ensure(self.results,23,{'rn':name,'nu':[self.callback],'enc':{'net':[3]},'nct':1,
                                             'lbl':['fire-route:ipe-observer',owner],'et':expiry})
        self.subscription={'path':self.results+'/'+name,'ri':sub['ri'],'owner':owner,'callback':self.callback}
        refs=subscription_refs(cb['csi'],self.results+'/'+name,sub)
        if not refs & self.inbox.verified_refs():raise ProtocolError('Actual MEC result SUB verification missing')
        service.approve(refs)

    def submit(self,frame):
        request_id='req-'+uuid4().hex
        payload={'schema_version':1,'kind':'FireRouteRiskRequest','event_id':request_id,'frame':frame}
        made=self.producer.resource(self.producer.create(self.requests,4,{'rn':request_id,'cnf':'application/json:0',
            'con':json.dumps(payload,separators=(',',':'))}),4)
        readback=self.producer.resource(self.producer.retrieve(self.requests+'/'+request_id),4)
        if made['ri']!=readback['ri'] or strict_json(readback['con'])!=payload:raise ProtocolError('MEC request CIN mismatch')
        deadline=time.monotonic()+20
        while not self.inbox.find(request_id) and time.monotonic()<deadline:time.sleep(.02)
        rows=self.inbox.find(request_id)
        if len(rows)!=1:raise ProtocolError('Matching real MEC result SUB missing')
        actual=self.reader.resource(self.reader.retrieve(self.results+'/result-'+request_id),4)
        result=strict_json(actual['con'])
        if rows[0]['cin_ri']!=actual['ri'] or rows[0]['event']!=result:raise ProtocolError('MEC result CIN/SUB mismatch')
        if result.get('event_id')!=request_id:raise ProtocolError('MEC request/result correlation differs')
        if any(result.get(k)!=frame[v] for k,v in [('source_event_id','event_id'),('incident_id','incident_id'),('sequence','sequence')]):
            raise ProtocolError('MEC result/source identity differs')
        self.evidence.append('mec-consumption.jsonl',{'request_id':request_id,'request_cin':made['ri'],
            'result_cin':actual['ri'],'source_event_id':frame['event_id'],'result':result})
        return result

    def close(self):
        confirmed=True
        if self.subscription:
            try:remove_observer(self.admin,self.subscription)
            except CSEError as exc:
                confirmed=False
                self.evidence.append('mec-observer-cleanup.jsonl',{'state':'UNCONFIRMED','reason':type(exc).__name__})
        if self.server:
            if self.thread and self.thread.is_alive():self.server.shutdown()
            self.server.server_close()
        if self.thread:self.thread.join(timeout=3)
        return confirmed


def remove_observer(admin, reference):
    """Delete only the exact SUB this requester created and has read back."""
    path=reference['path']
    def absent(exc):return not isinstance(exc,ProtocolError) and exc.http_status==404 and exc.rsc==4004
    try:actual=admin.resource(admin.retrieve(path),23)
    except CSEError as exc:
        if absent(exc):return
        raise
    if (actual['ri']!=reference['ri'] or actual.get('nu')!=[reference['callback']]
            or reference['owner'] not in actual.get('lbl',[]) or not path.rsplit('/',1)[-1].startswith('observer-')):
        raise ProtocolError('Refusing cleanup of an unowned subscription')
    admin.delete(path)
    try:admin.retrieve(path)
    except CSEError as exc:
        if absent(exc):return
        raise
    raise ProtocolError('Observer deletion not confirmed by actual read-back')
