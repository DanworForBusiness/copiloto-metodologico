"""Restore del arranque y recuperación BAJO DEMANDA de las imágenes de Cartelera (directorio `human-capital/cartelera/media`).
Archivo propio: no importa fixtures de otros módulos que simulan `remote_store`."""
from __future__ import annotations

import io

from webapp.services import cartelera_media  # noqa: F401 — registra el directorio bajo demanda

def test_boot_restore_skips_media_and_fetches_it_on_demand_without_reuploading(monkeypatch, tmp_path):
    """El restore del arranque NO baja el histórico multimedia (directorio bajo demanda); el resto de la Cartelera sí. Cada archivo se
    recupera al pedirlo, queda en disco y con su firma sembrada: mirror_up no lo vuelve a subir."""
    import config
    from core import remote_store as rs
    from tests.test_remote_store_write_through import FakeS3

    class S3(FakeS3):
        def get_object(self, Bucket, Key):
            if Key not in self.objects:
                raise type("NoSuchKey", (Exception,), {})()
            return {"Body": io.BytesIO(self.objects[Key])}
    fake, base = S3(), tmp_path / "app"
    base.mkdir()
    for k, v in (("BASE_DIR", base), ("PERSIST_ENABLED", True), ("SPACES_BUCKET", "bucket"), ("PERSIST_PATHS", ["human-capital"])):
        monkeypatch.setattr(config, k, v, raising=False)
    monkeypatch.setattr(rs, "_client", fake, raising=False)
    monkeypatch.setattr(rs, "_signatures", {}, raising=False)
    mid = "a" * 32
    fake.objects["human-capital/cartelera/editions/2026-11.json"] = b"{}"
    fake.objects[f"human-capital/cartelera/media/{mid}/display.webp"] = b"WEBP"
    rs.restore()
    assert (base / "human-capital/cartelera/editions/2026-11.json").exists()
    media_file = base / f"human-capital/cartelera/media/{mid}/display.webp"
    assert not media_file.exists()                                                         # no se bajó al arrancar
    assert rs.fetch_lazy(media_file) and media_file.read_bytes() == b"WEBP"                # bajo demanda
    assert rs._signatures[f"human-capital/cartelera/media/{mid}/display.webp"] == rs._sig(media_file)   # mirror_up no lo resube
    assert fake.uploads == [] and not rs.fetch_lazy(base / f"human-capital/cartelera/media/{'b' * 32}/display.webp")
