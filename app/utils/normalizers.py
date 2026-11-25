"""Normalization utilities for names, locations, and social handles."""

import re
import unicodedata
from urllib.parse import urlparse

# Common honorifics and noise words to strip during name normalization
NAME_PREFIXES = {"dr", "mr", "mrs", "ms", "prof", "coach", "captain", "cpt"}
NAME_POSTFIXES = {"phd", "md", "mba", "esq", "jr", "sr", "ii", "iii", "iv", "cfa", "cpa"}

# Mojibake patterns indicating character encoding corruption
MOJIBAKE_CHARS = {"Ã", "Ð", "Ù", "ð", "Ø", "§", "â", "€", "™", "\ufffd"}
MOJIBAKE_SUBSTRINGS = ("â€", "ð¤", "ð¬", "ð¡", "ðƒ", "ðš", "ð€", "ð  €", "ð  ¤")

# Sentence / post title starter phrases that indicate a headline or video instead of a person or business name
POST_STARTER_PHRASES = (
    "we had a",
    "hearing these",
    "what is a",
    "whats the",
    "what's the",
    "thinking about",
    "from the recent",
    "from the",
    "introducing",
    "a look at",
    "how to",
    "why you should",
    "top 10",
    "best place to",
    "and that was",
    "cv available",
    "valiant cricket present",
)

# Generic page-chrome titles to reject immediately
GENERIC_CHROME_NAMES = {
    "contact",
    "contact us",
    "about",
    "about us",
    "home",
    "homepage",
    "blog",
    "events",
    "find",
    "search",
    "faq",
    "terms",
    "privacy",
    "careers",
    "jobs",
    "overview",
    "login",
    "register",
    "menu",
    "gallery",
    "archives",
    "archive",
    "manual",
    "guide",
    "the cricket",
}

# Institutional and organizational words that disqualify a candidate unless the query asks for them
INSTITUTIONAL_ENTITY_WORDS = {
    "union",
    "association",
    "foundation",
    "insurance",
    "insurer",
    "academy",
    "society",
    "federation",
    "council",
    "institute",
}

# Generic directory, listing, event, or aggregator title patterns to drop
DIRECTORY_TITLE_PATTERNS = [
    r"^(?:find|search|top\s+\d+|best\s+\d+|list\s+of|directory\s+of|guide\s+to|manual\s+for)\b",
    r"\b(?:\d+\s+most|\d+\s+best|\d+\s+top)\b",
    r"\b(?:archives?|manuals?|directories|listings?)\b",
    r"\b(?:week|summit|festival|conference|meetup|scaleup)\b",
    r"\bcommunity\s+and\b",
    r"\b(?:startups?|businesses|companies|directories|listings?)\s+(?:of|in|around)\s+[a-zA-Z\s]+$",
    r"^(?:digital\s+marketing\s+agency|marketing\s+agency|creative\s+agency|advertising\s+agency|seo\s+agency|web\s+design\s+agency)$",
]

# Hotel, stadium, and venue patterns to reject unless explicitly sought
HOTEL_VENUE_PATTERNS = [
    r"\b(?:hyatt|marriott|hilton|radisson|sheraton|cricket\s+ground|stadium|arena)\b",
]


def has_mojibake(text: str) -> bool:
    """Detect if text contains mojibake encoding artifacts or corrupted multi-byte sequences."""
    if not text:
        return False
    # Check for presence of mojibake characters
    if any(c in text for c in MOJIBAKE_CHARS):
        return True
    if any(sub in text for sub in MOJIBAKE_SUBSTRINGS):
        return True
    # Check for unprintable control characters
    if any(unicodedata.category(c) in ("Cc", "Cs") and c not in ("\n", "\t", "\r") for c in text):
        return True

    # Reject names that are mostly non-letters or corrupted symbols
    non_space = [c for c in text if not c.isspace()]
    if non_space:
        real_letters = sum(
            1 for c in non_space if c.isalpha() and c not in ("ð", "Ð", "Ã", "Ù", "§", "Ø", "â", "€")
        )
        if (real_letters / len(non_space)) < 0.60:
            return True

    return False


def clean_unicode(text: str) -> str:
    """Apply Unicode NFC normalization and clean leading/trailing whitespace."""
    if not text:
        return ""
    normalized = unicodedata.normalize("NFC", text)
    return normalized.strip()


def is_valid_name(name: str, allowed_terms: set[str] | None = None) -> bool:
    """Validate that the extracted name is a realistic person or business name, not a sentence or post."""
    if not name or len(name) < 2:
        return False

    # Check for mojibake
    if has_mojibake(name):
        return False

    # Reject sentences (more than 6 words)
    words = name.split()
    if len(words) > 6:
        return False

    name_lower = name.lower()

    # Reject placeholder / uninitialized JS values like "undefined"
    if re.search(r"\bundefined\b", name_lower):
        return False

    # Reject generic page chrome names (Contact, About, Events, Archives, Manual, Guide, etc.)
    if name_lower in GENERIC_CHROME_NAMES:
        return False
    if any(re.search(r"\b" + re.escape(g) + r"\b", name_lower) for g in ("about us", "contact us", "owners manual", "website owners manual", "entrepreneur archives")):
        return False

    # Reject words like union/association/foundation/insurance/academy/society unless query asks for them
    tokens_lower = {w.lower().strip(".,:;!?'\"") for w in words}
    disallowed_institutions = INSTITUTIONAL_ENTITY_WORDS - (allowed_terms or set())
    if any(inst in tokens_lower for inst in disallowed_institutions):
        return False

    # Reject directories, listings, aggregator patterns (e.g. Find Startups In London, Startups Of London)
    if any(re.search(pat, name_lower) for pat in DIRECTORY_TITLE_PATTERNS):
        return False

    # Reject hotels, stadiums, venues (e.g. Hyatt Regency Ahmedabad, Cricket Ground In Ahmedabad)
    if any(re.search(pat, name_lower) for pat in HOTEL_VENUE_PATTERNS):
        return False

    # Reject if it starts with a post starter phrase
    if any(name_lower.startswith(phrase) for phrase in POST_STARTER_PHRASES):
        return False

    # Reject forbidden substrings (hashtags, URLs, phone numbers, wa.me, official site)
    forbidden_tokens = ["official site", "#", "wa.me", "http://", "https://", "www.", ".com", ".org", "instagram photos"]
    if any(token in name_lower for token in forbidden_tokens):
        return False

    # Reject if it looks like a phone number
    digits = re.sub(r"[^\d]", "", name)
    if len(digits) >= 7:
        return False

    # Reject single-word pure usernames that were lowercased and joined (e.g. dcdivasfootball)
    if len(words) == 1 and len(name) > 12 and name.islower():
        return False

    # Reject ALL-CAPS multi-word accounts (e.g. "INDIAN CRICKET LOVERS", "MENA RESTAURANT COMMUNITY")
    # Real person/business names may have one all-caps acronym word but not 3+ all-caps words
    all_caps_words = [w for w in words if len(w) > 2 and w.isupper() and w.isalpha()]
    if len(all_caps_words) >= 3:
        return False

    # Reject names containing community/fan-club/update suffixes that indicate generic accounts
    COMMUNITY_SUFFIXES = {
        "community", "update", "lovers", "fans", "fanclub", "nation",
        "army", "squad", "hub", "network", "world",
    }
    # Only reject if suffix is at the end and there's a topic word before it
    if len(words) >= 2 and words[-1].lower() in COMMUNITY_SUFFIXES:
        return False
    if len(words) >= 3 and words[-2].lower() in COMMUNITY_SUFFIXES:
        return False

    return True


def normalize_name(raw_name: str) -> str:
    """Normalize a person's or organization's name from title metadata."""
    if not raw_name:
        return ""

    cleaned = clean_unicode(raw_name)
    if has_mojibake(cleaned):
        return ""

    # 1. Clean platform suffixes
    cleaned = re.split(r"\s+[|\-–—•]\s+(?:LinkedIn|Instagram|Twitter|X|YouTube|Threads|Facebook)", cleaned, flags=re.IGNORECASE)[0].strip()

    # 2. General metadata separation: isolate the leading name when separated by dashes/pipes from roles, companies, or locations
    # (e.g. "Dr. Sarah Miller - Dentist - Toronto", "Eric Lituchy - Owner at Agency", "Naman Dhingra - App Name")
    meta_parts = re.split(r"\s+[|\-–—•]\s+", cleaned)
    if len(meta_parts) > 1:
        first_part = meta_parts[0].strip()
        first_words = first_part.split()
        # If the first segment is 1-6 words and does not look like an article starter, isolate it from trailing location/company metadata
        if 1 <= len(first_words) <= 6 and not any(first_part.lower().startswith(p) for p in POST_STARTER_PHRASES):
            cleaned = first_part

    # Instagram / X / Threads: "John Smith (@handle)" or "John Smith on Instagram"
    handle_match = re.search(r"^(.+?)\s*\(@[a-zA-Z0-9_.-]+\)", cleaned)
    if handle_match:
        cleaned = handle_match.group(1).strip()

    cleaned = re.split(r"\s+on\s+(?:Instagram|Twitter|X|Threads|Facebook)", cleaned, flags=re.IGNORECASE)[0].strip()

    # Remove quotes, brackets, emoji
    cleaned = re.sub(r"[\"\'`()\[\]{}]", "", cleaned)
    cleaned = re.sub(r"[^\w\s.,-]", "", cleaned, flags=re.UNICODE)

    words = [w.strip(".,") for w in cleaned.split() if w.strip(".,")]
    if not words:
        return ""

    # Remove leading honorifics
    if len(words) > 1 and words[0].lower() in NAME_PREFIXES:
        words = words[1:]

    # Remove trailing credentials
    if len(words) > 1 and words[-1].lower() in NAME_POSTFIXES:
        words = words[:-1]

    # Reconstruct name
    normalized = " ".join(words)
    if not is_valid_name(normalized):
        return ""

    # Capitalize words cleanly
    capitalized = " ".join(w.capitalize() if not w.isupper() else w for w in words)
    return capitalized


def extract_username_from_url(url: str) -> str | None:
    """Extract a platform handle or username from a profile URL."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        path = parsed.path.strip("/")
        if not path:
            return None

        segments = path.split("/")
        # YouTube / Threads @handle
        for seg in segments:
            if seg.startswith("@") and len(seg) > 1:
                return seg[1:].lower()

        # LinkedIn /in/username
        if "linkedin.com" in parsed.netloc and len(segments) >= 2 and segments[0].lower() in ("in", "company"):
            return segments[1].lower()

        # X, Instagram, Facebook /username
        candidate = segments[0].lower()
        if candidate not in {"channel", "c", "user", "in", "pub", "groups", "posts", "reel", "p"}:
            return candidate
        if len(segments) > 1:
            return segments[1].lower()
    except Exception:
        pass
    return None


def fold_accents(text: str) -> str:
    """Fold Unicode accents/diacritics to ASCII equivalents and lowercase."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def names_match(name_a: str, name_b: str) -> bool:
    """Token-based, accent-insensitive name matching allowing middle names and reordering.

    Rejects partial/substring matches (e.g. 'Lena Jungmann' vs 'Happy Lena Yoga' -> False).
    """
    if not name_a or not name_b:
        return False

    tokens_a = re.findall(r"[^\W_]+", fold_accents(name_a))
    tokens_b = re.findall(r"[^\W_]+", fold_accents(name_b))
    if not tokens_a or not tokens_b:
        return False

    set_a = set(tokens_a)
    set_b = set(tokens_b)

    # Single-token names must match another single-token name identically
    if len(set_a) == 1 or len(set_b) == 1:
        return set_a == set_b and len(next(iter(set_a))) >= 3

    shorter, longer = (set_a, set_b) if len(set_a) <= len(set_b) else (set_b, set_a)

    # Allow middle names and token reordering (e.g. 'John Smith' == 'John M. Smith' == 'Smith John')
    if shorter.issubset(longer):
        return True

    # Allow minor extra middle/suffix tokens when >= 2 tokens overlap and majority matches
    overlap = len(shorter & longer)
    return overlap >= 2 and (overlap / len(longer)) >= 0.65


def handles_share_fragment(handle_a: str | None, handle_b: str | None) -> bool:
    """Check whether two social handles/slugs share a meaningful identifier fragment."""
    if not handle_a or not handle_b:
        return False

    clean_a = re.sub(r"[^a-z0-9]", "", fold_accents(handle_a))
    clean_b = re.sub(r"[^a-z0-9]", "", fold_accents(handle_b))
    # Strip trailing numeric IDs (e.g. LinkedIn slug suffix -50566265)
    core_a = re.sub(r"\d{5,}$", "", clean_a)
    core_b = re.sub(r"\d{5,}$", "", clean_b)

    if len(core_a) < 4 or len(core_b) < 4:
        return False

    if core_a == core_b:
        return True

    if len(core_a) >= 5 and len(core_b) >= 5 and (core_a in core_b or core_b in core_a):
        return True

    return False


def calculate_string_similarity(s1: str, s2: str) -> float:
    """Calculate token-based Jaccard similarity between two strings."""
    tokens1 = set(re.findall(r"\w+", s1.lower()))
    tokens2 = set(re.findall(r"\w+", s2.lower()))
    if not tokens1 or not tokens2:
        return 0.0
    intersection = len(tokens1 & tokens2)
    union = len(tokens1 | tokens2)
    return intersection / union
