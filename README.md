# Install dependencies

From the project root, install uv, Node.js, and FFmpeg using Homebrew, then install the project dependencies:

```sh
brew install uv node ffmpeg
uv sync --locked
npm --prefix frontend ci
```

# Start the backend

In a terminal at the project root:

```sh
uv run uvicorn backend.api:create_app --factory --host 127.0.0.1 --port 8000
```

# Start the worker

In a separate terminal at the project root:

```sh
uv run python -m backend.worker
```

# Start the frontend

In a separate terminal at the project root:

```sh
npm --prefix frontend run dev
```

Open http://127.0.0.1:5173/ in your browser.
