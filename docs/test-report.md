# Live access test report

Bucket: `securevideo-spoorthi-2026` | Region: `ap-south-1` | Run: 2026-11-22 10:49 UTC

| ID | Test | Expected | Actual | Result |
|---|---|---|---|---|
| T1 | Anonymous GET of object URL | 403 AccessDenied | 403 AccessDenied | PASS |
| T2 | Anonymous bucket listing | 403 AccessDenied | 403 AccessDenied | PASS |
| T3 | Viewer role direct PutObject | AccessDenied | AccessDenied | PASS |
| T4 | Viewer role ListBucket | AccessDenied | AccessDenied | PASS |
| T5 | Viewer-signed pre-signed PUT URL | 403 AccessDenied | 403 AccessDenied | PASS |
| T6 | Upload without SSE header is still encrypted at rest (bucket default encryption) | AES256 | AES256 | PASS |
| T6b | Upload requesting a different algorithm (aws:kms) is refused | AccessDenied | AccessDenied | PASS |
| T7 | Viewer pre-signed GET URL | 200 OK | 200 | PASS |
| T8 | Range request (needed for video seeking) | 206 Partial Content | 206 | PASS |
| T9 | Tampered signature | 403 SignatureDoesNotMatch | 403 SignatureDoesNotMatch | PASS |
| T10 | Same signature reused for a different object | 403 | 403 SignatureDoesNotMatch | PASS |
| T11 | Plain HTTP (non-TLS) request with valid URL | 403 (bucket policy) | 403 AccessDenied | PASS |
| T12 | Pre-signed URL (3 s) before and after expiry | 200 then 403 expired | 200 then 403 AccessDenied | PASS |
| T13 | Instructor chunked upload via pre-signed part URL | 200, object encrypted | part 200, AES256 | PASS |
| T14 | Viewer cannot start a chunked upload | AccessDenied | AccessDenied | PASS |
| T15 | Viewer-signed chunk URL for an instructor's upload | 403 AccessDenied | 403 AccessDenied | PASS |

**16/16 passed.**
