# CODEZONE Cloud Deployment

This package contains only the files required for the Render cloud deployment.

## Repository root

- `backend/`
- `desktop/frontend/`
- `alembic.ini`
- `render.yaml`

## Render

Create a **Blueprint** from the repository and branch `main`.

The Blueprint creates:
- `codezone-api` — FastAPI backend
- `codezone-frontend` — static frontend
- `codezone-db` — PostgreSQL

Database migrations run automatically when the backend starts.

## API URL

The frontend currently points to:

`https://codezone-api.onrender.com`

If Render gives the API a different URL, edit:

`desktop/frontend/config.js`

## Custom domain later

- Frontend: `https://codezone.co.tz`
- API: `https://api.codezone.co.tz`

Update `CODEZONE_ALLOWED_ORIGINS` in `render.yaml` when the custom domain is connected.
