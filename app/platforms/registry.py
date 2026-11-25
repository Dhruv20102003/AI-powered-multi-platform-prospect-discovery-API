"""Platform registry managing all supported prospect discovery platform providers."""

from app.platforms.base import PlatformProvider
from app.platforms.facebook import FacebookPlatformProvider
from app.platforms.google import GooglePlatformProvider
from app.platforms.instagram import InstagramPlatformProvider
from app.platforms.linkedin import LinkedInPlatformProvider
from app.platforms.threads import ThreadsPlatformProvider
from app.platforms.whatsapp import WhatsAppPlatformProvider
from app.platforms.x_platform import XPlatformProvider
from app.platforms.youtube import YouTubePlatformProvider


def get_default_platform_providers() -> list[PlatformProvider]:
    """Instantiate and return the list of all registered platform providers.

    Supports the 9 platforms required:
    Google, LinkedIn, X, YouTube, Instagram, WhatsApp, Facebook, Email (via extraction), Threads.
    """
    return [
        GooglePlatformProvider(),
        LinkedInPlatformProvider(),
        XPlatformProvider(),
        YouTubePlatformProvider(),
        InstagramPlatformProvider(),
        WhatsAppPlatformProvider(),
        FacebookPlatformProvider(),
        ThreadsPlatformProvider(),
    ]
