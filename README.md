# Prompt-to-Portfolio

Agentic API that turns a prompt and profile sources into a structured, deployable portfolio site.

The generation pipeline combines prompt interpretation, optional resume and GitHub data, component selection, layout assembly, and copy generation. Jobs run asynchronously and results are retrieved by polling.

## Project Description

Prompt-to-Portfolio is a backend service for portfolio-generation products. A client submits a design prompt plus optional GitHub, resume, LinkedIn, and self-description data. The service creates a background job, gathers and normalizes source data, produces validated portfolio content and layout, then exposes the final structured result through the job-status API.

The API does not render or host the final site. It produces the portfolio specification that a renderer or frontend can consume.

## Architecture

```mermaid
flowchart TD
	Client[Client] -->|POST /v1/generate| API[FastAPI]
	API --> Queue[Job queue]
	Queue --> Graph[LangGraph pipeline]
	API --> Redis[(Redis)]
	API --> Postgres[(Postgres)]

	Graph --> Resume[Resume parser MCP]
	Graph --> GitHub[GitHub profile fetch]
	Graph --> Prompt[Prompt interpreter]
	Resume --> Merge[Merge and normalize]
	GitHub --> Merge
	Prompt --> Merge
	Merge --> Components[Component selector]
	Components --> Media[Media mapper]
	Media --> Copy[Copy generation]
	Copy --> Validate[Layout assembly and validation]
	Validate -->|valid| Result[Final portfolio schema]
	Validate -->|retry, max 2| Copy
	Result --> Postgres
	Result --> Redis
	Client -->|GET /v1/jobs/{job_id}| API
```

### Components

- **FastAPI**: accepts generation requests, exposes job polling, and manages application lifecycle.
- **LangGraph pipeline**: orchestrates source collection, normalization, component and media selection, copy generation, validation, and retry handling.
- **MCP services**: expose the component catalog and resume-parsing capabilities to the pipeline.
- **Redis**: caches job state, applies rate limits, and checkpoints LangGraph state for recovery.
- **Postgres**: provides durable job and generated-output storage.
- **Schemas**: Pydantic contracts define request input, intermediate profiles, and final portfolio output.

### Generation Flow

1. The client creates a job with `POST /v1/generate`.
2. Resume parsing, GitHub fetching, and prompt interpretation run in parallel.
3. Source results merge into one normalized profile.
4. The pipeline selects components, maps media, generates copy, and assembles a layout.
5. Validation either completes the job, retries copy generation up to two times, or returns a fallback result.
6. The client polls `GET /v1/jobs/{job_id}` until the job is complete or failed.

## Requirements

- Python 3.11+
- Docker and Docker Compose
- OpenAI API key

## Local Setup

```bash
cp .env.example .env
```

Set at least `OPENAI_API_KEY` in `.env`, then install dependencies:

```bash
pip install -e ".[dev]"
```

Start Redis and Postgres:

```bash
docker compose up -d redis postgres
```

Run the API:

```bash
uvicorn api.main:app --reload --port 8000
```

Verify it is running:

```bash
curl http://localhost:8000/health
```

When `APP_ENV=development`, interactive API documentation is available at `http://localhost:8000/docs`.

## API

### Create a generation job

`POST /v1/generate` accepts `multipart/form-data` and returns `202 Accepted` with a job ID.

```bash
curl -X POST http://localhost:8000/v1/generate \
	-F "profession_type_mode=manual" \
	-F "profession_type_value=software_engineer" \
	-F "style_pattern=editorial" \
	-F "prompt=Create a portfolio focused on platform engineering." \
	-F "self_description=Backend engineer with distributed systems experience." \
	-F "github_connected=false"
```

Optional fields include `github_username`, `linkedin_text`, and `resume_file`. The response contains `job_id`, `status`, and a `poll_url`.

### Get job status

`GET /v1/jobs/{job_id}` returns the current job state. A completed job includes generated output; a failed job includes errors.

```bash
curl http://localhost:8000/v1/jobs/<job_id>
```

## Configuration

Use `.env.example` as the configuration reference. Key settings include:

- `OPENAI_API_KEY` and model names
- `REDIS_URL` and `DATABASE_URL`
- MCP server URLs
- Rate-limit and pipeline thresholds
- `APP_ENV` and `API_VERSION`

`.env` is intentionally ignored by Git.

## Tests

```bash
pytest
```

## Layout

```text
api/          FastAPI routes and request/response models
pipeline/     LangGraph workflow, nodes, and routing edges
schemas/      Pydantic domain models
infra/        Redis, Postgres, job queue, and migrations
mcp_servers/  Component-library and resume-parser MCP services
mcp_clients/  MCP service clients
eval/         Evaluation runner and golden-set fixtures
tests/        Unit, integration, and contract tests
```
