# Camera-to-Cloud ingest protocol

This gateway accepts short, independently decodable MP4 or fragmented MP4 chunks. A camera vendor integration normally runs in a trusted camera companion app or an on-set edge gateway; do not embed the long-lived device secret in browser code.

## Control plane

All control-plane calls require `X-Ingest-Management-Token` until the application user-auth dependency replaces it.

1. `POST /api/v1/camera-ingest/devices` registers a hardware identifier for a project. The returned `device_secret` is displayed exactly once.
2. `POST /api/v1/camera-ingest/devices/{device_id}/sessions` opens a capture session and returns the chunk URL template and timeline ID.
3. `POST /api/v1/camera-ingest/sessions/{session_id}/complete` closes the recording after the final chunk was queued.

## Data plane

Send each segment with `PUT /api/v1/camera-ingest/sessions/{session_id}/chunks/{sequence_number}` over HTTPS. The body is the raw video bytes; do not use multipart form data.

Required headers:

- `X-Device-Id`: registered UUID
- `X-Ingest-Timestamp`: current Unix epoch seconds
- `X-Ingest-Nonce`: fresh cryptographically random value (at least 16 characters)
- `X-Chunk-SHA256`: lowercase SHA-256 digest of the exact body
- `X-Chunk-Signature`: lowercase HMAC-SHA256 hex signature
- `X-Camera-Metadata`: optional URL-safe base64 encoded JSON matching `CameraMetadata`

The signed UTF-8 message is exactly:

```text
POST\n
{request_path}\n
{session_id}\n
{sequence_number}\n
{timestamp}\n
{nonce}\n
{chunk_sha256}\n
{sha256_of_canonical_metadata_json}
```

`canonical_metadata_json` is JSON serialized with sorted keys, compact separators, and null fields removed. The service checks TLS, a five-minute clock window, one-time Redis nonce consumption, the HMAC, and then rehashes the streamed body before accepting it.

## Timeline and search behavior

The Worker probes each accepted chunk, emits a 720p H.264/AAC proxy, and publishes `extra.ingest.kind = "growing_timeline"` through the existing project SSE/WebSocket stream. The payload always contains the complete ordered live clip list so late or out-of-order chunk arrivals correct prior positions.

Exact camera metadata remains on the chunk and generated `MediaAsset`. A text representation of camera model, lens, timecode, focal length, exposure, and rounded GPS is encoded as a `camera_metadata` `MediaEmbeddingSegment`, enabling immediate pgvector semantic search.

## Production edge requirements

Terminate TLS at Nginx/load balancer and preserve `X-Forwarded-Proto`; keep `INGEST_REQUIRE_TLS=true`. Store `INGEST_DEVICE_ENCRYPTION_KEY` and `INGEST_MANAGEMENT_TOKEN` in a secret manager. Configure each device to generate a new nonce when retrying a request; reusing a consumed nonce is deliberately rejected as a replay.
