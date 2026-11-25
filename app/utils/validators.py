"""Validation routines for URLs, emails, phone numbers, and platform constraints."""

import re
from urllib.parse import urlparse

import email_validator
import phonenumbers

from app.schemas import PlatformName

# Specific non-prospect domains to drop unconditionally (news, aggregators, platforms, forums, directories)
NON_PROSPECT_DOMAINS = {
    "reddit.com",
    "quora.com",
    "wikipedia.org",
    "medium.com",
    "substack.com",
    "github.com",
    "salesforce.com",
    "stripe.com",
    "amazon.com",
    "tripadvisor.com",
    "yelp.com",
    "glassdoor.com",
    "indeed.com",
    "bloomberg.com",
    "forbes.com",
    "nytimes.com",
    "cnn.com",
    "bbc.com",
    "theguardian.com",
    "reuters.com",
    "nesta.org.uk",
    "eventbrite.com",
    "meetup.com",
    "crunchbase.com",
    "pitchbook.com",
    "zoominfo.com",
    "apollo.io",
    "yellowpages.com",
    "fandom.com",
    "wikidata.org",
    "apple.com",
    "google.com",
    "microsoft.com",
    "profootballhof.com",
    "gocolumbialions.com",
    "theufl.com",
    "popwarner.com",
    "aueagles.com",
    "udcfirebirds.com",
    "beststartup.co.uk",
    "edic.com",
    "tdic.com",
    "cityam.com",
    "entrepreneurplus.co.uk",
}

# Subpaths that denote articles, feeds, posts, or career directories rather than a prospect entity
NON_PROSPECT_PATH_PATTERNS = [
    r"/(blog|blogs)/",
    r"/(news|press|press-release|press-releases)/",
    r"/(article|articles)/",
    r"/(career|careers|jobs|job)/",
    r"/(topic|topics|tag|tags|category|categories)/",
    r"/(event|events)/",
    r"/(research|report|reports|whitepaper)/",
    r"/(feed|posts|search|terms|privacy)/",
    r"/(manual|manuals|guide|guides)/",
    r"/(archive|archives)/",
    r"/(investor|investors)/",
    r"/\d+-(?:most|best|top)-",
]

# Country calling codes for sanity checking
COUNTRY_CALLING_CODES = {
    "USA": "1",
    "UNITED STATES": "1",
    "US": "1",
    "UK": "44",
    "UNITED KINGDOM": "44",
    "GB": "44",
    "INDIA": "91",
    "IN": "91",
    "UAE": "971",
    "UNITED ARAB EMIRATES": "971",
    "AE": "971",
}


def is_malformed_or_truncated_url(url: str) -> bool:
    """Check if URL is truncated (ends with '-'), malformed, or has invalid characters."""
    if not url or not url.strip():
        return True
    cleaned = url.strip()
    # Check invalid characters forbidden in normalized URLs
    if any(c in cleaned for c in (" ", "<", ">", '"', "'", "\\", "^", "`", "{", "}", "|")):
        return True
    try:
        parsed = urlparse(cleaned)
    except Exception:
        return True
    if not parsed.scheme or not parsed.netloc:
        return True
    if parsed.scheme.lower() not in ("http", "https"):
        return True

    # Check for truncated path or URL ending in '-' or '...' or '_'
    path = parsed.path
    if path.endswith("-") or path.endswith("...") or path.endswith("_"):
        return True
    if cleaned.rstrip("/").endswith("-") or cleaned.rstrip("/").endswith("..."):
        return True
    return False


def is_non_prospect_page(url: str, title: str = "", snippet: str = "") -> bool:
    """Check if the URL points to a non-prospect page (articles, PDFs, forums, aggregators, job boards)."""
    if not url or is_malformed_or_truncated_url(url):
        return True

    cleaned_url = url.strip().lower()
    parsed = urlparse(cleaned_url)
    hostname = parsed.hostname or ""

    # Drop PDFs and non-web documents
    if any(cleaned_url.endswith(ext) for ext in (".pdf", ".doc", ".docx", ".ppt", ".pptx", ".zip")):
        return True

    # Drop blacklisted domains (aggregators, forums, corporate giants, news, insurers, listicles)
    for domain in NON_PROSPECT_DOMAINS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            return True

    # Drop paths indicating blog posts, news articles, job postings, etc.
    path = parsed.path.lower()
    for pattern in NON_PROSPECT_PATH_PATTERNS:
        if re.search(pattern, path):
            return True

    # Drop dating, shopping, directory, manual, archives, or insurer signals in title or snippet
    combined_text = f"{title} {snippet} {url}".lower()
    non_prospect_keywords = (
        "wikipedia",
        "reddit",
        "quora",
        "glassdoor",
        "indeed",
        "archives",
        "owners manual",
        "website owners manual",
        "insurance company",
        "physicians dentists union",
        "teachers union",
        "academy of general dentistry",
        "101 most impressive",
        "investor profile",
    )
    if any(kw in combined_text for kw in non_prospect_keywords):
        return True

    return False


def validate_email_address(raw_email: str) -> str | None:
    """Validate an email address using email-validator and return the normalized email or None."""
    if not raw_email or "@" not in raw_email:
        return None
    raw_cleaned = raw_email.strip().lower()
    if raw_cleaned.startswith("mailto:"):
        raw_cleaned = raw_cleaned[len("mailto:") :].strip()

    disallowed_domains = {"example.com", "test.com", "domain.com", "email.com", "placeholder.com"}
    domain_part = raw_cleaned.split("@")[-1]
    if domain_part in disallowed_domains:
        return None

    try:
        valid = email_validator.validate_email(
            raw_cleaned,
            check_deliverability=False,
        )
        return valid.normalized
    except email_validator.EmailNotValidError:
        return None


def validate_phone_number(raw_phone: str, expected_country: str | None = None) -> str | None:
    """Validate a phone number using Google's libphonenumber and sanity-check country code."""
    if not raw_phone or len(raw_phone.strip()) < 7:
        return None
    cleaned = raw_phone.strip()
    if cleaned.startswith("tel:"):
        cleaned = cleaned[len("tel:") :].strip()

    default_region = None
    if expected_country:
        c_upper = expected_country.strip().upper()
        if c_upper in ("USA", "UNITED STATES", "US"):
            default_region = "US"
        elif c_upper in ("UK", "UNITED KINGDOM", "GB"):
            default_region = "GB"
        elif c_upper in ("INDIA", "IN"):
            default_region = "IN"
        elif c_upper in ("UAE", "UNITED ARAB EMIRATES", "AE"):
            default_region = "AE"

    try:
        parsed = phonenumbers.parse(cleaned, default_region)
        if phonenumbers.is_possible_number(parsed) and phonenumbers.is_valid_number(parsed):
            formatted = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
            # Sanity check against query country
            if expected_country:
                expected_code = COUNTRY_CALLING_CODES.get(expected_country.strip().upper())
                if expected_code and not formatted.startswith(f"+{expected_code}"):
                    return None
            return formatted
    except phonenumbers.NumberParseException:
        pass
    return None


def validate_whatsapp_link(raw_link: str, expected_country: str | None = None) -> str | None:
    """Validate WhatsApp link and sanity-check number against expected country."""
    if not raw_link:
        return None
    cleaned = raw_link.strip()

    digits = None
    wa_match = re.search(r"(?:https?://)?wa\.me/(\+?[0-9]{7,15})", cleaned, re.IGNORECASE)
    if wa_match:
        digits = re.sub(r"[^\d]", "", wa_match.group(1))
    else:
        api_match = re.search(
            r"(?:https?://)?api\.whatsapp\.com/send\?phone=(\+?[0-9]{7,15})", cleaned, re.IGNORECASE
        )
        if api_match:
            digits = re.sub(r"[^\d]", "", api_match.group(1))

    if not digits or not (7 <= len(digits) <= 15):
        return None

    # Sanity-check country code against query country
    if expected_country:
        expected_code = COUNTRY_CALLING_CODES.get(expected_country.strip().upper())
        if expected_code and not digits.startswith(expected_code):
            return None

    return f"https://wa.me/{digits}"


# Strict Profile-URL Validators per platform (Fix 1)
def validate_linkedin_url(url: str, target_type: str = "individual") -> str | None:
    """Strict LinkedIn profile validator: linkedin.com/in/<slug>. Rejects posts, pulse, etc."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not (hostname == "linkedin.com" or hostname.endswith(".linkedin.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    # Reject non-profile paths
    if any(s.lower() in ("posts", "pulse", "jobs", "feed", "search", "learning", "events") for s in segments):
        return None

    if segments[0].lower() in ("in", "pub") and len(segments) >= 2:
        slug = segments[1]
        return f"https://{hostname}/in/{slug}"

    if target_type == "business" and segments[0].lower() == "company" and len(segments) >= 2:
        slug = segments[1]
        return f"https://{hostname}/company/{slug}"

    return None


def validate_instagram_url(url: str) -> str | None:
    """Strict Instagram profile validator: instagram.com/<username> only. Rejects p, reel, reels, stories."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not (hostname == "instagram.com" or hostname.endswith(".instagram.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return None

    username = segments[0].lower()
    rejected_segments = {
        "p", "reel", "reels", "stories", "explore", "tags", "tv", "channel",
        "about", "legal", "terms", "developer", "accounts", "direct"
    }
    if username in rejected_segments or not re.match(r"^[a-zA-Z0-9_.-]+$", username):
        return None

    return f"https://www.instagram.com/{username}"


def validate_x_url(url: str) -> str | None:
    """Strict X / Twitter profile validator: x.com or twitter.com/<handle> only. Rejects status, lists, etc."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not any(hostname == h or hostname.endswith(f".{h}") for h in ("x.com", "twitter.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if len(segments) != 1:
        return None

    handle = segments[0].lower()
    rejected_segments = {
        "status", "i", "hashtag", "search", "lists", "explore", "home",
        "settings", "intent", "notifications", "messages", "tos", "privacy"
    }
    if handle in rejected_segments or not re.match(r"^[a-zA-Z0-9_]{1,25}$", handle):
        return None

    return f"https://x.com/{handle}"


def validate_facebook_url(url: str) -> str | None:
    """Strict Facebook page/profile validator. Rejects groups, posts, videos, watch, events, share."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not (hostname == "facebook.com" or hostname.endswith(".facebook.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    # Reject groups, videos, watch, events, posts, photos, share
    rejected_paths = {
        "groups", "posts", "videos", "watch", "events", "share", "photo", "photos",
        "story.php", "permalink.php", "login", "recover", "help", "pages"
    }
    if any(s.lower() in rejected_paths for s in segments):
        return None

    # Standard profile or page
    if len(segments) == 1:
        slug = segments[0]
        if re.match(r"^[a-zA-Z0-9_.-]+$", slug):
            return f"https://www.facebook.com/{slug}"

    if segments[0].lower() == "profile.php" and "id=" in parsed.query:
        return f"https://www.facebook.com/profile.php?{parsed.query}"

    return None


def validate_youtube_url(url: str) -> str | None:
    """Strict YouTube channel/creator validator: @handle, channel/<id>, c/<name>, user/<name>. Rejects watch/shorts."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not (hostname == "youtube.com" or hostname.endswith(".youtube.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    first = segments[0]
    if first.lower() in ("watch", "shorts", "playlist", "feed", "live", "results"):
        return None

    if first.startswith("@") and len(segments) == 1:
        return f"https://www.youtube.com/{first}"

    if first.lower() in ("channel", "c", "user") and len(segments) >= 2:
        return f"https://www.youtube.com/{first.lower()}/{segments[1]}"

    return None


def validate_threads_url(url: str) -> str | None:
    """Strict Threads profile validator: threads.net/@user only. Rejects post."""
    parsed = urlparse(url.strip())
    hostname = (parsed.hostname or "").lower()
    if not any(hostname == h or hostname.endswith(f".{h}") for h in ("threads.net", "threads.com")):
        return None

    path = parsed.path.strip("/")
    segments = [s for s in path.split("/") if s]
    if not segments:
        return None

    if any(s.lower() in ("post", "t") for s in segments):
        return None

    user_seg = segments[0]
    if user_seg.startswith("@") and len(segments) == 1:
        return f"https://www.threads.net/{user_seg}"

    return None


# Platform Label from URL Domain Only (Fix 2)
def derive_platform_from_url(
    url: str,
    target_type: str = "individual",
    title: str = "",
    snippet: str = "",
) -> tuple[PlatformName, str] | None:
    """Derive platform label purely from the URL's domain and validate strict profile format.

    - instagram.com -> Instagram
    - x.com / twitter.com -> X
    - linkedin.com -> LinkedIn
    - facebook.com -> Facebook
    - youtube.com -> YouTube
    - threads.net / threads.com -> Threads
    - wa.me -> WhatsApp
    - Personal / Business domain -> Google (only if passes Fix 3 non-prospect page filter)
    """
    if not url or not url.strip().startswith(("http://", "https://")):
        return None
    if is_malformed_or_truncated_url(url):
        return None

    cleaned_url = url.strip()
    parsed = urlparse(cleaned_url)
    hostname = (parsed.hostname or "").lower()

    # 1. LinkedIn
    if "linkedin.com" in hostname:
        valid = validate_linkedin_url(cleaned_url, target_type=target_type)
        return ("LinkedIn", valid) if valid else None

    # 2. Instagram
    if "instagram.com" in hostname:
        valid = validate_instagram_url(cleaned_url)
        return ("Instagram", valid) if valid else None

    # 3. X / Twitter
    if "twitter.com" in hostname or "x.com" in hostname:
        valid = validate_x_url(cleaned_url)
        return ("X", valid) if valid else None

    # 4. Facebook
    if "facebook.com" in hostname:
        valid = validate_facebook_url(cleaned_url)
        return ("Facebook", valid) if valid else None

    # 5. YouTube
    if "youtube.com" in hostname:
        valid = validate_youtube_url(cleaned_url)
        return ("YouTube", valid) if valid else None

    # 6. Threads
    if "threads.net" in hostname or "threads.com" in hostname:
        valid = validate_threads_url(cleaned_url)
        return ("Threads", valid) if valid else None

    # 7. WhatsApp link
    if "wa.me" in hostname or "api.whatsapp.com" in hostname:
        valid = validate_whatsapp_link(cleaned_url)
        return ("WhatsApp", valid) if valid else None

    # 8. Personal or Business Website -> labeled 'Google' ONLY if passing Fix 3 filter
    if is_non_prospect_page(cleaned_url, title=title, snippet=snippet):
        return None

    # Must be a shallow/clean homepage or about/contact page
    path = parsed.path.strip("/").lower()
    path_segments = [s for s in path.split("/") if s]
    if len(path_segments) > 2:
        return None

    clean_site_url = f"https://{parsed.netloc}{parsed.path}".rstrip("/")
    if not clean_site_url.startswith("https://"):
        clean_site_url = "https://" + clean_site_url.split("://")[-1]
    return ("Google", clean_site_url)


def validate_platform_url(
    platform: PlatformName,
    url: str,
    target_type: str = "individual",
) -> str | None:
    """Validate URL against specific platform constraint. Dropped if invalid."""
    derived = derive_platform_from_url(url, target_type=target_type)
    if derived and derived[0] == platform:
        return derived[1]
    return None
