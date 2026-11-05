from weavtui.cli import parse_args


def test_defaults(monkeypatch):
    for var in ("HOST", "PORT", "GRPC_PORT", "API_KEY", "START"):
        monkeypatch.delenv(f"WEAVTUI_{var}", raising=False)
    s, start = parse_args([])
    assert (s.host, s.port, s.grpc_port, s.api_key) == ("localhost", 8080, 50051, None)
    assert start == "diagram"


def test_env_and_args(monkeypatch):
    monkeypatch.setenv("WEAVTUI_PORT", "8180")
    monkeypatch.setenv("WEAVTUI_API_KEY", "secret")
    s, start = parse_args(["--grpc-port", "50151", "--start", "browse"])
    assert (s.port, s.grpc_port, s.api_key) == (8180, 50151, "secret")
    assert start == "browse"
