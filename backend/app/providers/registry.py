from ..config import get_settings
from .base import Provider, ProviderCapability


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str, Provider] = {}

    def register(self, provider: Provider) -> None:
        if not provider.name:
            raise ValueError("Provider must define a name")
        self._providers[provider.name] = provider

    def clear(self) -> None:
        self._providers.clear()

    def get(self, name: str) -> Provider | None:
        return self._providers.get(name)

    def all(self) -> list[Provider]:
        return list(self._providers.values())

    def for_generator(self, generator: str) -> list[Provider]:
        return [p for p in self._providers.values() if generator in p.generators]

    def for_capability(self, cap: ProviderCapability) -> list[Provider]:
        return [p for p in self._providers.values() if p.capability == cap]


registry = ProviderRegistry()


def register_default_providers() -> None:
    """Called at startup. Add new providers here (video, avatar, ... in later phases)."""
    from .face import build_face_provider
    from .image import build_image_provider
    from .knowlez import KnowlezVoiceProvider
    from .pollinations import PollinationsImageProvider, PollinationsVideoProvider
    from .music import build_music_provider
    from .simulator import DevSimulatorProvider
    from .text import PromptRefinementProvider
    from .text_generation import TextGenerationProvider
    from .video import build_video_provider
    from .voice import build_voice_provider

    registry.clear()
    registry.register(PromptRefinementProvider())
    registry.register(TextGenerationProvider())     # story, script, lyrics
    registry.register(build_music_provider())
    registry.register(build_voice_provider())
    registry.register(KnowlezVoiceProvider())
    registry.register(build_video_provider())
    registry.register(PollinationsVideoProvider())
    registry.register(build_face_provider())
    registry.register(build_image_provider())
    registry.register(PollinationsImageProvider())
    if get_settings().enable_dev_simulator:
        registry.register(DevSimulatorProvider())


def prompt_refiner():
    from .text import PromptRefinementProvider
    p = next((p for p in registry.for_capability(ProviderCapability.TEXT) if isinstance(p, PromptRefinementProvider)), None)
    return p or PromptRefinementProvider()
