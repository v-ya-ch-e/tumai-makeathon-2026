# Backend Service

This directory contains the FastAPI backend service.

## Setup Local Environment

1. Create a virtual environment:
   ```bash
   python3 -m venv venv
   ```
2. Activate the virtual environment:
   ```bash
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run the server:
   ```bash
   uvicorn app.main:app --reload
   ```

## Run with Docker

1. Build and start the container:
   ```bash
   docker-compose up --build
   ```

## Endpoints

- `GET /`: Health check
- `GET /items/{item_id}`: Example item endpoint
