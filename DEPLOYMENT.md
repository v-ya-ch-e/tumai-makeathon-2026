# Deployment Guide: AWS EC2 with Docker

Step-by-step guide to deploy the FastAPI backend on an AWS EC2 instance using Docker and Docker Compose.

## Prerequisites

1. **AWS Account** with permissions to create EC2 instances
2. **EC2 Key Pair** (`.pem` file) for SSH access -- create one in the AWS Console under EC2 > Key Pairs
3. **Git** installed on your local machine

## Step 1: Launch an EC2 Instance

1. Log in to the [AWS Management Console](https://console.aws.amazon.com/)
2. Navigate to **EC2** > **Instances** > **Launch Instances**
3. Configure:
   - **Name**: choose a name (e.g., `my-backend-server`)
   - **AMI**: select **Amazon Linux 2023 AMI** or **Ubuntu Server 24.04 LTS**
   - **Instance Type**: `t2.micro` (Free Tier eligible) is enough for testing
   - **Key Pair**: select your key pair
4. Under **Network Settings** > **Security Group**, add these inbound rules:
   - **SSH** (port 22) -- from `My IP` (recommended) or `Anywhere`
   - **HTTP** (port 80) -- from `Anywhere` (0.0.0.0/0); used only to 301-redirect to HTTPS
   - **HTTPS** (port 443) -- from `Anywhere` (0.0.0.0/0) so the frontend + API are accessible
5. Click **Launch Instance**

> Save your instance's **Public IPv4 address** -- you'll need it throughout this guide.

## Step 2: Connect to Your Instance

```bash
chmod 400 /path/to/your-key-pair.pem
ssh -i "/path/to/your-key-pair.pem" <USERNAME>@<EC2_PUBLIC_IP>
```

Replace:
- `/path/to/your-key-pair.pem` -- path to your downloaded key file
- `<USERNAME>` -- `ec2-user` for Amazon Linux, `ubuntu` for Ubuntu
- `<EC2_PUBLIC_IP>` -- your instance's public IP address

## Step 3: Install Docker and Docker Compose

Run one of the following depending on your AMI choice.

### Amazon Linux 2023

```bash
sudo dnf update -y
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker $USER
newgrp docker

# Docker Compose plugin
sudo dnf install -y docker-compose-plugin
```

### Ubuntu

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

# Add Docker's GPG key
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

# Add the Docker repository
echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Install Docker Engine + Compose
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo usermod -aG docker $USER
newgrp docker
```

### Verify Docker is working

```bash
docker --version
docker compose version
```

## Step 4: Clone the Repository

On the EC2 instance, clone your repository:

```bash
git clone https://github.com/<YOUR_USERNAME>/<YOUR_REPO>.git
cd <YOUR_REPO>
```

Replace `<YOUR_USERNAME>/<YOUR_REPO>` with your actual GitHub path.

> **Private repo?** You'll need to authenticate. Options:
> - [Personal Access Token (PAT)](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-personal-access-token) -- use as the password when cloning over HTTPS
> - [SSH keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh) -- generate a key pair on the instance and add the public key to your GitHub account

## Step 5: Provide secrets

The WG Hunter agent needs `OPENAI_API_KEY`, the Places Autocomplete widget in the frontend needs `VITE_GOOGLE_MAPS_API_KEY` (baked into the built SPA at build time), and `GOOGLE_MAPS_SERVER_KEY` enables backend geocoding fallback, commute routing, and nearby-place enrichment.

From the repo root, create `.env` (copy from [`.env.example`](./.env.example)) with at minimum:

```bash
OPENAI_API_KEY=sk-...
VITE_GOOGLE_MAPS_API_KEY=AIza...  # required for the onboarding map
GOOGLE_MAPS_SERVER_KEY=AIza...    # optional but required for commute / nearby-place scoring
```

The root [`docker-compose.yml`](./docker-compose.yml) already wires `.env` into the backend via `env_file:` and passes `VITE_GOOGLE_MAPS_API_KEY` as a build arg to the frontend image, so nothing else to edit.

### SSL certificate

The nginx container terminates TLS for `doubleu.team` (and `www.doubleu.team`) and needs two files mounted from the repo root:

| File | What it is |
|------|------------|
| `fullchain.crt` | Leaf certificate + intermediates concatenated (leaf first, then the Sectigo CA bundle). |
| `doubleu_team.key` | Private key that pairs with the certificate. |

Upload both files to the repo root on the EC2 host (e.g. `~/tumai-makeathon-2026/`) with permissions readable by the `docker` user. They are mounted read-only at `/etc/nginx/certs/` inside the frontend container by [`docker-compose.yml`](./docker-compose.yml) and are git-ignored (`*.crt`, `*.key`).

```bash
# from your local machine, assuming you already have the cert + key locally
scp -i /path/to/your-key-pair.pem fullchain.crt doubleu_team.key <USERNAME>@<EC2_PUBLIC_IP>:~/tumai-makeathon-2026/
```

If your private key file is named differently, either rename it to `doubleu_team.key` or update the volume mount in `docker-compose.yml` to match.

## Step 6: Run the Application

From the repo root:

```bash
docker compose up -d --build
```

- `-d` runs containers in the background (detached mode)
- `--build` rebuilds the images from the Dockerfiles

This starts two services:
- `frontend` -- nginx serving the built Vite SPA on port 443 (TLS) and reverse-proxying `/api/*` to the backend; port 80 only 301-redirects to HTTPS
- `backend` -- FastAPI / uvicorn on the internal compose network, with SQLite persisted to the `wg_data` named volume at `/root/.wg_hunter`

## Step 7: Verify the Deployment

From your local machine or browser:

```bash
curl https://doubleu.team/api/health
# Expected: {"status":"ok"}
```

Open the app:

```
https://doubleu.team/
```

Interactive API docs:

```
https://doubleu.team/docs
```

## Updating the Deployment

### Manual update

SSH into the instance and pull the latest changes:

```bash
cd ~/<YOUR_REPO>
git pull origin <YOUR_BRANCH>
docker compose up -d --build
```

### Automatic update (CI/CD)

Set up GitHub Actions to deploy on every push. See [CI-CONFIGURATION.md](./CI-CONFIGURATION.md) for the full guide.

Once configured, the workflow will SSH into your server and run the update commands automatically -- no manual steps needed.

## Email notifications (Amazon SES)

WG Hunter emails each user autonomously whenever a new wg-gesucht listing scores at/above `WG_NOTIFY_THRESHOLD` (default `0.9`). The per-user matcher runs inside the `backend` container on a loop — SSE to the browser is **not** required; notifications work even when the user is offline.

The sender is `noreply@doubleu.team` (overridable via `SES_FROM_EMAIL`). Before any email actually leaves AWS you need to do the following **once**, in the AWS Console, in region **`eu-central-1` (Frankfurt)**.

### 1. Verify the sender domain

Verifying the whole domain (instead of a single address) enables DKIM and lets you send from any `@doubleu.team` address later.

1. Open **[Simple Email Service → Identities](https://eu-central-1.console.aws.amazon.com/ses/home?region=eu-central-1#/identities)** and click **Create identity**.
2. Choose **Domain**, enter `doubleu.team`, leave "Use a custom MAIL FROM domain" unchecked for now.
3. Under **Advanced DKIM settings**, pick **Easy DKIM** with **RSA 2048-bit**. Click **Create identity**.
4. SES generates three `CNAME` records of the form `xxx._domainkey.doubleu.team` → `xxx.dkim.amazonses.com`. Add all three to the DNS provider that hosts `doubleu.team` (the same one serving the `A` record for your EC2 box). TTL 300s is fine.
5. Reload the SES Identities page every few minutes. You want:
   - **Identity status: Verified**
   - **DKIM configuration: Successful**

Until both flip to green, SES refuses every `SendEmail` call for this domain.

### 2. Request production access (exit the SES sandbox)

A brand-new SES account is in **sandbox mode**: it can only send to pre-verified recipient addresses and is capped at 200 messages/day. For real users you must request production access.

1. Open **[Account dashboard](https://eu-central-1.console.aws.amazon.com/ses/home?region=eu-central-1#/account)** and click **Request production access**.
2. Fill in:
   - **Mail type:** Transactional
   - **Website URL:** `https://doubleu.team`
   - **Use case description:** "Transactional WG-match alerts sent to users who explicitly save a notification email in their WG Hunter profile. Each user receives at most one email per new listing scoring above their configured threshold (default 90%). Volume: under 500/day. Expected bounce and complaint rates are low since recipients opt in at account creation."
   - **Additional contacts:** leave blank
3. Submit. AWS typically responds in a few hours (occasionally up to 24h).

**While you wait**, you can still test end-to-end: go to **Identities → Create identity → Email address**, verify *your own* inbox (click the link AWS emails you), and point the debug route at that address. In sandbox mode both the sender domain *and* the recipient must be verified identities.

### 3. Create an IAM user with SES send permission

Never use your AWS root account for this.

1. Open **[IAM → Users](https://us-east-1.console.aws.amazon.com/iam/home#/users)** and click **Create user**.
2. Name it `wg-hunter-ses`. **Uncheck** "Provide user access to the AWS Management Console". Click **Next**.
3. **Permissions options:** *Attach policies directly*. Search for and select **`AmazonSESFullAccess`**, or, for a tighter footprint, click **Create inline policy** and paste:

   ```json
   {
     "Version": "2012-10-17",
     "Statement": [{
       "Effect": "Allow",
       "Action": ["ses:SendEmail", "ses:SendRawEmail"],
       "Resource": "arn:aws:ses:eu-central-1:<ACCOUNT_ID>:identity/doubleu.team"
     }]
   }
   ```
   (replace `<ACCOUNT_ID>` with your 12-digit AWS account number — visible in the top-right menu).
4. Create the user, open it, go to **Security credentials → Create access key**, choose **Application running outside AWS**, and copy the `AKIA…` key ID and secret **now** — AWS won't show the secret again.

### 4. Paste credentials into `.env` on the EC2 box

SSH to the EC2 host and edit `~/tumai-makeathon-2026/.env`:

```
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=eu-central-1
SES_FROM_EMAIL=noreply@doubleu.team
WG_NOTIFY_THRESHOLD=0.9
WG_RESCAN_INTERVAL_MINUTES=3
```

Keep `.env` out of git (it already is via `.gitignore`). Both the `backend` and `scraper` services load it via `env_file: .env` in [`docker-compose.yml`](./docker-compose.yml), but only `backend` actually calls SES today.

### 5. Redeploy and smoke-test

```bash
# on the EC2 host
cd ~/tumai-makeathon-2026
docker compose up -d --build
```

(or just `git push origin main` and let the CI workflow do it for you).

Temporarily enable the debug route, then fire a test:

```bash
# add ENABLE_EMAIL_DEBUG=1 to .env, restart, then:
curl "https://doubleu.team/api/debug/send-test-email?to=you@example.com"
```

Check `docker compose logs -f backend` — you should see `Sent score-alert email to you@example.com (score=0.91, user=test-user)`. If you see `Failed to send score-alert email to …: MessageRejected: Email address is not verified`, the sender domain or recipient isn't verified yet (see step 1 / step 2). After verifying the real flow, set `ENABLE_EMAIL_DEBUG=0` and redeploy so the debug route is not exposed publicly.

From that point on, any saved user profile whose email is set will receive an HTML notification whenever their matcher pass scores a new listing ≥ 0.9. No frontend interaction required.

## Useful Commands

| Command | Description |
|---------|-------------|
| `docker compose up -d --build` | Build and start containers in the background |
| `docker compose down` | Stop and remove containers |
| `docker compose logs -f` | Follow container logs (Ctrl+C to exit) |
| `docker compose ps` | List running containers |
| `docker image prune -f` | Remove unused Docker images to free disk space |

## Troubleshooting

**Can't connect to the API?**
- Verify the EC2 Security Group allows inbound TCP on ports 80 and 443
- Check that Docker is running: `sudo systemctl status docker`
- Check container status: `docker compose ps`
- Check logs: `docker compose logs` (or `docker compose logs backend` / `docker compose logs frontend`)

**TLS / HTTPS errors?**
- Make sure `fullchain.crt` and `doubleu_team.key` exist in the repo root on the EC2 host and are readable by Docker
- `fullchain.crt` must be leaf-first (the leaf certificate, then the Sectigo intermediate chain); if the browser reports an incomplete chain, concatenate the bundle: `cat doubleu_team.crt doubleu_team.ca-bundle > fullchain.crt`
- Inspect the served chain from your laptop: `openssl s_client -connect doubleu.team:443 -servername doubleu.team -showcerts </dev/null`
- Check that DNS for `doubleu.team` points at the EC2 public IP (`dig +short doubleu.team`)

**Permission denied when running Docker?**
- Make sure you ran `sudo usermod -aG docker $USER` and then `newgrp docker` (or log out and back in)

**Disk space running low?**
- Clean up old images: `docker image prune -f`
- Remove all stopped containers and unused data: `docker system prune -f`
