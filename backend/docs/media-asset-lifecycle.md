# PB-scale media asset lifecycle

## Storage policy

| Data | Policy |
| --- | --- |
| 4K source (`MediaAsset.storage_key`) of inactive completed Pro projects | Scheduler tags it after 30 inactive days; S3 Lifecycle moves only tagged raw objects to `DEEP_ARCHIVE`. |
| Proxy (`proxy_key`), thumbnails, audio derivatives, Timeline JSON | Never receive the `mam-tier=cold` tag; remain hot for editor preview and project recovery. |
| Free raw source | Never enters Deep Archive because the 90-day TTL is shorter than Deep Archive's 180-day minimum storage duration. It is deleted after 90 days without login; Proxy and Timeline remain. |

The S3 Lifecycle rule is deliberately tag-filtered, preserving all unrelated bucket lifecycle rules. The daily `mam.configure_lifecycle` task provisions the `mam-raw-4k-deep-archive` rule only when `MAM_S3_LIFECYCLE_ENABLED=true`.

## Hydration

`POST /api/v1/projects/{project_id}/storage/hydrate` creates a project-level hydration job and requests a temporary three-day restored copy. A 1080p/4K export automatically starts the same flow and returns HTTP 202 until sources are ready. The UI can continue previewing the Proxy while `GET /api/v1/projects/{project_id}/storage/status` reports the job progress. S3's restore header is the source of truth; the displayed 12-hour estimate is intentionally advisory.

## Free-tier retention

Use the real authentication success path to update `User.last_login_at`. The scheduled job sends notices at 60, 75, and 85 inactive days through SendGrid, tracked by `storage_retention_notices` to avoid duplicate deliveries. At 90 days it deletes only `storage_key` original audio/video objects, then marks the asset `purged`; no proxy, thumbnail, timeline, or analysis payload is deleted.

Required production IAM actions: `s3:GetBucketLifecycleConfiguration`, `s3:PutLifecycleConfiguration`, `s3:GetObjectTagging`, `s3:PutObjectTagging`, `s3:HeadObject`, `s3:RestoreObject`, and `s3:DeleteObject`. Do not enable lifecycle writes against the local MinIO bucket.
