"""Supported output languages. Game capture and OCR remain English."""

LANGUAGES = {
    'nl': ('Nederlands', 'Dutch', 'nl-NL', 'nl-NL-MaartenNeural'),
    'en': ('Engels (origineel)', 'English', 'en-US', 'en-US-GuyNeural'),
    'de': ('Duits', 'German', 'de-DE', 'de-DE-ConradNeural'),
    'fr': ('Frans', 'French', 'fr-FR', 'fr-FR-HenriNeural'),
    'es': ('Spaans', 'Spanish', 'es-ES', 'es-ES-AlvaroNeural'),
    'it': ('Italiaans', 'Italian', 'it-IT', 'it-IT-DiegoNeural'),
    'pt': ('Portugees', 'Portuguese', 'pt-PT', 'pt-PT-DuarteNeural'),
}

SAMPLES = {
    'nl': 'De helden trekken door het oude woud. In de verte klinkt een hoorn. Het avontuur in Midden-aarde begint.',
    'en': 'The heroes travel through the ancient forest. A horn sounds in the distance. The adventure in Middle-earth begins.',
    'de': 'Die Helden ziehen durch den alten Wald. In der Ferne ertönt ein Horn. Das Abenteuer in Mittelerde beginnt.',
    'fr': 'Les héros traversent la forêt ancienne. Un cor retentit au loin. L’aventure en Terre du Milieu commence.',
    'es': 'Los héroes atraviesan el antiguo bosque. A lo lejos suena un cuerno. La aventura en la Tierra Media comienza.',
    'it': 'Gli eroi attraversano l’antica foresta. In lontananza risuona un corno. L’avventura nella Terra di Mezzo ha inizio.',
    'pt': 'Os heróis atravessam a floresta antiga. Ao longe soa uma trompa. A aventura na Terra Média começa.',
}

LEGACY_STYLE = 'Lees in natuurlijk Nederlands, als een warme, meeslepende fantasyverteller.'
DEFAULT_STYLE = 'Read naturally, as a warm, engaging fantasy storyteller.'


def language_code(value, fallback='nl'):
    return value if value in LANGUAGES else fallback


def speech_profile(provider, profile, language):
    """Adapt built-in regional voices without changing saved provider settings."""
    language = language_code(language)
    result = dict(profile, language=language)
    if provider in ('edge', 'azure'):
        voice = result.get('voice', '')
        if not voice.lower().startswith(language + '-'):
            result['voice'] = LANGUAGES[language][3]
    if result.get('style') == LEGACY_STYLE:
        result['style'] = DEFAULT_STYLE
    return result


def speech_language(overlay_language, selected):
    return overlay_language if selected == 'same' else language_code(selected)
