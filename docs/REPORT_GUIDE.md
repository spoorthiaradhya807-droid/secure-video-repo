# Report + screenshot guide (5–8 pages)

## Screenshot checklist → deliverable
| # | Screenshot | Deliverable |
|---|---|---|
| 1 | Upload page, progress bar, "Upload complete and verified" (as instructor) | Video upload |
| 2 | S3 console showing the object under `videos/` with Encryption = SSE-S3 | Video upload |
| 3 | Watch page playing, countdown badge visible | Video streaming |
| 4 | Browser address bar / DevTools Network showing the `X-Amz-Signature` + `X-Amz-Expires=300` URL | Pre-signed URL demo |
| 5 | Same URL pasted later → `Request has expired` XML | Pre-signed URL demo |
| 6 | Anonymous open of plain object URL → AccessDenied | Access control |
| 7 | S3 → Permissions: Block Public Access (4× On), bucket policy, ownership = bucket owner enforced | Access control |
| 8 | IAM → Roles: InstructorRole + ViewerRole permissions tabs and trust policy | IAM roles |
| 9 | Viewer visiting `/upload` → 403 page; instructor audit log showing the `denied` row | Access control |
| 10 | Analytics dashboard with data (generate some views first) | Analytics |
| 11 | Terminal: `pytest` (15 passed) and `live_access_test.py` output | Testing |
| 12 | GitHub repo page | Source code |
| 13 | Upload page mid-upload of a large file (progress, speed, chunks) | Video upload |
| 14 | Login page, forgot-password page, reset e-mail (or terminal link) and reset page | Extra feature |

## Report outline
1. Introduction & objectives (0.5 p)
2. Architecture + diagram, service choices (1 p)
3. Implementation: S3 config, EC2 deploy, app modules, upload/stream flows (1.5 p)
4. Security design: IAM roles + policies (paste JSON), bucket policy, STS temporary creds, pre-signed URL/POST, anonymous blocking, defence in depth table (1.5 p)
5. Testing: paste `docs/test-report.md` table + app tests, discuss results (1 p)
6. Analytics & audit logging (0.5 p)
7. Challenges, limitations (e.g. SQLite, no MFA, CloudFront not used) and future work: CloudFront + OAC + signed cookies, Cognito/MFA, GuardDuty, KMS keys, HLS transcoding (0.5 p)
8. Conclusion + references (0.5 p)

## Demo script (for viva)
1. Show bucket → Block Public Access on. 2. Anonymous URL → 403. 3. Log in as viewer → play video → show URL. 4. Viewer opens `/upload` → 403. 5. Log in as instructor → upload → analytics/audit show the denied attempt. 6. Wait 5 min / show expired URL.
