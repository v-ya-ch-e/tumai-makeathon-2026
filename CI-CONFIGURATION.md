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
 
1.  In your project root, create a directory named `.github/workflows` if it doesn't exist.
2.  Create a file named `deploy.yml` inside it (`.github/workflows/deploy.yml`).
3.  Paste the following content:

```yaml
name: Deploy to EC2

on:
  push:
    branches:
      - main  # Trigger on push to main branch

jobs:
  deploy:
    runs-on: ubuntu-latest

    steps:
    - name: Deploy to EC2
      uses: appleboy/ssh-action@v1.0.3
      with:
        host: ${{ secrets.EC2_HOST }}
        username: ${{ secrets.EC2_USERNAME }}
        key: ${{ secrets.EC2_SSH_KEY }}
        script: |
          # Navigate to the project directory
          # Ensure this path matches where you cloned the repo on the server
          cd ~/football-analytics-hackathon

          # Pull the latest changes from the main branch
          git fetch origin
          git reset --hard origin/main
          
          # Navigate to backend directory
          cd backend

          # Rebuild and restart containers
          docker compose down
          docker compose up -d --build
          
          # Clean up unused images to save space
          docker image prune -f
```

## Step 3: Verify

1.  Push a change to the `main` branch.
2.  Go to the **Actions** tab in your GitHub repository.
3.  You should see the "Deploy to EC2" workflow running.
4.  Once it completes (green checkmark), your server should be updated.

> **Note on Branches**: This configuration assumes you want to deploy the `main` branch. If you are working on `vyach` or another branch, update the `on: push: branches` section and the `git reset --hard origin/YOUR_BRANCH` command in the YAML file.
