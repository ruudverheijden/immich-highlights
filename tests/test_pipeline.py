"""Tests for top-level pipeline context, config, and orchestration."""

from types import SimpleNamespace

import requests

from src import pipeline
from src.pipeline import PipelineConfig, PipelineContext, PipelineOptions


def test_run_album_generation_stage_passes_resolved_pipeline_inputs(monkeypatch):
    """The pipeline spine should pass config and runtime flags to album generation."""
    calls = []

    def fake_generate_albums(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return ["album-result"]

    monkeypatch.setattr(pipeline, "generate_albums", fake_generate_albums)
    monkeypatch.setattr(pipeline, "TEMP_DIR", "/tmp/test-scorer")
    monkeypatch.setattr(pipeline, "IMMICH_API_URL", "http://immich.local")
    context = PipelineContext(
        client="client",
        conn="conn",
        album_manager="album-manager",
    )
    config = PipelineConfig(
        album_rules=["rule"],
        content_filters=["filter"],
        scoring_config="scoring-config",
    )

    result = pipeline.run_album_generation_stage(
        context,
        config,
        PipelineOptions(force_rescore=True),
    )

    assert result == ["album-result"]
    assert calls == [
        {
            "args": (
                "client",
                "conn",
                "album-manager",
                ["rule"],
                "/tmp/test-scorer",
                "http://immich.local",
            ),
            "kwargs": {
                "content_filters": ["filter"],
                "scoring_config": "scoring-config",
                "force_rescore": True,
            },
        }
    ]


def test_run_pipeline_executes_spine_in_order(monkeypatch):
    """The top-level pipeline should make orchestration explicit and testable."""
    calls = []
    context = PipelineContext(
        client="client",
        conn="conn",
        album_manager="album-manager",
    )
    config = PipelineConfig(
        album_rules=[],
        content_filters=[],
        scoring_config=None,
    )

    def fake_create_context():
        calls.append("create_context")
        return context

    def fake_verify_permissions(client):
        calls.append(("verify_permissions", client))

    def fake_load_config():
        calls.append("load_config")
        return config

    def fake_run_album_generation_stage(stage_context, stage_config, options):
        calls.append(
            (
                "album_generation",
                stage_context,
                stage_config,
                options.force_rescore,
            )
        )
        return ["result"]

    monkeypatch.setattr(pipeline, "create_pipeline_context", fake_create_context)
    monkeypatch.setattr(pipeline, "verify_permissions", fake_verify_permissions)
    monkeypatch.setattr(pipeline, "load_pipeline_config", fake_load_config)
    monkeypatch.setattr(
        pipeline,
        "run_album_generation_stage",
        fake_run_album_generation_stage,
    )

    result = pipeline.run_pipeline(PipelineOptions(force_rescore=True))

    assert result == ["result"]
    assert calls == [
        "create_context",
        ("verify_permissions", "client"),
        "load_config",
        ("album_generation", context, config, True),
    ]


def test_run_pipeline_logs_request_errors_and_returns_none(monkeypatch):
    """API failures should be handled at the orchestration boundary."""
    monkeypatch.setattr(
        pipeline,
        "create_pipeline_context",
        lambda: PipelineContext(
            client=SimpleNamespace(),
            conn=SimpleNamespace(),
            album_manager=SimpleNamespace(),
        ),
    )
    monkeypatch.setattr(pipeline, "verify_permissions", lambda client: None)
    monkeypatch.setattr(
        pipeline,
        "load_pipeline_config",
        lambda: PipelineConfig([], [], None),
    )

    def fail_album_generation(context, config, options):
        raise requests.RequestException("offline")

    monkeypatch.setattr(
        pipeline,
        "run_album_generation_stage",
        fail_album_generation,
    )

    assert pipeline.run_pipeline() is None
