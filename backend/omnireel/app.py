"""Application container: one object wiring settings, storage and services."""

from __future__ import annotations

from dataclasses import dataclass, field

from .assembly import Assembler
from .capabilities import CapabilityRegistry
from .config import Settings
from .continuity import MediaTools
from .db import Database
from .providers import build_providers
from .providers.base import VideoProvider
from .quota import ProviderHealth, QuotaLedger
from .scheduler import Scheduler


@dataclass
class Application:
    settings: Settings
    db: Database
    registry: CapabilityRegistry
    providers: dict[str, VideoProvider]
    ledger: QuotaLedger
    health: ProviderHealth
    media: MediaTools
    scheduler: Scheduler
    assembler: Assembler
    worker: object = field(default=None)

    @classmethod
    def build(cls, settings: Settings) -> "Application":
        settings.ensure_dirs()
        db = Database(settings.db_path)
        db.init()
        registry = CapabilityRegistry.default(settings)
        providers = build_providers(settings)
        ledger = QuotaLedger(db, settings)
        health = ProviderHealth(db)
        media = MediaTools(settings)
        scheduler = Scheduler(db, settings, registry, providers, ledger, health, media)
        assembler = Assembler(db, settings, media)
        application = cls(
            settings=settings,
            db=db,
            registry=registry,
            providers=providers,
            ledger=ledger,
            health=health,
            media=media,
            scheduler=scheduler,
            assembler=assembler,
        )
        # Imported here to avoid a circular import at module load.
        from .worker import GenerationWorker

        application.worker = GenerationWorker(application)
        return application

    async def aclose(self) -> None:
        worker = self.worker
        if worker is not None and hasattr(worker, "stop"):
            await worker.stop()  # type: ignore[union-attr]
        for provider in self.providers.values():
            await provider.aclose()

    def configured_provider_ids(self) -> list[str]:
        return [pid for pid, provider in self.providers.items() if provider.configured]
