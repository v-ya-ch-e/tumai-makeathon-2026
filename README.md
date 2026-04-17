# CI/CD Template: FastAPI + Docker + GitHub Actions + AWS EC2

A ready-to-use template for deploying a Dockerized FastAPI backend to AWS EC2 with automated continuous deployment via GitHub Actions.

Push to your deploy branch and the app is live on your server within seconds.

## What's Included

```
.
├── backend/
│   ├── app/
│   │   └── main.py              # FastAPI application (your code goes here)
│   ├── Dockerfile               # Container image definition
│   ├── docker-compose.yml       # Compose configuration
│   ├── requirements.txt         # Python dependencies (FastAPI, Uvicorn, Pydantic)
│   └── README.md                # Backend-specific docs
├── .github/
│   └── workflows/
│       └── deploy.yml           # GitHub Actions CD pipeline
├── CI-CONFIGURATION.md          # How to set up the CI/CD pipeline
├── DEPLOYMENT.md                # How to set up and deploy to AWS EC2
├── requirements.txt             # Root-level Python deps (data analysis, optional)
└── .gitignore
```

## How It Works

1. You push code to your deploy branch on GitHub
2. GitHub Actions triggers the `deploy.yml` workflow
3. The workflow SSHs into your EC2 instance using `appleboy/ssh-action`
4. On the server, it pulls the latest code and rebuilds the Docker containers
5. Your updated app is live

## Quick Start

### 1. Use This Template

Clone or fork this repo, then replace the example FastAPI app in `backend/app/main.py` with your own code.

### 2. Run Locally

```bash
cd backend
docker compose up --build
```

The API will be available at `http://localhost:8000`. Interactive docs at `http://localhost:8000/docs`.

Alternatively, without Docker:

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 3. Deploy to EC2

Follow the step-by-step guide in [DEPLOYMENT.md](./DEPLOYMENT.md) to:
- Launch and configure an EC2 instance
- Install Docker on the server
- Clone and run the app

### 4. Set Up Continuous Deployment

Follow [CI-CONFIGURATION.md](./CI-CONFIGURATION.md) to:
- Add your EC2 credentials as GitHub Secrets (`EC2_HOST`, `EC2_USERNAME`, `EC2_SSH_KEY`)
- Configure the deploy workflow trigger branch

After setup, every push to your deploy branch auto-deploys to EC2.

## Customizing the Template

Before using this template for your own project, update these files:

| File | What to Change |
|------|---------------|
| `backend/app/main.py` | Replace example endpoints with your own API |
| `backend/requirements.txt` | Add your Python dependencies |
| `.github/workflows/deploy.yml` | Update the branch name, server path, and branch in `git reset` |
| `backend/docker-compose.yml` | Add environment variables, ports, or additional services |
| `backend/Dockerfile` | Modify if you need a different Python version or build steps |

### Key Values to Update in `deploy.yml`

```yaml
on:
  push:
    branches:
      - main              # <- your deploy branch

script: |
  cd ~/your-project-name  # <- path where you cloned the repo on EC2
  git fetch origin
  git reset --hard origin/main  # <- must match the branch above
```

## Example API

The template ships with a minimal FastAPI app to verify everything works:

- `GET /` -- health check, returns `{"Hello": "World!!!!"}`
- `GET /items/{item_id}?q=search` -- example read endpoint
- `PUT /items/{item_id}` -- example write endpoint (JSON body: `name`, `price`, `is_offer`)

## GitHub Secrets Required

| Secret | Description |
|--------|-------------|
| `EC2_HOST` | Public IP or DNS of your EC2 instance |
| `EC2_USERNAME` | SSH username (`ec2-user` for Amazon Linux, `ubuntu` for Ubuntu) |
| `EC2_SSH_KEY` | Full contents of your `.pem` private key file |

## Tech Stack

- **Python 3.11** with **FastAPI** + **Uvicorn**
- **Docker** + **Docker Compose** for containerization
- **GitHub Actions** for CI/CD
- **AWS EC2** as the deployment target
