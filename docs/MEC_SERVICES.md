# Local FireRoute MEC service and project IPE

This increment implements native app/service registration, an independent risk provider, project-specific oneM2M/MEC interworking and an optional authenticated dashboard. Actual Docker execution is still pending; implementation/unit results do not inherit the earlier seven-check component PASS. The upstream meep-ipe, MEC033/046, platform-controlled VM placement and Option C are not claimed.

~~~bash
cd /home/lilkid/fire-evac/fire-route
# Native registration/discovery, natural hazards through actual CSE/IPE/MEC/
# Group/independent device ACKs, then role/movement/expiry HTTP dashboard checks.
make p1-mec-services

# Normal interactive use: http://127.0.0.1:3002/
make demo-mec-up
# Read the existing role codes privately from .runtime/access-codes.json.
# Press 초기화 if the expiry test left the app paused with stale observations.

make mec-services-logs
make mec-services-down
~~~

The separate fire-route-mec-services project retains its named volumes. Only loopback MEC API 8091 (MEC_SERVICES_PORT) and dashboard 3002 (MEC_DASHBOARD_PORT) are published. Redis/CSE/CouchDB/InfluxDB/provider/IPE remain private. The previous component profile on 8090 and default dashboard on 3001 remain separate.

The official Sandbox controller allocates the real app ID; App Enablement confirms READY and allocates the service ID. Registration saves those actual assignments and verifies native read-back. An uncertain CREATE is recorded before submission and reconciled through real GET results; absence/ambiguity blocks instead of replaying POST. No app/service/scene record is manually seeded into Redis. The native controller accepts nodeName mep1 as an external-app label; this does not demonstrate an instantiated host, VM or network-emulation topology.

Provider POST /v1/frames authenticates a private access key, validates the existing ScenarioFrame and computes the unchanged evacuation model. A private SQLite journal commits each frame/result atomically and reconstructs model state on restart. Exact current-frame duplicates do not move people twice; conflicting, skipped, old or expired frames fail. Unit coverage verifies reconstruction; actual crash recovery remains untested. The cache retains 16 incidents and reconstructs older ones. A 20,000-frame PoC journal budget fails closed without deleting data; this single-provider profile is not a multi-replica database design.

The project IPE maps actual request CIN/SUB → discovered ACTIVE provider HTTP → correlated result CIN/SUB. Requests cannot select arbitrary URLs or commands. Result fields bind request/source IDs, incident, version, policy hash, full-model hash, app/service identity and expiry. Result resources omit per-person records. Existing responder-only diagnostics keep their role restrictions. Producer/IPE/reader ACPs deny cross-role writes. The persistent output outbox reconciles a named CIN after an uncertain CREATE and never blindly replays it. A result expiring before publication becomes UNKNOWN with no route. Consumers verify source freshness and expiry again.

Consumer observer subscriptions carry unique ownership and one-hour expiry. Normal close verifies the exact SUB ID/callback/owner, deletes that SUB once and confirms absence before removing its listener. This prevents repeated resets/tests from leaving active callbacks to removed containers. An unconfirmed cleanup is recorded and cannot turn the gate green.

The optional dashboard has no provider key or direct provider-network connection. It submits through actual CSE resources, requires a fresh matching MEC result, compares the full-model hash and publishes the verified decision through the existing Group/device path. Local model parity supports verification and private presentation; it is not a fallback when MEC fails. An incomplete MEC operation degrades guidance and requires explicit reset. Paused observations still expire after 30 wall-clock seconds. MEC 경유 계산 확인 appears only for a current verified result. Existing default-mode behavior and the supplied fire engine stay unchanged.

Native controller initialization requires CouchDB/InfluxDB. [Apache image documentation](https://github.com/apache/couchdb-docker/blob/main/README.md) confirms couchdb:3.5.2.1/admin environment configuration; the [official InfluxDB 1.8.10 image](https://hub.docker.com/layers/library/influxdb/1.8.10/images/sha256-93ee9cd349882c18e69980d85fe4d2d0667c9dcbdd611c019bd1f7816884aa81) matches the upstream v1 HTTP client. Reviewed pins are not installed-version/pull proof. The only controller build-copy patch replaces two hardcoded Couch auth literals with environment reads; exact-context tests reject unexpected upstream changes. The acquired Git tree and prior component stage stay unchanged. Preparation creates separate private mode-0600 .runtime/mec-couch.env and mec-risk.env without printing values. No keys enter Git or the build context. Private HTTP/development CSE header identities remain explicit limitations.

The actual gate must verify native registration/nonempty discovery, request/result read-back and both SUB paths, four natural hazard stages, model parity, Group/fopt/three independent display ACKs, duplicate handling, expired-input UNKNOWN and real ACP denials. It then checks authenticated roles, moving residents, private access and expiry on the MEC dashboard. Browser rendering, official-IPE/VM/Hosted support are separate results. See [STATUS](STATUS.md), [TEST_PLAN](TEST_PLAN.md) and [LOCAL_MEC](LOCAL_MEC.md).
