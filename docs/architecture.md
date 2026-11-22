# Solution architecture

```mermaid
flowchart LR
  U[Browser<br/>Instructor / Viewer] -- HTTPS 443 --> N[nginx + gunicorn<br/>Flask app on EC2]
  N -- login, RBAC, audit --> DB[(SQLite<br/>users, videos, events)]
  N -- 1. sts:AssumeRole<br/>by user role --> STS[AWS STS]
  STS -. temp credentials 15 min .-> N
  N -- 2. signs URL with role creds --> N
  N -- pre-signed URL / POST form --> U
  U -- GET 300 s URL stream --> S3[(Private S3 bucket<br/>Block Public Access<br/>SSE-S3, versioning)]
  U -- POST form instructors only --> S3
  A[Anonymous user] -. no signature .-> S3
  S3 -. 403 AccessDenied .-> A
  IR[IAM InstructorRole<br/>Put/Get/Delete videos/*] --- STS
  VR[IAM ViewerRole<br/>GetObject videos/* only] --- STS
  EC2R[EC2 instance role<br/>may only AssumeRole] --- N
  BP[Bucket policy:<br/>TLS only, SSE required,<br/>PutObject only by InstructorRole] --- S3
  CT[CloudTrail] -. logs role sessions .-> S3
```

Draw the same picture in draw.io for the report (use the AWS icon set) – this file is the blueprint.

## Request flows
**Stream:** Viewer logs in → `/watch/ID` → app assumes ViewerRole → signs GET URL (300 s) → browser streams from S3 → URL dies after 5 min (the player asks for a fresh one if the user is still authorised).
**Upload (any size, chunked):** Instructor → `/api/upload/init` (RBAC + type/size checks, S3 `CreateMultipartUpload` with SSE-S3) → browser requests one short-lived pre-signed `UploadPart` URL per 8 MB chunk and PUTs it directly to S3 (3 in parallel, retries) → `/api/upload/complete` assembles the parts and verifies size, encryption and real MP4/WebM bytes → video becomes visible. Cancel/failure → `AbortMultipartUpload`.
**Forgot password:** user enters e-mail → signed single-use token (30 min) → link sent through Amazon SES (EC2 role has `ses:SendEmail`) → new password stored hashed; the old link stops working.
**Attack paths blocked:** anonymous object URL (403), Viewer forged upload (403 from IAM and bucket policy), tampered/expired URL (403), plain-HTTP access (403), wrong encryption algorithm (403), `.exe` renamed to `.mp4` (rejected by content check), declared size ≠ uploaded size (rejected).
