"""HTTP entry point for SourceCraft Repo Health."""

from fastapi import FastAPI


app = FastAPI(
    title="SourceCraft Repo Health",
    version="0.1.0",
    description="Repository health analysis for SourceCraft projects.",
)


@app.get("/health", tags=["system"])
def health() -> dict[str, str]:
    """Return process health for local development and deployment probes."""

    return {"status": "ok"}

