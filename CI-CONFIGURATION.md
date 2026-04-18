# CI/CD Configuration: GitHub Actions to AWS EC2

This guide explains how to set up a Continuous Deployment (CD) pipeline. Whenever you push code to the `main` branch on GitHub, this workflow will automatically update your EC2 instance.

## Prerequisites

1.  **EC2 Instance Running**: You should have completed the setup in [DEPLOYMENT.md](./DEPLOYMENT.md).
2.  **SSH Access**: You need the private key (`.pem` file) and the Public IP/DNS of your EC2 instance.

## Step 1: Configure GitHub Secrets

For security, we don't put credentials in the code. We use GitHub Secrets.

1.  Go to your GitHub Repository.
2.  Click **Settings** > **Secrets and variables** > **Actions**.
3.  Click **New repository secret** and add the following secrets:

| Name | Value |
| :--- | :--- |
| `EC2_HOST` | The Public IPv4 address or Public DNS of your EC2 instance (e.g., `ec2-xx-xx-xx-xx.compute-1.amazonaws.com`). |
| `EC2_USERNAME` | `ec2-user` (for Amazon Linux) or `ubuntu` (for Ubuntu). |
| `EC2_SSH_KEY` | The **entire content** of your `.pem` private key file. Open it with a text editor and copy everything, including `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----`. |

## Step 2: Create the Workflow File

The workflow is already committed at [`.github/workflows/deploy.yml`](./.github/workflows/deploy.yml) — if you forked this repo you already have it. For reference, the file passes the GitHub token through so it can deploy even if the repository is private:

```yaml
name: Deploy to EC2

on:
  push:
    branches:
      - main

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - name: Deploy to EC2
      uses: appleboy/ssh-action@v1.0.3
      env:
        GH_TOKEN: ${{ github.token }}
        REPO: ${{ github.repository }}
      with:
        host: ${{ secrets.EC2_HOST }}
        username: ${{ secrets.EC2_USERNAME }}
        key: ${{ secrets.EC2_SSH_KEY }}
        envs: GH_TOKEN,REPO
        script_stop: true
        script: |
          cd ~/tumai-makeathon-2026

          git remote set-url origin "https://x-access-token:${GH_TOKEN}@github.com/${REPO}.git"
          git fetch origin
          git reset --hard origin/main
          git remote set-url origin "https://github.com/${REPO}.git"

          cd backend

          docker compose down
          docker compose up -d --build

          docker image prune -f
```

Adjust `cd ~/tumai-makeathon-2026` if you cloned the repo into a different directory on the EC2 host.

## Step 3: Verify

1.  Push a change to the `main` branch.
2.  Go to the **Actions** tab in your GitHub repository.
3.  You should see the "Deploy to EC2" workflow running.
4.  Once it completes (green checkmark), your server should be updated.

> **Note on Branches**: This configuration deploys the `main` branch. To deploy a different branch, update both the `on: push: branches` list and the `git reset --hard origin/YOUR_BRANCH` command.
