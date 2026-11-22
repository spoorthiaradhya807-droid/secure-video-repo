# SecureStream – Secure Video Streaming Repository

Educational video hosting on **AWS** (EC2 + private S3) with role-based access, temporary
credentials and pre-signed URLs. Assignment 8 – Object storage security.

## Features
| Area | What it does |
|---|---|
| Accounts | Sign up (always **Viewer**), log in, **forgot / reset password** by e-mail (Amazon SES), strength meter, rate limiting. New instructors come in two ways: a teacher signs up and sends a **Become an instructor** request that an existing instructor approves on the **Team** page, or an instructor invites a colleague by e-mail (single-use, 48-hour link). The admin can also use the CLI. |
| Upload | Instructors only. **Any size up to a limit you set (default 10 GB; S3 itself allows 5 TB).** The browser sends the file straight to S3 in 8 MB chunks (3 in parallel, automatic retries, progress/speed/ETA, cancel). The server then verifies size, encryption and real MP4/WebM header bytes. |
| Delete | An instructor can delete the videos they uploaded (**My videos** page, or the button on the video page). Removes the file from S3 and the library; the action is audit-logged. Bucket versioning keeps a hidden old copy for 30 days. |
| Stream | Any logged-in user. Videos play from S3 via a **5-minute pre-signed GET URL**; the player auto-renews it if the user is still authorised. |
| Library UI | Searchable library, thumbnails, durations, "how this stream is secured" panel with live link countdown and a **Show signed URL** button (handy for screenshots). |
| Analytics | Instructors only: totals, views per video, 14-day activity, uploads per instructor, top viewers, denied requests, failed logins + filterable audit log. |

## Security design
| Requirement | Implementation |
|---|---|
| Restrict uploads to instructors | (1) `@role_required("instructor")` on every upload route, (2) chunk URLs are signed with the **Instructor IAM role**, (3) bucket policy **denies `s3:PutObject` for everyone except that role**. |
| Viewer role | `VideoRepo-ViewerRole`: only `s3:GetObject` on `videos/*`. Viewer URLs are signed with this role, so a Viewer can never upload even with a forged request. |
| Pre-signed URLs | STS `AssumeRole` per user (session name = username, visible in CloudTrail) → short-lived creds → URLs valid 300 s. |
| Block anonymous access | S3 Block Public Access (all 4) + no ACLs (BucketOwnerEnforced) + every object access needs a signature. |
| Encryption | Bucket default encryption SSE-S3; bucket policy refuses any other algorithm; server refuses to publish unencrypted objects. TLS-only policy. |
| Account security | Password hashing, CSRF tokens, login/reset rate limits, single-use 30-minute reset links, no user enumeration, reset links built from `APP_BASE_URL` (not the Host header), CSP + security headers, audit log. |

Diagram: [docs/architecture.md](docs/architecture.md)

### How a new teacher gets an account
**Option 1: the teacher asks (self-service).**
1. The teacher signs up on the website (they start as a Viewer) and clicks **Become an instructor**, optionally adding a short note.
2. Every instructor sees a red counter on **Team**. They open it, read the request (username, e-mail, note) and press **Approve** or **Reject**.
3. After approval the teacher refreshes and gets Upload, My videos and the other instructor pages.

**Option 2: an instructor invites a colleague.** On **Team**, enter the colleague's e-mail. They receive a one-time link (e-mail with SES, or copy-paste it in dev mode), choose a username and password and become an Instructor directly.

Both routes are audit-logged (`instructor_requested`, `instructor_approved`, `instructor_rejected`, `instructor_invited`, `instructor_joined`). Approval can only be done by an existing instructor, and nobody can approve their own request.

## Quick start (local, VS Code)
```bash
python -m venv venv && venv\Scripts\activate            # macOS/Linux: source venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env                                    # fill in values (see section A)
python -m flask --app wsgi create-user teacher1 --role instructor --email you@example.com
python -m flask --app wsgi create-user student1 --role viewer --email you2@example.com
python wsgi.py                                          # http://localhost:5000
python -m pytest                                        # 44 app-level tests (S3 mocked)
```
Existing users without an e-mail (created before password reset existed): 
`python -m flask --app wsgi set-email teacher1 you@example.com`.
Admin password reset without e-mail: `python -m flask --app wsgi reset-password teacher1`.
The database upgrades itself on start-up; nothing is lost.

## A. Create / update the AWS resources (CloudFormation)
```bash
aws cloudformation deploy --stack-name videorepo --template-file infra/storage-and-iam.yaml \
  --capabilities CAPABILITY_NAMED_IAM --region ap-south-1 \
  --parameter-overrides BucketName=<unique-bucket-name> "AllowedOrigins=http://localhost:5000,http://127.0.0.1:5000"
aws cloudformation describe-stacks --stack-name videorepo --query "Stacks[0].Outputs" --output table
```
Copy the outputs into `.env`. The same policies are in `policies/` for the report. When you deploy to EC2,
run the command again with your `https://...` address added to `AllowedOrigins`.

## B. Forgot-password e-mail (Amazon SES)
* **Dev mode (default):** `SES_SENDER` empty → the reset link is printed in the terminal running the server.
* **Real e-mail:** SES console (region `ap-south-1`) → *Verified identities* → create an **e-mail address** identity,
  click the verification link. New SES accounts are in the *sandbox*: you can only send **to verified addresses**, so verify
  the recipient addresses too (fine for a class project). Then set `SES_SENDER=<verified address>` and, on EC2,
  `APP_BASE_URL=https://<your-site>` in `.env`.

## C. Launch the VM
EC2 → Amazon Linux 2023, t3.micro, **IAM instance profile `VideoRepo-AppInstanceProfile`**,
security group: 443 + 80 from anywhere, **no SSH** (use Session Manager) or 22 from your IP only.
Then: `bash deploy/setup_ec2.sh <your-git-repo-url>` (systemd service + nginx + HTTPS).
Set `BEHIND_PROXY=1`, `COOKIE_SECURE=1`, `APP_BASE_URL` in the VM's `.env`.

## D. Live access tests (generates the test report)
```bash
python tests/live_access_test.py      # writes docs/test-report.md (16 authorised/unauthorised checks incl. a real chunked upload)
```

## E. Push to GitHub
```bash
git init && git add . && git commit -m "Secure video streaming repository"
git branch -M main
git remote add origin https://github.com/<your-username>/secure-video-repo.git
git push -u origin main
```
Never commit `.env`, `*.db` or `.pem` files (already in `.gitignore`).

## Cost control
t3.micro + a few GB of S3 costs very little. **Stop the EC2 instance** when not demoing
and set an AWS Budgets alert. Unfinished chunked uploads are auto-deleted by a 1-day lifecycle rule.
