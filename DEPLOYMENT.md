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
   - **Custom TCP** (port 8000) -- from `Anywhere` (0.0.0.0/0) so the API is accessible
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

## Step 5: Run the Application

```bash
cd backend
docker compose up -d --build
```

- `-d` runs containers in the background (detached mode)
- `--build` rebuilds the image from the Dockerfile

## Step 6: Verify the Deployment

From your local machine or browser:

```bash
curl http://<EC2_PUBLIC_IP>:8000/
# Expected: {"Hello":"World!!!!"}
```

You can also visit the interactive API docs at:

```
http://<EC2_PUBLIC_IP>:8000/docs
```

## Updating the Deployment

### Manual update

SSH into the instance and pull the latest changes:

```bash
cd ~/<YOUR_REPO>
git pull origin <YOUR_BRANCH>
cd backend
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
- Verify the EC2 Security Group allows inbound TCP on port 8000
- Check that Docker is running: `sudo systemctl status docker`
- Check container status: `docker compose ps`
- Check logs: `docker compose logs`

**Permission denied when running Docker?**
- Make sure you ran `sudo usermod -aG docker $USER` and then `newgrp docker` (or log out and back in)

**Disk space running low?**
- Clean up old images: `docker image prune -f`
- Remove all stopped containers and unused data: `docker system prune -f`
