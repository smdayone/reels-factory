"""
Multi-language configuration for reels-factory.

All user-visible text that appears IN the generated videos (overlay texts,
captions, CTAs) must be generated in the target language.
CLI/console messages remain in English — only video content is localised.

Supported languages: en · es · de · fr · it
"""

# Supported language codes → display name
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "es": "Español",
    "de": "Deutsch",
    "fr": "Français",
    "it": "Italiano",
}

# ── Generic CTA pool — 4 variants per language ────────────────────────────────
# Used when --cta-type generic (or no --cta-type flag): one is picked at random
# per video. Same call-to-action intent, culturally adapted phrasing.
GENERIC_CTAS: dict[str, list[str]] = {
    "en": [
        "Get yours \u2192 link in bio \u2b06\ufe0f",
        "Tap the link in bio to get yours \U0001f446",
        "Don\u2019t miss out \u2014 link in bio \U0001f517",
        "Follow for more + link in bio \U0001f4f2",
    ],
    "es": [
        "Cons\u00edguelo \u2192 link en bio \u2b06\ufe0f",
        "Toca el link en bio para conseguirlo \U0001f446",
        "No te lo pierdas \u2014 link en bio \U0001f517",
        "S\u00edguenos + link en bio \U0001f4f2",
    ],
    "de": [
        "Jetzt holen \u2192 Link in Bio \u2b06\ufe0f",
        "Tap den Link in der Bio \U0001f446",
        "Nicht verpassen \u2014 Link in Bio \U0001f517",
        "Folgen + Link in Bio \U0001f4f2",
    ],
    "fr": [
        "Obtiens le tien \u2192 lien en bio \u2b06\ufe0f",
        "Clique sur le lien en bio \U0001f446",
        "Ne rate pas \u00e7a \u2014 lien en bio \U0001f517",
        "Suis-nous + lien en bio \U0001f4f2",
    ],
    "it": [
        "Prendilo \u2192 link in bio \u2b06\ufe0f",
        "Clicca il link in bio \U0001f446",
        "Non perderlo \u2014 link in bio \U0001f517",
        "Seguici + link in bio \U0001f4f2",
    ],
}

# ── CTA comment-trigger template ──────────────────────────────────────────────
# Used when --cta-type trigger. Call .format(trigger=WORD) to get the full string.
CTA_TRIGGER_TEMPLATE: dict[str, str] = {
    "en": "Comment \u2018{trigger}\u2019 below \U0001f447",
    "es": "Comenta \u2018{trigger}\u2019 abajo \U0001f447",
    "de": "Kommentiere \u2018{trigger}\u2019 unten \U0001f447",
    "fr": "Commente \u2018{trigger}\u2019 ci-dessous \U0001f447",
    "it": "Commenta \u2018{trigger}\u2019 qui sotto \U0001f447",
}

# ── Script language instruction for the Claude prompt ─────────────────────────
# Inserted into SCRIPT_PROMPT at {language_instruction}. Tells Claude how to
# write the overlay texts and caption — cultural adaptation, not literal translation.
SCRIPT_LANGUAGE_INSTRUCTION: dict[str, str] = {
    "en": (
        "English — plain conversational English, write as you would text a friend"
    ),
    "es": (
        "Spanish (Espa\u00f1ol) — culturally adapted for Spanish-speaking audiences, "
        "not a literal translation. Use natural everyday Spanish as spoken across "
        "Latin America or Spain. Avoid anglicisms when a native word exists."
    ),
    "de": (
        "German (Deutsch) — culturally adapted for German-speaking audiences. "
        "Direct, concise tone. Natural everyday German. Avoid overly formal register."
    ),
    "fr": (
        "French (Fran\u00e7ais) — culturally adapted for French-speaking audiences. "
        "Warm and relatable tone. Natural everyday French. "
        "Avoid franglais — use native French expressions where possible."
    ),
    "it": (
        "Italian (Italiano) — culturally adapted for Italian-speaking audiences. "
        "Expressive and direct tone. Natural everyday Italian. "
        "Keep the same punchy energy as the English originals."
    ),
}
