from app.data import http


def test_rejected_values_are_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CACHE_DIR", tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return "error page"

    assert http.cached("k", 0, fetch, keep=lambda v: "category:" in v) == "error page"
    http.cached("k", 0, fetch, keep=lambda v: "category:" in v)
    assert len(calls) == 2 and not list(tmp_path.iterdir())


def test_good_values_are_cached_locally(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CACHE_DIR", tmp_path)
    calls = []

    def fetch():
        calls.append(1)
        return {"category": "Corn"}

    http.cached("k2", 0, fetch)
    assert http.cached("k2", 0, fetch) == {"category": "Corn"} and len(calls) == 1


def test_bucket_errors_fall_back_to_live_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "CACHE_DIR", tmp_path)

    class Broken:
        def get_blob(self, *_):
            raise RuntimeError("bucket down")

        def blob(self, *_):
            raise RuntimeError("bucket down")

    monkeypatch.setattr(http, "_gcs_bucket", lambda: Broken())
    assert http.cached("k3", 0, lambda: {"ok": True}) == {"ok": True}
