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

The WG Hunter agent needs `OPENAI_API_KEY`, the Places Autocomplete widget in the frontend needs `VITE_GOOGLE_MAPS_API_KEY` (baked into the built SPA at build time), and `GOOGLE_MAPS_SERVER_KEY` enables listing geocoding + Routes API commute times on the backend.

From the repo root, create `.env` (copy from [`.env.example`](./.env.example)) with at minimum:

```bash
OPENAI_API_KEY=sk-...
VITE_GOOGLE_MAPS_API_KEY=AIza...  # required for the onboarding map
GOOGLE_MAPS_SERVER_KEY=AIza...    # optional but required for commute scoring
```

The root [`docker-compose.yml`](./docker-compose.yml) already wires `.env` into the backend via `env_file:` and passes `VITE_GOOGLE_MAPS_API_KEY` as a build arg to the frontend image, so nothing else to edit.

### SSL certificate

The nginx container terminates TLS for `doubleu.team` (and `www.doubleu.team`) and needs two files mounted from the repo root:

| File | What it is |
|------|------------|
| `fullchain.crt` | Leaf certificate + intermediates concatenated (leaf first, then the Sectigo CA bundle). |
| `privkey.key` | Private key that pairs with the certificate. |

Upload both files to the repo root on the EC2 host (e.g. `~/tumai-makeathon-2026/`) with permissions readable by the `docker` user. They are mounted read-only at `/etc/nginx/certs/` inside the frontend container by [`docker-compose.yml`](./docker-compose.yml) and are git-ignored (`*.crt`, `*.key`).

```bash
# from your local machine, assuming you already have the cert + key locally
scp -i /path/to/your-key-pair.pem fullchain.crt privkey.key <USERNAME>@<EC2_PUBLIC_IP>:~/tumai-makeathon-2026/
```

If your private key file is named differently, either rename it to `privkey.key` or update the volume mount in `docker-compose.yml` to match.

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
- Make sure `fullchain.crt` and `privkey.key` exist in the repo root on the EC2 host and are readable by Docker
- `fullchain.crt` must be leaf-first (the leaf certificate, then the Sectigo intermediate chain); if the browser reports an incomplete chain, concatenate the bundle: `cat doubleu_team.crt doubleu_team.ca-bundle > fullchain.crt`
- Inspect the served chain from your laptop: `openssl s_client -connect doubleu.team:443 -servername doubleu.team -showcerts </dev/null`
- Check that DNS for `doubleu.team` points at the EC2 public IP (`dig +short doubleu.team`)

**Permission denied when running Docker?**
- Make sure you ran `sudo usermod -aG docker $USER` and then `newgrp docker` (or log out and back in)

**Disk space running low?**
- Clean up old images: `docker image prune -f`
- Remove all stopped containers and unused data: `docker system prune -f`
