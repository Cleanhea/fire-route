export function routeFor(view){
  return view?.connections?.cse==='LIVE'&&view.guidance_status==='ROUTE_AVAILABLE'&&Array.isArray(view.route?.path)&&view.route.path.length?view.route.path:[];
}
export function timeLabel(seconds){return Number.isInteger(seconds)?`${Math.floor(seconds/60)}분 ${seconds%60}초`:'—';}
export function selectedResident(view,id){return view?.residents?.find(p=>p.resident_id===id)||view?.residents?.[0]||null;}

function hasPeople(data,incident){
  return Boolean(incident&&data?.incident_id===incident&&Number.isInteger(data.route_version)&&data.route_version>0
    &&Array.isArray(data.residents)&&data.population&&Number.isInteger(data.population.total)
    &&data.residents.length===data.population.total);
}
export function freshPeople(data,incident){
  return hasPeople(data,incident)&&data.is_live!==false&&data.connections?.cse==='LIVE'
    &&['ROUTE_AVAILABLE','NO_VERIFIED_ROUTE'].includes(data.guidance_status);
}
function acceptedRecord(data,incident){
  return freshPeople(data,incident)||(hasPeople(data,incident)&&data.snapshot_kind==='HISTORICAL'&&data.is_live===false);
}
export function rememberPeople(previous,incoming,incident){
  const old=acceptedRecord(previous,incident)?previous:null;
  if(!acceptedRecord(incoming,incident))return old;
  return !old||incoming.route_version>=old.route_version?incoming:old;
}
export function peoplePresentation(live,saved,incident){
  if(freshPeople(live,incident))return {kind:'LIVE',data:live};
  if(acceptedRecord(saved,incident))return {kind:'HISTORICAL',data:saved};
  return {kind:'UNAVAILABLE',data:null};
}
export function residentOptionsKey(data){return (data?.residents||[]).map(p=>p.resident_id).join('\u0000');}
function liveGuidance(view){
  return view?.connections?.cse==='LIVE'&&['ROUTE_AVAILABLE','NO_VERIFIED_ROUTE'].includes(view.guidance_status);
}
// Personal guidance for a resident-chosen room; the choice stays in the browser.
export function zoneRouteFor(view,zone){
  const route=view?.zone_route;
  return zone&&liveGuidance(view)&&route?.source_node===zone&&route.status==='ROUTE_AVAILABLE'
    &&Array.isArray(route.path)&&route.path.length>1?route.path:[];
}
export function zoneSummary(view,zone){
  if(!zone)return null;
  const route=view?.zone_route;
  if(!liveGuidance(view)||route?.source_node!==zone)return {state:'UNAVAILABLE',text:'내 위치 안내를 확인할 수 없습니다. 방향 안내를 따르지 마세요.'};
  if(route.status==='ROUTE_AVAILABLE')return {state:'ROUTE',text:`${zone} → ${route.target_exit} · 같은 프레임의 차단 정보로 컨트롤러가 계산`};
  return {state:'NO_ROUTE',text:`${zone}에서 확인된 경로가 없습니다. 현장 안내를 따르세요.`};
}
export function roomOptions(building){
  const floors=new Map();
  for(const node of building?.nodes||[]){if(node.kind!=='room')continue;if(!floors.has(node.floor))floors.set(node.floor,[]);floors.get(node.floor).push(node.id);}
  const room=id=>Number(id.split('-R')[1]);
  return [...floors.entries()].sort((a,b)=>a[0]-b[0]).map(([floor,ids])=>({floor,rooms:ids.sort((a,b)=>room(a)-room(b))}));
}
const likelihoodLabels={HIGH:'높음',POSSIBLE:'있음',LOW:'낮음'};
const basisLabels={REGISTERED_PRIOR:'등록 인원',PASSAGE:'출입 집계',PRESENCE_SCAN:'스캔만 감지'};
export function estimatePresentation(view){
  const estimate=view?.occupancy_estimate;
  if(!estimate)return {state:'UNAVAILABLE',rows:[],summary:'이 구성에는 MEC 재실 추정 서비스가 연결되어 있지 않습니다.'};
  if(estimate.state!=='ESTIMATED'||!Array.isArray(estimate.priority))return {state:'UNKNOWN',rows:[],summary:'현재 프레임의 MEC 재실 추정을 확인할 수 없습니다. 이전 추정은 표시하지 않습니다.'};
  const rows=estimate.priority.map(item=>{
    const h=item.hazard||{},danger=Math.max(h.fire_index||0,h.smoke_index||0);
    const ev=item.evidence||{};
    return {rank:item.rank,zone:item.zone_id,count:`추정 ${item.estimated_count}명`,likelihood:likelihoodLabels[item.likelihood]||'확인 불가',
      level:item.likelihood,probability:Number.isFinite(item.probability)?`${Math.round(item.probability*100)}%`:'—',
      basis:`${basisLabels[item.basis]||'—'} · 스캔 ${ev.detections??0}/${ev.scans??0}회 감지`,
      last:item.last_detection_second==null?'등록 인원 · 감지 기록 없음':timeLabel(item.last_detection_second),
      hazard:h.blocked||h.collapsed?'차단':danger>0?`위험 지수 ${danger}`:'—'};
  });
  const e=estimate.evaluation||{},count=level=>estimate.priority.filter(p=>p.likelihood===level).length;
  const summary=`추정 잔류 ${estimate.estimated_remaining}명(등록 ${estimate.registered_prior_total} − 출구 통과 감지 ${estimate.detected_exits})`
    +` · 확인 구역 ${rows.length}곳(높음 ${count('HIGH')} · 있음 ${count('POSSIBLE')} · 낮음 ${count('LOW')}) · Wi-Fi/BLE 스캔 ${estimate.presence_scans??0}회 반영`
    +(Number.isInteger(e.truth_zones)?` · 모의 정답 대비: 실제 재실 구역 ${e.truth_zones}곳 중 탐지 ${e.detected_zones} · 미탐 ${e.missed_zones}`
      +` · 실제 재실 비율 — 높음 ${e.high_true_zones}/${e.high_zones} · 있음 ${e.possible_true_zones}/${e.possible_zones} · 낮음 ${e.low_true_zones}/${e.low_zones}`:'');
  return {state:'ESTIMATED',rows,summary};
}
export function mecLabel(view){
  if(view?.connections?.mec==='VERIFIED_RESULT'&&view.connections.cse==='LIVE'
    &&['ROUTE_AVAILABLE','NO_VERIFIED_ROUTE'].includes(view.guidance_status))return 'MEC 경유 계산 확인';
  return view?.connections?.mec==='UNKNOWN'?'MEC 계산 확인 불가':'MEC 미연결';
}
