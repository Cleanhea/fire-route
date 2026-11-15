# Certificate-bound CSE security profile

This opt-in profile places an NGINX mTLS gateway in front of the actual ACME CSE. It is not a replacement CSE. The existing demo and its volumes remain separate. Implementation and crypto/unit tests do not establish an actual gateway/CSE PASS; use the executed result in STATUS.md.

## Trust and request flow

Client AE → TLS 1.2+ with client certificate → NGINX → private ACME HTTP binding.

The local CA signs a separate certificate whose CN is the actual CSE-returned AE-ID. NGINX validates the certificate, extracts that identity and rejects a conflicting X-M2M-Origin. It forwards the verified identity to the real CSE, which still executes AE/CNT/CIN/SUB and enforces ACPs. The gateway never generates successful oneM2M resources or RSC values. HTTP authentication denials at NGINX and real CSE HTTP 403/RSC 4103 denials are separate proof.

Bootstrap CAdmin and the reserved C registration identity have separate privileged certificates. Only the enrollment runner holds the CA private key; gateway/CSE/device/browser containers must not receive it. for_actor selects a new credential transport for the actual returned identity; a missing transport fails without falling back to admin. The CA is local and short-lived, with no OS trust-store change.

Only gateway and CSE join the private backend network. Ordinary test clients join the separate client network and cannot directly resolve/reach the CSE service through their declared network. No security-stack service is published on the host. This protects against application clients in that deployment; host/Docker administrators remain trusted. CA issuance/revocation infrastructure, hardware key storage and application-process isolation are not production PKI claims.

## Execute serially

~~~bash
make p1-cse-security
# Inspect the printed LOCAL_CERTIFICATE_BOUND_CSE evidence and assertions.
make cse-security-down
~~~

The command creates private .runtime/cse-mtls files and builds an isolated fire-route-security project with its own named volumes. Existing/partial/unsafe credentials are validated or rejected, never silently replaced. Cleanup preserves volumes and private evidence. OpenSSL is needed for local certificate generation/enrollment. The Python base image must also provide its OpenSSL CLI; missing tools are a failed prerequisite, not a passing crypto test.

The privileged test runner provisions eight actual AEs and their ACPs, issues certificates for their actual AE-IDs, creates a sensor CIN through mTLS, reads it as controller and receives real SUB verification/event callbacks. It verifies real resident ACP denial, sensor attempts to claim admin/controller identities, missing/untrusted client certificates and an untrusted server CA. Negative requests carry valid oneM2M headers so malformed-request rejection cannot masquerade as authentication proof. No arbitrary fixture is accepted by this actual gate.

## Explicit boundaries

- Client-to-gateway is mTLS; gateway-to-CSE and the private callback relay remain HTTP. This is not end-to-end encrypted callbacks or native ACME certificate authentication.
- The main selected demo, MQTT broker and device processes are not silently migrated by this isolated gate. Existing real Group/ACK evidence stays on its original profile; rerun the affected full scenario before claiming secure Group/device operation.
- This does not implement MQTT TLS/authentication, Mcc registrar security, application crash recovery or MEC deployment.
- NGINX stable 1.30.6 is selected from its official release listing; nginx:1.30.6-alpine image availability/digest and installed behavior still require a successful real build/run.

Sources: [NGINX client-certificate verification and subject variables](https://nginx.org/en/docs/http/ngx_http_ssl_module.html), [official NGINX releases](https://nginx.org/en/download.html), [ACME authentication capabilities](https://acmecse.net/setup/Certificates/). ACME Basic/Bearer support alone must not be represented as binding an arbitrary X-M2M-Origin to the authenticated actor.
